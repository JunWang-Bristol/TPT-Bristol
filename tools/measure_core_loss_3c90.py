#!/usr/bin/env python3
"""TPT core-loss measurement on TX26/15/10 (3C90), compared with the
Ferroxcube 3C90 datasheet.

Core   : TX26/15/10, 3C90   (CORE_DATABASE key "T26")
         Ae = 52.3 mm^2, le = 63.5 mm, Ve = Ae*le = 3.32 cm^3
Winding: N1 = 10 (primary), N2 = 1 (flux sense)

Method (Wang, Yuan & Rasekh, IECON 2020):
    H(t) = N1*I(t)/le
    B(t) = 1/(N2*Ae) * integral(V_sec dt)
    Q    = |(N1/N2) * integral(I*V_sec dt)|   over the closed target cycle
    P    = Q * f ;  Pv = P / Ve

Datasheet reference points (Ferroxcube 3C90, 2004-09-01), ALL AT 100 C
and measured with SINUSOIDAL excitation:
    25 kHz , 200 mT  ->  Pv <= 80 kW/m^3
    100 kHz, 100 mT  ->  Pv <= 80 kW/m^3
    100 kHz, 200 mT  ->  Pv ~= 450 kW/m^3   (needs ~46 V, out of PSU range)

Two caveats when reading the comparison:
  * Temperature. Datasheet is 100 C; this rig runs at ambient (~25 C).
    3C90 loss has a minimum near 80-100 C, so an ambient measurement is
    expected to read HIGHER than the datasheet figure.
  * Waveform. Datasheet is sinusoidal; TPT is rectangular. Divergence here
    is the entire motivation for TPT -- see Section I of the paper.
"""
import os
import sys
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement, CORE_DATABASE, flux_to_voltage

CORE      = "T26"          # TX26/15/10
N1, N2    = 10, 10
L_ESTIMATE = 404e-6        # measured on this rig (positive, post probe-flip)

# (frequency Hz, target B_peak T, datasheet Pv kW/m^3 @100 C sinusoidal)
POINTS = [
    ( 25e3, 0.200,  80.0),
    (100e3, 0.100,  80.0),
]

PSU_MAX_V = 30.0


def rig_is_switching(m):
    """Capture V_pri during a burst and look for the square wave.

    Deliberately NOT based on PSU current: a burst switches for only ~80 us
    out of the ~1 s it takes to issue the SCPI commands, so the average
    current rises by only ~0.4 mA -- comparable to meter noise, which would
    false-abort on a perfectly healthy rig. The scope sees the edges directly.
    """
    T_half, n_pulses = 10e-6, 8
    board, scope = m.board, m.scope

    board.clear_pulses()
    for _ in range(n_pulses):
        board.add_pulse(T_half)

    m.psu.set_source_voltage(1, 10.0)
    m.psu.set_source_voltage(2, 10.0)
    m.psu.set_current_limit(1, 3.0)
    m.psu.set_current_limit(2, 3.0)
    m.psu.enable_output(1)
    m.psu.enable_output(2)
    time.sleep(0.6)

    scope.set_probe_scale(m.CH_VOLTAGE, m.input_voltage_probe_scale)
    scope.set_channel_configuration(m.CH_VOLTAGE, 20.0, 'DC', 0.0)
    scope.set_channel_label(m.CH_VOLTAGE, 'V_pri')
    scope.set_number_samples(5000)
    scope.set_sampling_time(100e-9)      # 500 us window: train + ringdown
    scope.set_number_pre_trigger_samples(300)
    # Trigger at ~20 % of the expected V_pri peak.  The threshold is passed to
    # the scope at BNC level, so divide by the probe scale.
    V_probe_scale = scope.probe_scale.get(m.CH_VOLTAGE, 1.0) or 1.0
    scope.set_rising_trigger(m.CH_VOLTAGE, 2.0 / V_probe_scale, timeout=3000)

    scope.start_single_acquisition()
    time.sleep(0.2)  # scope arms in ms; long delay lets noise trigger before pulses arrive
    board.run_pulses(1)

    deadline = time.monotonic() + 8
    while scope.get_acquisition_state() != 'COMP':
        if time.monotonic() > deadline:
            print("  scope never completed acquisition")
            return False
        time.sleep(0.1)

    v = scope.read_data([m.CH_VOLTAGE])['V_pri'].to_numpy()
    v_pp = float(v.max() - v.min())
    print(f"  V_pri swing during burst: {v_pp:.2f} V pp "
          f"({v.min():+.2f} .. {v.max():+.2f} V)")
    return v_pp > 5.0


