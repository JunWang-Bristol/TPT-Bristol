#!/usr/bin/env python3
"""TPT core-loss vs DC PRE-MAGNETISATION on TX26/15/10 3C90.

Why this cannot be a like-for-like MagNet comparison
----------------------------------------------------
MagNet's offline package cannot model DC bias:
  * `core_loss_iGSE_*(dc_bias=...)` returns the IDENTICAL loss at every bias -
    iGSE depends only on |dB/dt| and dB, and a DC offset changes neither;
  * the trained NN (`core_loss_ML_triangular`) takes no bias argument at all;
  * MagNet 2023 deliberately "fixed dc-bias as 0 to ensure the highest data
    quality" (challenge handbook).
So MagNet is used here as a ZERO-BIAS BASELINE, and the measurement quantifies
how far the real core departs from it as bias grows.  That gap is precisely
the modelling deficiency the MagNet Challenge exists to fix.

How TPT establishes the bias (Wang/Yuan/Rasekh IECON 2020, Fig. 10)
-------------------------------------------------------------------
The FIRST pulse is lengthened so its extra volt-seconds ramp the magnetising
current up to I_dc; the remaining symmetric half-periods then ring-fence a
closed minor loop around that operating point:
    T_stage1 = T_half + I_dc * L / V
Loss is taken from the LAST full cycle, as always.

Reported bias is the MEASURED mean current of that cycle (H0 = N1*I0/le),
not the requested value - the requested value is only a drive setting.
"""
import os
import sys
import time
import csv

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'tools'))
# Princeton MagNet reference model, optional.  Install with
#     pip install mag-net
# It ships the 3C90 iGSE parameters and a trained model, so the
# comparison needs no download.  Absent, this script still measures and
# simply omits the reference column.

import numpy as np

from tpt import CoreLossMeasurement, CORE_DATABASE, flux_to_voltage
from magnet_sweep import last_full_cycle, N1, N2, L_EST, DEADTIME

try:
    from magnet.core import core_loss_ML_triangular, core_loss_iGSE_triangular
    HAVE_MAGNET = True
except ImportError:
    HAVE_MAGNET = False

CORE       = "T26"
FREQ       = 50e3          # mid-band: the sweep's most reliable region
B_AC       = 0.100         # T, AC amplitude held constant across the bias sweep
PSU_ILIMIT = 2.0
N_PULSES   = 8
# Finer steps below the saturation knee.  At 100 mT AC this core starts to
# saturate around |I0| ~ 0.35-0.4 A: the ripple current jumps several-fold and
# the loss goes non-monotonic, so those points are not valid minor loops.
DC_BIASES  = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]      # A


def capture(m, f, i_range, pulses):
    board, scope = m.board, m.scope
    T_half = 1.0 / (2.0 * f)

    try:
        board.flush_buffer()
    except Exception:
        pass
    board.clear_pulses()
    for p in pulses:
        board.add_pulse(p)

    total = sum(pulses)
    dt = max(T_half / 50.0, 16e-9)
    n_samples = int(np.clip((total * 1.4) / dt, 1000, 8000))

    scope.set_probe_scale(m.CH_VOLTAGE,   m.input_voltage_probe_scale)
    scope.set_probe_scale(m.CH_SECONDARY, m.output_voltage_probe_scale)
    scope.set_probe_scale(m.CH_CURRENT,   m.current_probe_scale)
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
    return t, vp, vs, i, bool(np.abs(i).max() >= 0.98 * full)


