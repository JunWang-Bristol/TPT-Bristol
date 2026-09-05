"""Full TPT core-loss measurement with auto-range and volt-second balancing.

Pipeline:
  1) Configure scope (initial coarse ranges).
  2) Auto-range current channel: capture, detect clipping, widen if clipped,
     tighten if signal far below range. Iterate.
  3) Volt-second balance: capture, find target cycle, measure delta_I (drift
     across the cycle). Adjust PSU CH2 (V-) to minimise drift. Iterate until
     |delta_I| / I_peak < tolerance.
  4) Final capture, compute B(t), H(t), Q, P_core, BH loop. Plot.

Probes (user setup):
  CH A V_pri TP16 (GND TP17), CH B V_sec TP18 (GND TP19), CH C PWM TP4, CH D current.
"""
import os, sys, time
_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from tpt import CoreLossMeasurement, theoretical_inductance, CORE_DATABASE


CORE      = "T26"
MATERIAL  = "3C90"
N1        = 10
N2        = 10
FREQ      = 50_000
VOLTAGE   = 5.0
DC_BIAS_A = 0.0
N_HALF    = 8
SAMPLES   = 2500
SAMPLE_DT = 100e-9
CH_PWM    = 2

BAL_TOL      = 0.05    # |delta_I| / I_peak  -> 5 %
BAL_MAX_ITER = 8


def find_pwm_edges(t_us, pwm, vthresh=1.0):
    above = pwm > vthresh
    rising  = np.where((~above[:-1]) &  above[1:])[0] + 1
    falling = np.where( above[:-1] & (~above[1:]))[0] + 1
    return rising, falling


def is_clipped(arr, range_v, threshold=0.97):
    """Detect ADC clipping. arr is in displayed-volts (post-probe-scale)."""
    return np.abs(arr).max() >= range_v * threshold


def fire_and_capture(meas, pulses_s, ch_pwm=CH_PWM):
    """Arm scope, fire one burst, return DataFrame."""
    s = meas.scope
    meas.board.clear_pulses()
    for T in pulses_s:
        meas.board.add_pulse(T)
    s.set_rising_trigger(ch_pwm, 0.2, timeout=0)
    s.start_single_acquisition()
    time.sleep(1.0)
    meas.board.run_pulses(1)
    deadline = time.monotonic() + 6
    while True:
        if s.get_acquisition_state() == 'COMP':
            break
        if time.monotonic() > deadline:
            return None
        time.sleep(0.05)
    return s.read_data([meas.CH_VOLTAGE, meas.CH_SECONDARY, ch_pwm, meas.CH_CURRENT])


def auto_range_current(meas, pulses_s):
    """Iteratively pick the smallest scope range that captures current without clip."""
    s = meas.scope
    # Try ranges from tightest to widest
    candidates = [0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0]
    chosen = None
    for r in candidates:
        s.set_channel_configuration(meas.CH_CURRENT, r, 'DC', 0.0)
        s.set_channel_label(meas.CH_CURRENT, 'I')
        df = fire_and_capture(meas, pulses_s)
        if df is None:
            continue
        i = df['I'].to_numpy()
        # Real range = scope_volts_range * probe_scale  (in amps for current probe)
        real_range = r * meas.current_probe_scale
        clipped = is_clipped(i, real_range)
        peak = np.abs(i).max()
        print(f'  [auto-range] scope={r:.2f}V (real +-{real_range:.3f}A)  '
              f'peak={peak*1e3:.1f}mA  {"CLIPPED" if clipped else "OK"}')
        if not clipped:
            chosen = r
            return chosen, df
    print('  [auto-range] No range fits; using widest (clipped)')
    return candidates[-1], df


def balance_voltages(meas, pulses_s, voltage, L_henry, T_half, T_total,
                     tol=BAL_TOL, max_iter=BAL_MAX_ITER):
    """Iteratively adjust CH2 (V-) and/or CH1 (V+) to close the target cycle.

    Strategy: compute correction sign empirically. If first step makes drift
    worse, reverse direction. Tries CH2 first, then CH1.

    Returns final V_neg, V_pos, last captured DataFrame, target cycle indices.
    """
    V_neg = voltage
    V_pos = voltage
    df = None
    cycle_start = cycle_end = None
    last_delta = None
    sign = +1   # initial guess: increase V_neg to fix positive drift

    for it in range(max_iter):
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, V_neg)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     V_pos)
        time.sleep(0.2)
        df = fire_and_capture(meas, pulses_s)
        if df is None:
            print(f'  [balance] iter {it+1}: capture failed'); break
        t_us = df['time'].to_numpy() * 1e6
        pwm  = df['PWM'].to_numpy()
        I    = df['I'].to_numpy()

        rising, falling = find_pwm_edges(t_us, pwm, vthresh=1.0)
        if len(rising) < 2 or len(falling) < 2:
            print(f'  [balance] iter {it+1}: not enough PWM edges'); break
        i_start = int(rising[-2])
        falls_after = falling[falling > i_start]
        if not len(falls_after): break
        i_mid = int(falls_after[0])
        rises_after = rising[rising > i_mid]
        i_end = int(rises_after[0]) if len(rises_after) else len(t_us) - 1

        I_win   = I[i_start:i_end]
        delta_I = float(I[i_end] - I[i_start])
        I_peak  = max((I_win.max() - I_win.min()) / 2.0, 1e-6)
        rel = abs(delta_I) / I_peak
        cycle_start, cycle_end = i_start, i_end
        print(f'  [balance] iter {it+1}: V+={V_pos:.3f} V-={V_neg:.3f}  '
              f'delta_I={delta_I*1e3:+.2f}mA  I_peak={I_peak*1e3:.1f}mA  rel={rel*100:.1f}%')
        if rel < tol:
            print(f'  [balance] converged in {it+1} iter'); break

        # Detect if we are going wrong way: compare to last drift
        if last_delta is not None and abs(delta_I) > abs(last_delta) * 1.05:
            sign = -sign
            print(f'  [balance] flipping correction sign (drift growing)')
        last_delta = delta_I

        # Step size: scaled by drift magnitude
        step = 0.1 + 0.5 * abs(delta_I) * L_henry / T_half
        V_neg = max(0.5, min(2.0 * voltage, V_neg + sign * np.sign(delta_I) * step))

    return V_neg, V_pos, df, cycle_start, cycle_end


