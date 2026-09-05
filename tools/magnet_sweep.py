#!/usr/bin/env python3
"""Direct TPT core-loss sweep vs Princeton MagNet 3C90 - no balancing loop.

Why not use CoreLossMeasurement.measure_core_loss()?
    Its volt-second balancing loop diverges on this rig: it trims the negative
    rail from a clipped current reading, drove V_neg to 31 V against a 23 V
    positive rail, saturated the core and railed the current probe.  Direct
    verification earlier showed the two rails are ALREADY matched to ~0.3 %
    volt-second imbalance when simply set to the same voltage, so the loop
    buys nothing here and can destroy a measurement.

This script instead:
    1. sets both rails to the same voltage,
    2. fires the standard 8 half-period TPT train,
    3. auto-ranges the current channel from a real capture (with headroom),
    4. extracts the LAST full cycle (the paper's "target cycle"),
    5. computes B, H, Q, P, Pv and the volt-second imbalance as a quality flag,
    6. compares against MagNet's iGSE model at the MEASURED flux.

Method: Wang, Yuan & Rasekh, IECON 2020.
    H = N1*I/le ;  B = (1/(N2*Ae)) integral(V_sec dt)
    Q = |(N1/N2) integral(I*V_sec dt)| over one closed cycle ; P = Q*f ; Pv = P/Ve
"""
import os
import sys
import time
import csv

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))
# Princeton MagNet reference model, optional.  Install with
#     pip install mag-net
# It ships the 3C90 iGSE parameters and a trained model, so the
# comparison needs no download.  Absent, this script still measures and
# simply omits the reference column.

import numpy as np

from tpt import CoreLossMeasurement, CORE_DATABASE, flux_to_voltage

try:
    from magnet.constants import materials, materials_extra, material_core_tested
    from magnet.core import core_loss_iGSE_triangular, core_loss_ML_triangular
    HAVE_MAGNET = True
except ImportError:
    HAVE_MAGNET = False


def magnet_reference(f, B):
    """MagNet's prediction for 3C90 at (f, B), triangular duty 0.5.

    Returns (nn, igse) in kW/m^3.  The neural model is trained directly on
    MagNet's measured 3C90 dataset and is the better reference; the iGSE fit
    is a 3-parameter smoothing of the same data and drifts at the ends of the
    frequency range, so it is reported only as a secondary check.
    """
    nn = core_loss_ML_triangular(freq=f, flux=B, duty=0.5, material='3C90') / 1e3
    ig = core_loss_iGSE_triangular(freq=f, flux=B, duty=0.5, material='3C90') / 1e3
    return nn, ig

CORE   = "T26"
N1, N2 = 10, 10
L_EST      = 404e-6
PSU_MAX_V  = 30.0
PSU_ILIMIT = 2.0          # A - protects the core/FETs if a point misbehaves
DEADTIME   = 500e-9
N_PULSES   = 8

POINTS = [
    ( 25e3, 0.100), ( 25e3, 0.200), ( 25e3, 0.300),
    ( 50e3, 0.100), ( 50e3, 0.200),
    (100e3, 0.050), (100e3, 0.100),
    (200e3, 0.025), (200e3, 0.050),
]


