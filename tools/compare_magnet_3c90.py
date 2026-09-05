#!/usr/bin/env python3
"""TPT core-loss sweep on TX26/15/10 (3C90) compared against Princeton MagNet.

Reference
---------
Princeton MagNet ships iGSE parameters fitted to its own measured 3C90 dataset
(`magnet.constants.materials['3C90']`).  MagNet's 3C90 data covers
25 kHz - 200 kHz and 10 - 300 mT, measured on a TX-25-15-10 toroid (ours is
TX26/15/10 - same family).  Our TPT drive is a symmetric rectangular voltage,
so B(t) is TRIANGULAR at duty 0.5, which is exactly what MagNet's
`core_loss_iGSE_triangular(duty=0.5)` models.  That makes this an
apples-to-apples comparison - unlike the Ferroxcube datasheet, which is
sinusoidal and specified at 100 C.

Each point is compared at its MEASURED B_peak (not the requested target), so
flux-targeting error cannot distort the comparison.

Winding: N1 = 10, N2 = 10 (verified: |V_sec|/|V_pri| = 0.97-1.00).
Probes  : x10 voltage probes on V_pri and V_sec (probe scale 10 in config).

Usage:  python tools/compare_magnet_3c90.py [--quick]
"""
import os
import sys
import time

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
    from magnet.core import core_loss_iGSE_triangular
    HAVE_MAGNET = True
except ImportError:
    HAVE_MAGNET = False

CORE   = "T26"
N1, N2 = 10, 10
L_ESTIMATE = 404e-6
PSU_MAX_V  = 30.0
DEADTIME   = 500e-9

# (frequency Hz, target B_peak T) - chosen to stay inside MagNet's 3C90 range
# (25-200 kHz, 10-300 mT) AND inside the PSU's 30 V ceiling.
POINTS = [
    ( 25e3, 0.100), ( 25e3, 0.200), ( 25e3, 0.300),
    ( 50e3, 0.100), ( 50e3, 0.200),
    (100e3, 0.050), (100e3, 0.100),
    (200e3, 0.025), (200e3, 0.050),
]
QUICK = [(25e3, 0.200), (100e3, 0.100)]


def required_voltage(B, f, Ae):
    return flux_to_voltage(B, N1, Ae, f, deadtime_s=DEADTIME)


def rig_is_switching(m):
    """Scope-based check: PSU current is too insensitive for short bursts."""
    board, scope = m.board, m.scope
    try:
        board.flush_buffer()
    except Exception:
        pass
    board.clear_pulses()
    for _ in range(8):
        board.add_pulse(10e-6)
    m.psu.set_source_voltage(1, 10.0)
    m.psu.set_source_voltage(2, 10.0)
    m.psu.set_current_limit(1, 3.0)
    m.psu.set_current_limit(2, 3.0)
    m.psu.enable_output(1)
    m.psu.enable_output(2)
    time.sleep(0.6)

    scope.set_probe_scale(m.CH_VOLTAGE, m.input_voltage_probe_scale)
    scope.set_channel_configuration(m.CH_VOLTAGE, 5.0, 'DC', 0.0)
    scope.set_channel_label(m.CH_VOLTAGE, 'V_pri')
    scope.set_number_samples(5000)
    scope.set_sampling_time(100e-9)
    scope.set_number_pre_trigger_samples(300)
    scope.set_rising_trigger(m.CH_VOLTAGE, 0.2, timeout=3000)
    scope.start_single_acquisition()
    time.sleep(1.0)
    board.run_pulses(1)
    deadline = time.monotonic() + 10
    while scope.get_acquisition_state() != 'COMP':
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    v = scope.read_data([m.CH_VOLTAGE])['V_pri'].to_numpy()
    pp = float(v.max() - v.min())
    print(f"  V_pri swing: {pp:.2f} V pp")
    return pp > 5.0