def main():
    core = CORE_DATABASE[CORE]
    Ae, le = core["Ae"], core["le"]
    Ve = Ae * le

    print("=" * 74)
    print(f"  TPT core loss - {core['name']} 3C90,  N1={N1} N2={N2}")
    print(f"  Ae={Ae*1e6:.1f} mm^2  le={le*1e3:.1f} mm  Ve={Ve*1e6:.2f} cm^3")
    print("=" * 74)

    m = CoreLossMeasurement.from_config(
        os.path.join(_TPT_ROOT, 'hardware_configuration.json'))

    results = []
    try:
        print("\n[0] Checking the half-bridge is actually switching...")
        if not rig_is_switching(m):
            print("\n  ABORT: the half-bridge is not switching (V_pri never moves).")
            print("  Refusing to measure - any loss number from this would be noise.")
            print()
            print("  To split MCU faults from power-stage faults, probe the gate drive")
            print("  directly with a spare scope channel (C / index 2 is unused):")
            print("      TP4  = PWM_U  (high-side gate drive, from Nucleo PB10)")
            print("      TP5  = PWM_L  (low-side  gate drive, from Nucleo PB4)")
            print("      TP11 = ground reference")
            print("  A clean ~3.3 V logic swing at TP4/TP5 with a flat V_pri means the")
            print("  MCU and firmware are fine and the fault is downstream: isolated")
            print("  gate-driver supply (U1/U7, fuses F1/F2), drivers (U6/U8), or")
            print("  FETs (Q1/Q2).  Note a correctly-connected 12 V lead does NOT")
            print("  guarantee the isolated driver domain is actually powered.")
            return 1
        print("  OK - half-bridge is switching.\n")

        for freq, B_target, pv_datasheet in POINTS:
            v_needed = flux_to_voltage(B_target, N1, Ae, freq)
            print("=" * 74)
            print(f"  {freq/1e3:.0f} kHz @ {B_target*1e3:.0f} mT   "
                  f"(needs ~{v_needed:.1f} V)")
            print("=" * 74)
            if v_needed > PSU_MAX_V:
                print(f"  SKIP: needs {v_needed:.1f} V, above the {PSU_MAX_V:.0f} V limit.\n")
                continue

            r = m.measure_core_loss(
                voltage=v_needed, frequency=freq, N1=N1, N2=N2,
                core_name=CORE, L_henry=L_ESTIMATE,
                target_B_peak_T=B_target, plot=False,
                save_csv=os.path.join(_TPT_ROOT,
                                      f"coreloss_{freq/1e3:.0f}kHz_{B_target*1e3:.0f}mT.csv"),
            )
            if r is None:
                print("  FAILED to extract a closed loop at this point.\n")
                continue

            pv_meas = r["P_density"] / 1e3            # W/m^3 -> kW/m^3
            r.update(freq=freq, B_target=B_target,
                     pv_meas=pv_meas, pv_datasheet=pv_datasheet)
            results.append(r)
            print(f"\n  -> Q={r['Q_cycle']*1e6:.2f} uJ  P={r['P_core']*1e3:.1f} mW  "
                  f"B_peak={r['B_peak']*1e3:.0f} mT  Pv={pv_meas:.1f} kW/m^3\n")

        if results:
            print("=" * 74)
            print("  COMPARISON vs Ferroxcube 3C90 datasheet")
            print("=" * 74)
            print(f"  {'point':>16} | {'B_meas':>7} | {'Pv measured':>12} | "
                  f"{'Pv sheet':>9} | ratio")
            print("  " + "-" * 68)
            for r in results:
                print(f"  {r['freq']/1e3:6.0f} kHz {r['B_target']*1e3:4.0f} mT | "
                      f"{r['B_peak']*1e3:5.0f} mT | {r['pv_meas']:8.1f} kW/m3 | "
                      f"{r['pv_datasheet']:6.0f}    | {r['pv_meas']/r['pv_datasheet']:.2f}x")
            print("\n  Datasheet column is 100 C, sinusoidal. This rig is ambient (~25 C)")
            print("  and rectangular, so measured > datasheet is the expected direction.")
    finally:
        try:
            m.psu.disable_output(1)
            m.psu.disable_output(2)
        except Exception:
            pass
        m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