def capture(m, f, i_range):
    """Fire one TPT train and return (t, V_pri, V_sec, I, clipped)."""
    board, scope = m.board, m.scope
    T_half = 1.0 / (2.0 * f)

    try:
        board.flush_buffer()
    except Exception:
        pass
    board.clear_pulses()
    for _ in range(N_PULSES):
        board.add_pulse(T_half)

    # Derive dt from the half-period, NOT from a fixed sample count.  Fixing
    # n_samples and solving for dt drives the timebase to 7-8 ns at 200 kHz,
    # which the 2408B cannot sustain with three channels enabled - the capture
    # comes back as noise.  50 samples per half-period is ample for the
    # integrals, and the floor keeps the timebase realisable.
    total = N_PULSES * T_half
    dt = max(T_half / 50.0, 16e-9)
    n_samples = int(np.clip((total * 1.4) / dt, 1000, 8000))

    scope.set_probe_scale(m.CH_VOLTAGE,   m.input_voltage_probe_scale)
    scope.set_probe_scale(m.CH_SECONDARY, m.output_voltage_probe_scale)
    scope.set_probe_scale(m.CH_CURRENT,   m.current_probe_scale)
    # generous voltage ranges: the swing is rail-to-rail plus ringing overshoot
    scope.set_channel_configuration(m.CH_VOLTAGE,   5.0, 'DC', 0.0)
    scope.set_channel_configuration(m.CH_SECONDARY, 5.0, 'DC', 0.0)
    scope.set_channel_configuration(m.CH_CURRENT,   i_range, 'DC', 0.0)
    scope.set_channel_label(m.CH_VOLTAGE,   'V_pri')
    scope.set_channel_label(m.CH_SECONDARY, 'V_sec')
    scope.set_channel_label(m.CH_CURRENT,   'Current')
    scope.set_number_samples(n_samples)
    scope.set_sampling_time(dt)
    scope.set_number_pre_trigger_samples(200)
    scope.set_rising_trigger(m.CH_VOLTAGE, 0.2, timeout=3000)

    scope.start_single_acquisition()
    time.sleep(1.0)
    board.run_pulses(1)
    deadline = time.monotonic() + 10
    while scope.get_acquisition_state() != 'COMP':
        if time.monotonic() > deadline:
            return None
        time.sleep(0.1)

    df = scope.read_data([m.CH_VOLTAGE, m.CH_SECONDARY, m.CH_CURRENT])
    t = df['time'].to_numpy()
    vp = df['V_pri'].to_numpy()
    vs = df['V_sec'].to_numpy()
    i = df['Current'].to_numpy()
    full = i_range * m.current_probe_scale
    clipped = bool(np.abs(i).max() >= 0.98 * full)
    return t, vp, vs, i, clipped


def autorange_current(m, f, v_rail, expect_vpp, retries=3):
    """Widen the current range until nothing clips AND the train is present.

    Two independent failure modes are guarded here:
      * current clipping - widen the range and retry;
      * a capture that missed the burst entirely (V_pri flat).  That shows up
        as a tiny V_pri swing and silently produces nonsense downstream
        (volt-second imbalance of 80%+), so reject and retry instead.
    """
    ranges = sorted(m.scope.get_input_voltage_ranges())
    T_half = 1.0 / (2.0 * f)
    i_est = v_rail * T_half / L_EST
    start = i_est * 3.0 / m.current_probe_scale      # 3x headroom for DC offset
    cand = [r for r in ranges if r >= start] or [ranges[-1]]

    last = None
    for r in cand:
        for _ in range(retries):
            out = capture(m, f, r)
            if out is None:
                continue
            last = out
            vpp = float(np.ptp(out[1]))
            if vpp < 0.5 * expect_vpp:
                continue                              # missed the burst - retry
            if not out[4]:
                return r, out                         # good: no clip, train present
            break                                     # clipped -> widen range
    return (cand[-1] if cand else None), last


def last_full_cycle(t, v, f):
    """Index range of the final complete +/- cycle, bounded by REAL edges.

    Bounding the window with t[i0] + 1/f instead of the next measured edge
    leaves a partial cycle whenever the true period differs slightly from 1/f
    (deadtime, timing overhead).  The leftover fraction makes the net
    volt-second integral non-zero, which then looks like a badly unbalanced
    bridge - 10-25% "imbalance" on a rig that is really balanced to ~0.3%.
    Using consecutive rising edges makes the window a true closed cycle.
    """
    th = 0.3 * np.abs(v).max()
    s = np.where(v > th, 1, np.where(v < -th, -1, 0))
    starts = [k for k in range(1, len(s)) if s[k] == 1 and s[k - 1] <= 0]
    if len(starts) < 2:
        return None, None
    for j in range(len(starts) - 1, 0, -1):
        i0, i1 = starts[j - 1], starts[j]
        period = t[i1] - t[i0]
        if 0.7 / f <= period <= 1.3 / f and (i1 - i0) > 20:
            return i0, i1
    return None, None