def virtual_close_loop(B, t, i_start, i_end):
    """Force B[end_of_cycle] == B[start_of_cycle] by subtracting linear drift.

    Returns drift-corrected B array. Implements paper Section II.D's virtual
    closure: connect start and end point in post-processing.
    """
    B_out = B.copy()
    drift = (B[i_end] - B[i_start]) / (t[i_end] - t[i_start])
    # Subtract drift so cycle closes
    B_out[i_start:i_end+1] = B[i_start:i_end+1] - drift * (t[i_start:i_end+1] - t[i_start])
    return B_out


def main():
    m = CoreLossMeasurement.from_config(os.path.join(_TPT_ROOT, 'hardware_configuration.json'))
    Ae = CORE_DATABASE[CORE]['Ae']
    le = CORE_DATABASE[CORE]['le']
    Vc = Ae * le
    L_henry = theoretical_inductance(CORE, MATERIAL, N1)

    T_half = 1.0 / (2.0 * FREQ)
    T_stage1 = T_half + DC_BIAS_A * L_henry / VOLTAGE if DC_BIAS_A > 0 else T_half
    pulses_s = [T_stage1] + [T_half] * (N_HALF - 1)
    T_total = sum(pulses_s)

    print(f'== TPT setup ==')
    print(f'  Core: {CORE} {MATERIAL}, N1={N1} N2={N2}, Ae={Ae*1e6:.1f}mm^2, le={le*1e3:.1f}mm')
    print(f'  L_theory={L_henry*1e6:.1f}uH, V={VOLTAGE}V, f={FREQ/1e3:.0f}kHz')
    print(f'  Pulse train: {len(pulses_s)} half-periods, {T_total*1e6:.1f}us')

    try:
        # PSU init
        m.psu.set_source_voltage(1, VOLTAGE); m.psu.set_source_voltage(2, VOLTAGE)
        m.psu.set_current_limit(1, 3.0);      m.psu.set_current_limit(2, 3.0)
        m.psu.enable_output(1);               m.psu.enable_output(2)
        time.sleep(0.4)

        # Scope channel scales (from config)
        s = m.scope
        s.set_probe_scale(m.CH_VOLTAGE,   m.input_voltage_probe_scale)
        s.set_probe_scale(m.CH_SECONDARY, m.output_voltage_probe_scale)
        s.set_probe_scale(CH_PWM,         10.0)
        s.set_probe_scale(m.CH_CURRENT,   m.current_probe_scale)
        s.set_channel_configuration(m.CH_VOLTAGE,   2.0, 'DC', 0.0)
        s.set_channel_configuration(m.CH_SECONDARY, 1.0, 'DC', 0.0)
        s.set_channel_configuration(CH_PWM,         2.0, 'DC', 0.0)
        s.set_channel_label(m.CH_VOLTAGE,   'V_pri')
        s.set_channel_label(m.CH_SECONDARY, 'V_sec')
        s.set_channel_label(CH_PWM,         'PWM')
        s.set_number_samples(SAMPLES)
        s.set_sampling_time(SAMPLE_DT)

        # ----- 1) Auto-range current channel -----
        print('\n--- Auto-ranging current ---')
        chosen_range, _ = auto_range_current(m, pulses_s)

        # ----- 2) Volt-second balancing -----
        print('\n--- Balancing volt-seconds (adjusting CH2 V-) ---')
        V_neg, V_pos, df, i_start, i_end = balance_voltages(m, pulses_s, VOLTAGE, L_henry, T_half, T_total)
        if df is None:
            print('Balance failed -- if V_pri is near zero, check the +12V gate-driver supply')
            return

        # ----- 3) Final capture (use last balanced df) -----
        t = df['time'].to_numpy()
        t_us = t * 1e6
        vp  = df['V_pri'].to_numpy()
        vs  = df['V_sec'].to_numpy()
        pwm = df['PWM'].to_numpy()
        I   = df['I'].to_numpy()
        dt = t[1] - t[0]

        # Sanity check
        if abs(vp).max() < 1.0:
            print('\nERROR: V_pri swing < 1V. REMINDER: check +12V gate-driver supply.')
            return

        # ----- 4) Compute B(t), H(t), Q on the (closed) target cycle -----
        burst_mask = t_us < (T_total * 1e6) * 1.1
        B_full = np.zeros_like(vs)
        B_full[burst_mask] = np.cumsum(vs[burst_mask] * dt) / (N2 * Ae)
        t_burst = t[burst_mask]
        if len(t_burst) > 10:
            p = np.polyfit(t_burst, B_full[burst_mask], 1)
            B_full[burst_mask] = B_full[burst_mask] - np.polyval(p, t_burst)

        # Virtual closure of the BH loop: force B[i_end] == B[i_start]
        # (Paper Section II.D fallback for unbalanced drives.)
        B_full = virtual_close_loop(B_full, t, i_start, i_end)
        H_full = N1 * I / le

        t_c  = t[i_start:i_end]
        vs_c = vs[i_start:i_end]
        I_c  = I[i_start:i_end]
        B_c  = B_full[i_start:i_end]
        H_c  = H_full[i_start:i_end]

        Q       = abs((N1/N2) * np.trapezoid(I_c * vs_c, t_c))
        P_core  = Q * FREQ
        B_peak  = (B_c.max() - B_c.min()) / 2.0
        H_peak  = (H_c.max() - H_c.min()) / 2.0

        delta_I_final = I[i_end] - I[i_start]
        I_pp_cycle = I_c.max() - I_c.min()

        print(f'\n== TPT result ==')
        print(f'  Final V_neg = {V_neg:.3f} V (CH2)')
        print(f'  Target cycle: t={t_us[i_start]:.2f}..{t_us[i_end]:.2f}us  (window {(t_us[i_end]-t_us[i_start]):.2f}us)')
        print(f'  Cycle drift delta_I = {delta_I_final*1e3:+.2f} mA  (I_pp cycle {I_pp_cycle*1e3:.1f}mA)')
        print(f'  Q_cycle    = {Q*1e6:.3f} uJ')
        print(f'  P_core     = {P_core*1e3:.3f} mW @ {FREQ/1e3:.0f}kHz')
        print(f'  B_peak     = {B_peak*1e3:.2f} mT')
        print(f'  H_peak     = {H_peak:.2f} A/m')
        print(f'  P_density  = {P_core/Vc/1e3:.2f} kW/m^3')

        # ----- 5) Plot -----
        fig = plt.figure(figsize=(15, 11))
        gs = fig.add_gridspec(4, 2)
        axw = [fig.add_subplot(gs[k, 0]) for k in range(4)]
        for k in range(1,4): axw[k].sharex(axw[0])
        axbh = fig.add_subplot(gs[:, 1])

        cy0_us, cy1_us = t_us[i_start], t_us[i_end]
        for ax, sig, c, lbl in [(axw[0], pwm, 'C2', 'PWM (TP4)'),
                                (axw[1], vp,  'C0', 'V_pri (TP16-TP17)'),
                                (axw[2], vs,  'C1', 'V_sec (TP18-TP19)'),
                                (axw[3], I*1e3,'C3','I (mA)')]:
            ax.plot(t_us, sig, color=c, lw=1.0)
            ax.axhline(0, color='gray', lw=0.4, ls=':')
            ax.axvspan(cy0_us, cy1_us, alpha=0.18, color='yellow')
            ax.set_ylabel(lbl, fontsize=10)
            ax.grid(alpha=0.3)
        axw[3].set_xlabel('time [us]')
        axw[0].set_title(f'TPT  {CORE} {MATERIAL}, N1={N1} N2={N2}, V+/-={VOLTAGE}/{V_neg:.2f}V, f={FREQ/1e3:.0f}kHz')

        axbh.plot(H_full[burst_mask], B_full[burst_mask]*1e3, color='lightgray', lw=0.7, label='full burst')
        axbh.plot(H_c, B_c*1e3, color='C4', lw=2.0, label='target cycle (closed)')
        axbh.axhline(0, color='gray', lw=0.4, ls=':'); axbh.axvline(0, color='gray', lw=0.4, ls=':')
        axbh.set_xlabel('H [A/m]'); axbh.set_ylabel('B [mT]')
        axbh.set_title(f'B-H loop\nQ={Q*1e6:.2f}uJ  P_core={P_core*1e3:.1f}mW  B_pk={B_peak*1e3:.1f}mT  drift={delta_I_final*1e3:+.1f}mA')
        axbh.grid(alpha=0.3); axbh.legend(loc='upper left', fontsize=9)

        plt.tight_layout()
        out = os.path.abspath('tpt_measure.png')
        plt.savefig(out, dpi=120)
        print(f'\nSaved {out}')

    finally:
        m.psu.disable_output(1); m.psu.disable_output(2)
        m.close()


if __name__ == '__main__':
    main()