def main():
    quick = "--quick" in sys.argv
    pts = QUICK if quick else POINTS

    core = CORE_DATABASE[CORE]
    Ae, le = core["Ae"], core["le"]
    Ve = Ae * le

    print("=" * 88)
    print(f"  TPT vs MagNet - {core['name']} 3C90   N1={N1} N2={N2}   Ve={Ve*1e6:.2f} cm^3")
    if HAVE_MAGNET:
        k_i, alpha, beta = materials['3C90']
        _, f_min, f_max = materials_extra['3C90']
        print(f"  MagNet 3C90 iGSE: k_i={k_i:.6g} alpha={alpha:.4f} beta={beta:.4f}"
              f"   valid {f_min/1e3:.0f}-{f_max/1e3:.0f} kHz")
        print(f"  MagNet reference core: {material_core_tested['3C90']}")
    else:
        print("  WARNING: magnet package unavailable - measuring only, no comparison")
    print("=" * 88)

    m = CoreLossMeasurement.from_config(
        os.path.join(_TPT_ROOT, 'hardware_configuration.json'))
    print(f"  probe scales: V_pri x{m.input_voltage_probe_scale}  "
          f"V_sec x{m.output_voltage_probe_scale}  I x{m.current_probe_scale}")

    results = []
    try:
        print("\n[0] Verifying the half-bridge is switching...")
        if not rig_is_switching(m):
            print("  ABORT: V_pri never moves - the power stage is not switching.")
            print("  Probe TP4 (PWM_U) / TP5 (PWM_L) vs TP11 to split MCU from power stage.")
            return 1
        print("  OK\n")

        for f, B_t in pts:
            v_req = required_voltage(B_t, f, Ae)
            head = f"{f/1e3:.0f} kHz @ {B_t*1e3:.0f} mT"
            print("=" * 88)
            print(f"  {head}   (needs {v_req:.1f} V)")
            if v_req > PSU_MAX_V:
                print(f"  SKIP - exceeds {PSU_MAX_V:.0f} V PSU ceiling\n")
                continue
            print("=" * 88)

            r = m.measure_core_loss(
                voltage=v_req, frequency=f, N1=N1, N2=N2, core_name=CORE,
                L_henry=L_ESTIMATE, plot=False, balance=True,
                save_csv=os.path.join(_TPT_ROOT,
                                      f"magnet_{f/1e3:.0f}kHz_{B_t*1e3:.0f}mT.csv"))
            if r is None:
                print("  FAILED - no closed loop extracted\n")
                continue

            B_meas = r["B_peak"]
            pv_meas = r["P_density"] / 1e3
            row = dict(f=f, B_target=B_t, B_meas=B_meas, pv_meas=pv_meas,
                       Q=r["Q_cycle"], P=r["P_core"])
            if HAVE_MAGNET:
                # compare AT THE MEASURED FLUX, so targeting error doesn't matter
                row["pv_magnet"] = core_loss_iGSE_triangular(
                    freq=f, flux=B_meas, duty=0.5, material='3C90') / 1e3
            results.append(row)
            msg = (f"\n  -> B_meas={B_meas*1e3:.1f} mT  Q={r['Q_cycle']*1e6:.2f} uJ  "
                   f"Pv={pv_meas:.1f} kW/m3")
            if HAVE_MAGNET:
                msg += f"   MagNet={row['pv_magnet']:.1f} kW/m3  ratio={pv_meas/row['pv_magnet']:.2f}x"
            print(msg + "\n")

    finally:
        try:
            m.psu.disable_output(1)
            m.psu.disable_output(2)
        except Exception:
            pass
        m.close()

    if not results:
        print("No successful points.")
        return 1

    print("=" * 88)
    print("  TPT MEASURED vs MagNet iGSE (triangular, duty 0.5) - compared at measured B")
    print("=" * 88)
    print(f"  {'freq':>8} | {'B target':>8} | {'B meas':>8} | {'Pv measured':>12} | "
          f"{'MagNet':>10} | ratio")
    print("  " + "-" * 84)
    ratios = []
    for r in results:
        line = (f"  {r['f']/1e3:6.0f} k | {r['B_target']*1e3:5.0f} mT | "
                f"{r['B_meas']*1e3:5.1f} mT | {r['pv_meas']:9.1f} kW/m3 | ")
        if "pv_magnet" in r:
            ratio = r["pv_meas"] / r["pv_magnet"]
            ratios.append(ratio)
            line += f"{r['pv_magnet']:7.1f} kW | {ratio:.2f}x"
        print(line)

    if ratios:
        ratios = np.array(ratios)
        print()
        print(f"  ratio: mean {ratios.mean():.2f}x   median {np.median(ratios):.2f}x   "
              f"min {ratios.min():.2f}x   max {ratios.max():.2f}x   "
              f"spread {ratios.std()/ratios.mean()*100:.0f}%")
        print()
        print("  A consistent ratio across points = one systematic offset (calibration,")
        print("  temperature, core tolerance).  A ratio that drifts with f or B means a")
        print("  frequency- or flux-dependent error instead.")

    out = os.path.join(_TPT_ROOT, "magnet_comparison.csv")
    import csv
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frequency_Hz", "B_target_T", "B_measured_T",
                    "Pv_measured_kW_m3", "Pv_magnet_kW_m3", "ratio",
                    "Q_cycle_J", "P_core_W"])
        for r in results:
            pm = r.get("pv_magnet", "")
            w.writerow([r["f"], r["B_target"], r["B_meas"], r["pv_meas"], pm,
                        (r["pv_meas"] / pm) if pm else "", r["Q"], r["P"]])
    print(f"\n  saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