def analyse(t, vp, vs, i, f, Ae, le, Ve):
    i0, i1 = last_full_cycle(t, vp, f)
    if i0 is None:
        return None
    tc, vsc, ic, vpc = t[i0:i1], vs[i0:i1], i[i0:i1], vp[i0:i1]
    dt = np.gradient(tc)

    B = np.cumsum(vsc * dt) / (N2 * Ae)
    B -= B.mean()
    H = N1 * ic / le
    Q = abs((N1 / N2) * np.trapezoid(ic * vsc, tc))
    P = Q * f
    Pv = P / Ve / 1e3
    B_peak = (B.max() - B.min()) / 2.0

    vs_net = np.trapezoid(vpc, tc)
    vs_abs = np.trapezoid(np.abs(vpc), tc)
    imbalance = abs(vs_net) / vs_abs * 100 if vs_abs > 0 else float('nan')

    return dict(B_peak=B_peak, H_peak=(H.max() - H.min()) / 2.0, Q=Q, P=P, Pv=Pv,
                imbalance=imbalance, i_pp=float(np.ptp(ic)),
                v_pp=float(np.ptp(vpc)), n=int(i1 - i0))


def main():
    core = CORE_DATABASE[CORE]
    Ae, le = core["Ae"], core["le"]
    Ve = Ae * le

    print("=" * 92)
    print(f"  TPT vs MagNet - {core['name']} 3C90   N1={N1} N2={N2}   Ve={Ve*1e6:.2f} cm^3")
    if HAVE_MAGNET:
        k_i, a, b = materials['3C90']
        _, f_min, f_max = materials_extra['3C90']
        print(f"  MagNet 3C90 iGSE k_i={k_i:.5g} alpha={a:.4f} beta={b:.4f}"
              f"   valid {f_min/1e3:.0f}-{f_max/1e3:.0f} kHz"
              f"   ref core {material_core_tested['3C90']}")
    print("=" * 92)

    m = CoreLossMeasurement.from_config(
        os.path.join(_TPT_ROOT, 'hardware_configuration.json'))
    print(f"  probes: V_pri x{m.input_voltage_probe_scale}  "
          f"V_sec x{m.output_voltage_probe_scale}  I x{m.current_probe_scale}")

    rows = []
    try:
        for f, B_t in POINTS:
            v_req = flux_to_voltage(B_t, N1, Ae, f, deadtime_s=DEADTIME)
            tag = f"{f/1e3:.0f} kHz @ {B_t*1e3:.0f} mT"
            if v_req > PSU_MAX_V:
                print(f"\n--- {tag}: SKIP, needs {v_req:.1f} V > {PSU_MAX_V:.0f} V")
                continue
            print(f"\n--- {tag}   V={v_req:.2f} V")

            m.psu.set_source_voltage(1, v_req)
            m.psu.set_source_voltage(2, v_req)
            m.psu.set_current_limit(1, PSU_ILIMIT)
            m.psu.set_current_limit(2, PSU_ILIMIT)
            m.psu.enable_output(1)
            m.psu.enable_output(2)
            time.sleep(0.5)

            expect_vpp = 2.0 * v_req          # rail-to-rail swing
            i_range, out = autorange_current(m, f, v_req, expect_vpp)
            if out is None:
                print("    capture failed (scope timeout)")
                continue
            t, vp, vs, i, clipped = out
            if clipped:
                print(f"    REJECT: current still clipping at +-{i_range*m.current_probe_scale:.2f} A")
                continue
            vpp = float(np.ptp(vp))
            if vpp < 0.5 * expect_vpp:
                print(f"    REJECT: capture missed the burst "
                      f"(V_pri {vpp:.1f} Vpp, expected ~{expect_vpp:.1f})")
                continue

            r = analyse(t, vp, vs, i, f, Ae, le, Ve)
            if r is None:
                print("    could not isolate a full cycle")
                continue
            if r['imbalance'] > 5.0:
                print(f"    REJECT: volt-second imbalance {r['imbalance']:.1f}% "
                      f"- not a closed loop")
                continue

            r.update(f=f, B_target=B_t, V=v_req, i_range=i_range)
            if HAVE_MAGNET:
                nn, ig = magnet_reference(f, r['B_peak'])
                r['pv_magnet'], r['pv_igse'] = nn, ig
                r['ratio'] = r['Pv'] / nn
                r['ratio_igse'] = r['Pv'] / ig
            rows.append(r)

            msg = (f"    V_pri {r['v_pp']:.1f} Vpp  I {r['i_pp']*1e3:.0f} mApp  "
                   f"imbalance {r['imbalance']:.2f}%  |  B={r['B_peak']*1e3:.1f} mT  "
                   f"Q={r['Q']*1e6:.2f} uJ  Pv={r['Pv']:.1f} kW/m3")
            if HAVE_MAGNET:
                msg += (f"  MagNet_NN={r['pv_magnet']:.1f} ratio={r['ratio']:.2f}x"
                        f"  (iGSE {r['pv_igse']:.1f}, {r['ratio_igse']:.2f}x)")
            print(msg)
    finally:
        try:
            m.psu.disable_output(1)
            m.psu.disable_output(2)
        except Exception:
            pass
        m.close()

    if not rows:
        print("\nNo usable points.")
        return 1

    print("\n" + "=" * 92)
    print("  RESULTS - compared at MEASURED flux (targeting error cannot distort this)")
    print("=" * 92)
    print(f"  {'freq':>7} | {'B tgt':>6} | {'B meas':>7} | {'imbal':>6} | "
          f"{'Pv measured':>12} | {'MagNet':>9} | ratio")
    print("  " + "-" * 88)
    for r in rows:
        line = (f"  {r['f']/1e3:5.0f} k | {r['B_target']*1e3:4.0f}mT | "
                f"{r['B_peak']*1e3:5.1f}mT | {r['imbalance']:5.2f}% | "
                f"{r['Pv']:9.1f} kW/m3 | ")
        line += f"{r['pv_magnet']:7.1f} | {r['ratio']:.2f}x" if HAVE_MAGNET else "-"
        print(line)

    if HAVE_MAGNET:
        ra = np.array([r['ratio'] for r in rows])
        print(f"\n  ratio  mean {ra.mean():.2f}x  median {np.median(ra):.2f}x  "
              f"min {ra.min():.2f}x  max {ra.max():.2f}x  spread {ra.std()/ra.mean()*100:.0f}%")

    out = os.path.join(_TPT_ROOT, "magnet_sweep_results.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frequency_Hz", "V_rail", "B_target_T", "B_measured_T",
                    "H_peak_A_m", "Q_cycle_J", "P_core_W", "Pv_kW_m3",
                    "Pv_magnet_NN_kW_m3", "ratio_NN", "Pv_magnet_iGSE_kW_m3",
                    "ratio_iGSE", "vs_imbalance_pct", "I_pp_A"])
        for r in rows:
            w.writerow([r['f'], r['V'], r['B_target'], r['B_peak'], r['H_peak'],
                        r['Q'], r['P'], r['Pv'], r.get('pv_magnet', ''),
                        r.get('ratio', ''), r.get('pv_igse', ''),
                        r.get('ratio_igse', ''), r['imbalance'], r['i_pp']])
    print(f"\n  saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