def acquire(m, f, pulses, v_rail, i_dc_req, retries=3):
    """Widen the current range until the DC bias + AC ripple fits unclipped."""
    ranges = sorted(m.scope.get_input_voltage_ranges())
    T_half = 1.0 / (2.0 * f)
    i_ac = v_rail * T_half / L_EST
    need = (abs(i_dc_req) + i_ac) * 2.5 / m.current_probe_scale
    cand = [r for r in ranges if r >= need] or [ranges[-1]]
    expect_vpp = 2.0 * v_rail
    last = None
    for r in cand:
        for _ in range(retries):
            out = capture(m, f, r, pulses)
            if out is None:
                continue
            last = out
            if float(np.ptp(out[1])) < 0.5 * expect_vpp:
                continue
            if not out[4]:
                return r, out
            break
    return (cand[-1] if cand else None), last


def main():
    core = CORE_DATABASE[CORE]
    Ae, le = core["Ae"], core["le"]
    Ve = Ae * le
    T_half = 1.0 / (2.0 * FREQ)
    V = flux_to_voltage(B_AC, N1, Ae, FREQ, deadtime_s=DEADTIME)

    print("=" * 94)
    print(f"  TPT DC-bias sweep - {core['name']} 3C90   N1={N1} N2={N2}   Ve={Ve*1e6:.2f} cm^3")
    print(f"  {FREQ/1e3:.0f} kHz, AC flux target {B_AC*1e3:.0f} mT  ->  rails +-{V:.2f} V")
    print("  MagNet is a ZERO-BIAS baseline: iGSE is bias-blind and the NN takes no")
    print("  bias input, so any growth in the ratio below is model error, not noise.")
    print("=" * 94)

    m = CoreLossMeasurement.from_config(
        os.path.join(_TPT_ROOT, 'hardware_configuration.json'))

    rows = []
    try:
        m.psu.set_source_voltage(1, V)
        m.psu.set_source_voltage(2, V)
        m.psu.set_current_limit(1, PSU_ILIMIT)
        m.psu.set_current_limit(2, PSU_ILIMIT)
        m.psu.enable_output(1)
        m.psu.enable_output(2)
        time.sleep(0.5)

        for i_dc in DC_BIASES:
            T_stage1 = T_half + i_dc * L_EST / V
            pulses = [T_stage1] + [T_half] * (N_PULSES - 1)
            print(f"\n--- I_dc requested {i_dc*1e3:.0f} mA   "
                  f"T_stage1={T_stage1*1e6:.2f} us (vs T_half {T_half*1e6:.1f} us)")

            # Imbalance rejection is intermittent (a marginal capture, not a
            # property of the operating point), so take the best of a few
            # attempts rather than dropping the point outright.
            best = None
            clipped_all = True
            for _ in range(3):
                rng, out = acquire(m, FREQ, pulses, V, i_dc)
                if out is None:
                    continue
                t, vp, vs, i, clipped = out
                if clipped:
                    continue
                clipped_all = False
                i0, i1 = last_full_cycle(t, vp, FREQ)
                if i0 is None:
                    continue
                tc, vsc, ic, vpc = t[i0:i1], vs[i0:i1], i[i0:i1], vp[i0:i1]
                vs_net = np.trapezoid(vpc, tc)
                vs_abs = np.trapezoid(np.abs(vpc), tc)
                imbal = abs(vs_net) / vs_abs * 100 if vs_abs else float('inf')
                if best is None or imbal < best[0]:
                    best = (imbal, tc, vsc, ic, vpc)
                if imbal <= 6.0:
                    break

            if best is None:
                if clipped_all:
                    print(f"    REJECT: current clipping at "
                          f"+-{rng*m.current_probe_scale:.2f} A (core saturating)")
                else:
                    print("    REJECT: no closed cycle found")
                continue
            imbal, tc, vsc, ic, vpc = best
            if imbal > 6.0:
                print(f"    REJECT: volt-second imbalance {imbal:.1f}% (best of 3)")
                continue

            B = np.cumsum(vsc * np.gradient(tc)) / (N2 * Ae)
            B -= B.mean()
            B_pk = (B.max() - B.min()) / 2.0
            I0 = float(ic.mean())                 # measured DC bias
            H0 = N1 * I0 / le
            Q = abs((N1 / N2) * np.trapezoid(ic * vsc, tc))
            P = Q * FREQ
            Pv = P / Ve / 1e3

            i_pp = float(np.ptp(ic))
            # Saturation guard: once the core saturates, permeability collapses,
            # the ripple current balloons and the "loss" stops being a valid
            # minor-loop measurement - it even goes non-monotonic.
            saturated = bool(rows and i_pp > 3.0 * rows[0]['i_pp'])
            row = dict(i_dc_req=i_dc, I0=I0, H0=H0, B_pk=B_pk, Q=Q, Pv=Pv,
                       imbal=imbal, i_pp=i_pp, saturated=saturated)
            if HAVE_MAGNET:
                row['nn'] = core_loss_ML_triangular(
                    freq=FREQ, flux=B_pk, duty=0.5, material='3C90') / 1e3
                row['ratio'] = Pv / row['nn']
            rows.append(row)

            msg = (f"    I0={I0*1e3:+6.0f} mA  H0={H0:6.1f} A/m  B_ac={B_pk*1e3:5.1f} mT  "
                   f"Ipp={i_pp*1e3:5.0f} mA  imbal={imbal:.2f}%  Pv={Pv:7.1f} kW/m3")
            if saturated:
                msg += "  [SATURATING - excluded]"
            if HAVE_MAGNET:
                msg += f"  MagNet(0 bias)={row['nn']:.1f}  ratio={row['ratio']:.2f}x"
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

    print("\n" + "=" * 94)
    print(f"  DC-BIAS RESULT - {FREQ/1e3:.0f} kHz, AC flux held at ~{B_AC*1e3:.0f} mT")
    print("=" * 94)
    print(f"  {'I0 meas':>9} | {'H0':>9} | {'B_ac':>7} | {'Pv measured':>12} | "
          f"{'MagNet 0-bias':>13} | {'ratio':>6} | vs unbiased")
    print("  " + "-" * 90)
    pv0 = rows[0]['Pv']
    for r in rows:
        line = (f"  {r['I0']*1e3:+6.0f} mA | {r['H0']:6.1f} A/m | "
                f"{r['B_pk']*1e3:5.1f}mT | {r['Pv']:9.1f} kW/m3 | ")
        if HAVE_MAGNET:
            line += f"{r['nn']:10.1f} kW | {r['ratio']:5.2f}x | "
        line += f"{r['Pv']/pv0:.2f}x"
        if r.get('saturated'):
            line += "   <- saturating, excluded"
        print(line)

    clean = [r for r in rows if not r.get('saturated')]
    if HAVE_MAGNET and len(clean) > 1:
        r0, rN = clean[0], clean[-1]
        print(f"\n  Over the VALID (unsaturated) range:")
        print(f"    bias    {abs(r0['H0']):6.1f} -> {abs(rN['H0']):6.1f} A/m")
        print(f"    loss    {r0['Pv']:6.1f} -> {rN['Pv']:6.1f} kW/m3   ({rN['Pv']/r0['Pv']:.2f}x)")
        print(f"    MagNet  {r0['nn']:6.1f} -> {rN['nn']:6.1f} kW/m3   (predicts NO bias effect)")
        print(f"    ratio   {r0['ratio']:6.2f}x -> {rN['ratio']:5.2f}x")

    out = os.path.join(_TPT_ROOT, "magnet_dcbias_results.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["I_dc_requested_A", "I0_measured_A", "H0_A_per_m", "B_ac_T",
                    "Q_cycle_J", "Pv_kW_m3", "Pv_magnet_0bias_kW_m3", "ratio",
                    "vs_imbalance_pct", "I_pp_A"])
        for r in rows:
            w.writerow([r['i_dc_req'], r['I0'], r['H0'], r['B_pk'], r['Q'], r['Pv'],
                        r.get('nn', ''), r.get('ratio', ''), r['imbal'], r['i_pp']])
    print(f"\n  saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
