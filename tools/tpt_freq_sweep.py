"""TPT frequency sweep, 10 kHz -> 5 MHz, 10 logarithmic steps.

For each frequency:
  - Build 8-pulse train at that frequency
  - Auto-set scope sample rate (40 samples per half-period)
  - Auto-range current channel
  - Capture (PWM trigger on CH C)
  - Extract target cycle, compute Q, B_peak, H_peak, P_core
  - Save BH loop for that point

Output: tpt_sweep.csv with all metrics, tpt_sweep.png with summary plots.

Stops if any frequency point fails (no trigger, no pulses, all clipping).
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
N_HALF    = 8
N_FREQ    = 10
F_LO      = 10e3
F_HI      = 5e6
CH_PWM    = 2

# Flux targeting: keep B_peak ~constant across frequencies
B_TARGET  = 0.05      # 50 mT half-swing
FLUX_TOL  = 0.20      # converge when |B_peak - target| / target < 20 %
FLUX_ITER = 4         # max voltage iterations per frequency
V_MIN     = 0.5       # PSU minimum (V)
V_MAX     = 30.0      # PSU maximum / safety cap (V)
V_INIT    = 5.0       # starting voltage on first frequency

# Logarithmic frequency sweep
FREQS = np.logspace(np.log10(F_LO), np.log10(F_HI), N_FREQ)


def find_pwm_edges(t_us, pwm, vthresh=0.5):
    above = pwm > vthresh
    rising  = np.where((~above[:-1]) &  above[1:])[0] + 1
    falling = np.where( above[:-1] & (~above[1:]))[0] + 1
    return rising, falling


def is_clipped(arr, range_v, threshold=0.97):
    return np.abs(arr).max() >= range_v * threshold


def fire_and_capture(meas, pulses_s):
    s = meas.scope
    meas.board.clear_pulses()
    for T in pulses_s:
        meas.board.add_pulse(T)
    s.set_rising_trigger(CH_PWM, 0.2, timeout=0)
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
    return s.read_data([meas.CH_VOLTAGE, meas.CH_SECONDARY, CH_PWM, meas.CH_CURRENT])


SCOPE_RANGES = [0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]

# Sensible initial ranges (scope-side volts) that won't clip the typical signal
# AND keep the PWM trigger threshold reachable. We tighten only after we have
# a clean (non-clipping) capture.
INITIAL_IDX = {
    'V_pri': 7,   # 5.0 V scope -> +-5 V real (probe scale 1, 1x probe)
    'V_sec': 7,   # 5.0 V scope -> +-5 V real
    'PWM':   8,   # 10.0 V scope -> +-10 V real (covers logic-level PWM)
    'I':     3,   # 0.2 V scope -> +-0.4 A real (probe scale 2, 2 mA/mV probe)
}


def auto_range_all(meas, pulses_s, max_passes=6):
    """Start at SAFE WIDE ranges (so PWM trigger fires), then widen any that
    still clip. Returns the final DataFrame with no channel clipping.
    """
    s = meas.scope
    channels = [
        (meas.CH_VOLTAGE,   meas.input_voltage_probe_scale,  'V_pri'),
        (meas.CH_SECONDARY, meas.output_voltage_probe_scale, 'V_sec'),
        (CH_PWM,            10.0,                             'PWM'),
        (meas.CH_CURRENT,   meas.current_probe_scale,         'I'),
    ]

    range_idx = {label: INITIAL_IDX[label] for _, _, label in channels}

    for pass_num in range(max_passes):
        for ch, scale, label in channels:
            s.set_channel_configuration(ch, SCOPE_RANGES[range_idx[label]], 'DC', 0.0)
            s.set_channel_label(ch, label)

        df = fire_and_capture(meas, pulses_s)
        if df is None:
            print(f'  [auto-range] pass {pass_num+1}: capture timed out — widening PWM range')
            # If trigger never fires, widen PWM range to make signal more likely to cross threshold
            if range_idx['PWM'] < len(SCOPE_RANGES) - 1:
                range_idx['PWM'] += 1
                continue
            return None

        widened_any = False
        widened_list = []
        for ch, scale, label in channels:
            data = df[label].to_numpy()
            r = SCOPE_RANGES[range_idx[label]]
            real_range = r * scale
            if is_clipped(data, real_range) and range_idx[label] < len(SCOPE_RANGES) - 1:
                range_idx[label] += 1
                widened_any = True
                widened_list.append(label)

        ranges_str = ', '.join(f'{lab}={SCOPE_RANGES[range_idx[lab]]}V'
                                for _, _, lab in channels)
        if not widened_any:
            print(f'  [auto-range] settled (pass {pass_num+1}): {ranges_str}')
            return df
        else:
            print(f'  [auto-range] pass {pass_num+1}: widened {widened_list}; new ranges {ranges_str}')

    print(f'  [auto-range] did not settle after {max_passes} passes — using widest available')
    return df


def auto_range_current(meas, pulses_s, max_iter=6):
    df = auto_range_all(meas, pulses_s)
    return None, df


def _capture_and_analyse(meas, pulses_s, T_total, le, Ae):
    """Auto-range capture, find target cycle, compute B_peak, Q, etc.
    Returns dict with raw measurements (no flux targeting here)."""
    df = auto_range_all(meas, pulses_s)
    if df is None:
        return None
    t = df['time'].to_numpy()
    t_us = t * 1e6
    vp = df['V_pri'].to_numpy()
    vs = df['V_sec'].to_numpy()
    pwm = df['PWM'].to_numpy()
    I = df['I'].to_numpy()
    dt_real = t[1] - t[0]

    rising, falling = find_pwm_edges(t_us, pwm, vthresh=0.5)
    if len(rising) < 2 or len(falling) < 2:
        return {'failed': 'no PWM edges', 'df': df}

    i_start = int(rising[-2])
    falls_after = falling[falling > i_start]
    if not len(falls_after):
        return {'failed': 'no falling after rising', 'df': df}
    i_mid = int(falls_after[0])
    rises_after = rising[rising > i_mid]
    i_end = int(rises_after[0]) if len(rises_after) else len(t) - 1

    burst_mask = t_us < (T_total * 1e6) * 1.1
    B = np.zeros_like(vs)
    if burst_mask.sum() > 10:
        B[burst_mask] = np.cumsum(vs[burst_mask] * dt_real) / (N2 * Ae)
        p = np.polyfit(t[burst_mask], B[burst_mask], 1)
        B[burst_mask] = B[burst_mask] - np.polyval(p, t[burst_mask])
    drift = (B[i_end] - B[i_start]) / (t[i_end] - t[i_start])
    B[i_start:i_end+1] = B[i_start:i_end+1] - drift * (t[i_start:i_end+1] - t[i_start])
    H = N1 * I / le

    t_c = t[i_start:i_end]
    vs_c = vs[i_start:i_end]
    I_c = I[i_start:i_end]
    B_c = B[i_start:i_end]
    H_c = H[i_start:i_end]

    Q = abs((N1/N2) * np.trapezoid(I_c * vs_c, t_c)) if len(t_c) > 1 else 0.0
    B_peak = (B_c.max() - B_c.min()) / 2.0 if len(B_c) > 1 else 0.0
    H_peak = (H_c.max() - H_c.min()) / 2.0 if len(H_c) > 1 else 0.0

    return {
        'failed': None, 'df': df,
        'Q': Q, 'B_peak': B_peak, 'H_peak': H_peak,
        'I_pp': (I_c.max() - I_c.min()) if len(I_c) > 1 else 0.0,
        'Vpri_pp': vp.max() - vp.min(),
        'Vsec_pp': vs.max() - vs.min(),
        'cycle': (i_start, i_end),
        'B_c_mT': B_c * 1e3, 'H_c': H_c,
    }


def measure_one_freq(meas, f, V_init):
    """Iteratively adjust PSU voltage until B_peak ~= B_TARGET, then measure."""
    T_half = 1.0 / (2.0 * f)
    pulses_s = [T_half] * N_HALF
    T_total = sum(pulses_s)

    dt = max(16e-9, T_half / 40.0)
    n_samples = min(8000, max(1000, int(T_total * 3 / dt)))
    s = meas.scope
    s.set_number_samples(n_samples)
    s.set_sampling_time(dt)

    Ae = CORE_DATABASE[CORE]['Ae']
    le = CORE_DATABASE[CORE]['le']

    V = max(V_MIN, min(V_MAX, V_init))
    last_result = None
    for it in range(FLUX_ITER):
        meas.psu.set_source_voltage(1, V)
        meas.psu.set_source_voltage(2, V)
        time.sleep(0.2)
        r = _capture_and_analyse(meas, pulses_s, T_total, le, Ae)
        if r is None or r.get('failed'):
            print(f'  [flux] iter {it+1}: V={V:.2f}V  capture failed ({r.get("failed") if r else "timeout"})')
            return r
        last_result = r
        Bp = r['B_peak']
        err = (Bp - B_TARGET) / max(B_TARGET, 1e-12)
        print(f'  [flux] iter {it+1}: V={V:.2f}V  B_peak={Bp*1e3:.1f}mT  '
              f'(target {B_TARGET*1e3:.0f}mT, err={err*100:+.1f}%)')
        if abs(err) < FLUX_TOL:
            break
        # Scale V to hit target; clamp PSU limits
        if Bp > 1e-6:
            V_new = V * B_TARGET / Bp
        else:
            V_new = V * 2.0   # B too small to read — bump up
        V = max(V_MIN, min(V_MAX, V_new))

    if last_result is None:
        return None
    last_result['f'] = f
    last_result['V_used'] = V
    last_result['T_half_us'] = T_half * 1e6
    last_result['P_core'] = last_result['Q'] * f
    return last_result

    t = df['time'].to_numpy()
    t_us = t * 1e6
    vp = df['V_pri'].to_numpy()
    vs = df['V_sec'].to_numpy()
    pwm = df['PWM'].to_numpy()
    I = df['I'].to_numpy()
    dt_real = t[1] - t[0]

    # Pulse edges
    rising, falling = find_pwm_edges(t_us, pwm, vthresh=0.5)
    if len(rising) < 2 or len(falling) < 2:
        return {'f': f, 'failed': 'no PWM edges', 'df': df}

    i_start = int(rising[-2])
    falls_after = falling[falling > i_start]
    if not len(falls_after):
        return {'f': f, 'failed': 'no falling after rising', 'df': df}
    i_mid = int(falls_after[0])
    rises_after = rising[rising > i_mid]
    i_end = int(rises_after[0]) if len(rises_after) else len(t) - 1

    # B(t), H(t)
    Ae = CORE_DATABASE[CORE]['Ae']
    le = CORE_DATABASE[CORE]['le']
    burst_mask = t_us < (T_total * 1e6) * 1.1
    B = np.zeros_like(vs)
    if burst_mask.sum() > 10:
        B[burst_mask] = np.cumsum(vs[burst_mask] * dt_real) / (N2 * Ae)
        p = np.polyfit(t[burst_mask], B[burst_mask], 1)
        B[burst_mask] = B[burst_mask] - np.polyval(p, t[burst_mask])
    # Virtual closure
    drift = (B[i_end] - B[i_start]) / (t[i_end] - t[i_start])
    B[i_start:i_end+1] = B[i_start:i_end+1] - drift * (t[i_start:i_end+1] - t[i_start])
    H = N1 * I / le

    t_c = t[i_start:i_end]
    vs_c = vs[i_start:i_end]
    I_c = I[i_start:i_end]
    B_c = B[i_start:i_end]
    H_c = H[i_start:i_end]

    Q = abs((N1/N2) * np.trapezoid(I_c * vs_c, t_c))
    P_core = Q * f
    B_peak = (B_c.max() - B_c.min()) / 2.0 if len(B_c) > 1 else 0
    H_peak = (H_c.max() - H_c.min()) / 2.0 if len(H_c) > 1 else 0
    I_pp = I_c.max() - I_c.min() if len(I_c) > 1 else 0
    Vpri_pp = vp.max() - vp.min()
    Vsec_pp = vs.max() - vs.min()

    return {
        'f': f, 'failed': None, 'df': df,
        'Q': Q, 'P_core': P_core, 'B_peak': B_peak, 'H_peak': H_peak,
        'I_pp': I_pp, 'Vpri_pp': Vpri_pp, 'Vsec_pp': Vsec_pp,
        'cycle': (i_start, i_end), 'B_c': B_c*1e3, 'H_c': H_c,
        'dt': dt, 'n_samples': n_samples,
        'T_half_us': T_half*1e6,
    }


def main():
    m = CoreLossMeasurement.from_config(os.path.join(_TPT_ROOT, 'hardware_configuration.json'))
    Vc = CORE_DATABASE[CORE]['Ae'] * CORE_DATABASE[CORE]['le']

    print(f'== TPT frequency sweep: {N_FREQ} steps from {F_LO/1e3:.0f} kHz to {F_HI/1e6:.1f} MHz ==')
    for k, f in enumerate(FREQS):
        print(f'  step {k+1:2d}: {f/1e3:7.1f} kHz  (T_half = {1e6/(2*f):6.2f} us)')

    results = []
    try:
        # PSU - initial voltage (will be adjusted per-frequency by flux targeting)
        m.psu.set_source_voltage(1, V_INIT); m.psu.set_source_voltage(2, V_INIT)
        m.psu.set_current_limit(1, 3.0);     m.psu.set_current_limit(2, 3.0)
        m.psu.enable_output(1);              m.psu.enable_output(2)
        time.sleep(0.4)

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

        # Track last successful voltage so the next frequency can start near it
        V_last = V_INIT
        for k, f in enumerate(FREQS):
            print(f'\n--- step {k+1}/{N_FREQ}: f = {f/1e3:.1f} kHz ---')
            # Voltage scales linearly with frequency for constant flux (V*T_half = const)
            # So scale V_last by (f / f_prev) for a good initial guess
            if k > 0:
                V_init = max(V_MIN, min(V_MAX, V_last * f / FREQS[k-1]))
            else:
                V_init = V_INIT
            r = measure_one_freq(m, f, V_init)
            if r and not r.get('failed'):
                V_last = r['V_used']
            if r is None:
                print(f'  FAILED: capture timed out')
                results.append({'f': f, 'failed': 'capture timeout'})
                break
            if r.get('failed'):
                print(f'  FAILED: {r["failed"]}')
                results.append(r)
                break
            print(f'  V_used={r["V_used"]:.2f}V  Q={r["Q"]*1e6:.2f}uJ  '
                  f'P_core={r["P_core"]*1e3:.1f}mW  B_pk={r["B_peak"]*1e3:.1f}mT  '
                  f'I_pp={r["I_pp"]*1e3:.1f}mA  V_pri_pp={r["Vpri_pp"]:.1f}V')
            results.append(r)

    finally:
        m.psu.disable_output(1); m.psu.disable_output(2)
        m.close()

    # Save CSV
    import csv
    with open('tpt_sweep.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['f_Hz', 'Q_uJ', 'P_core_mW', 'B_peak_mT', 'H_peak_Apm',
                    'I_pp_mA', 'Vpri_pp', 'Vsec_pp', 'failed'])
        for r in results:
            if r.get('failed'):
                w.writerow([r['f'], '', '', '', '', '', '', '', r['failed']])
            else:
                w.writerow([r['f'], r['Q']*1e6, r['P_core']*1e3,
                            r['B_peak']*1e3, r['H_peak'],
                            r['I_pp']*1e3, r['Vpri_pp'], r['Vsec_pp'], ''])
    print(f'\nSaved tpt_sweep.csv ({len(results)} points)')

    # Plot summary
    good = [r for r in results if not r.get('failed') and 'Q' in r]
    if not good:
        print('No good data points')
        return

    fs = np.array([r['f'] for r in good])
    P_core = np.array([r['P_core']*1e3 for r in good])
    B_peak = np.array([r['B_peak']*1e3 for r in good])
    I_pp   = np.array([r['I_pp']*1e3 for r in good])
    Vpri   = np.array([r['Vpri_pp'] for r in good])
    Vsec   = np.array([r['Vsec_pp'] for r in good])

    fig, ax = plt.subplots(2, 3, figsize=(16, 9))
    ax[0,0].loglog(fs/1e3, P_core, 'o-', color='C3'); ax[0,0].set_xlabel('f [kHz]'); ax[0,0].set_ylabel('P_core [mW]'); ax[0,0].grid(True, which='both', alpha=0.3); ax[0,0].set_title('Core loss vs freq')
    ax[0,1].loglog(fs/1e3, B_peak, 'o-', color='C0'); ax[0,1].set_xlabel('f [kHz]'); ax[0,1].set_ylabel('B_peak [mT]'); ax[0,1].grid(True, which='both', alpha=0.3); ax[0,1].set_title('Flux density swing')
    ax[0,2].semilogx(fs/1e3, I_pp,  'o-', color='C2'); ax[0,2].set_xlabel('f [kHz]'); ax[0,2].set_ylabel('I_pp [mA]'); ax[0,2].grid(True, which='both', alpha=0.3); ax[0,2].set_title('Primary current pp')
    ax[1,0].semilogx(fs/1e3, Vpri,  'o-', color='C0'); ax[1,0].set_xlabel('f [kHz]'); ax[1,0].set_ylabel('V_pri pp [V]'); ax[1,0].grid(True, which='both', alpha=0.3); ax[1,0].set_title('V_pri swing (TP16-TP17)')
    ax[1,1].semilogx(fs/1e3, Vsec,  'o-', color='C1'); ax[1,1].set_xlabel('f [kHz]'); ax[1,1].set_ylabel('V_sec pp [V]'); ax[1,1].grid(True, which='both', alpha=0.3); ax[1,1].set_title('V_sec swing (TP18-TP19)')

    # All BH loops overlaid
    cmap = plt.cm.viridis
    for k, r in enumerate(good):
        c = cmap(k/max(1, len(good)-1))
        ax[1,2].plot(r['H_c'], r['B_c'], color=c, lw=1.0, label=f'{r["f"]/1e3:.0f}kHz')
    ax[1,2].set_xlabel('H [A/m]'); ax[1,2].set_ylabel('B [mT]')
    ax[1,2].set_title('BH loops (target cycles)')
    ax[1,2].grid(alpha=0.3); ax[1,2].legend(fontsize=7, ncol=2, loc='upper left')

    plt.suptitle(f'TPT frequency sweep — T26 3C90, N1={N1} N2={N2}, V={VOLTAGE}V', fontsize=12)
    plt.tight_layout()
    out = os.path.abspath('tpt_sweep.png')
    plt.savefig(out, dpi=120)
    print(f'Saved {out}')


if __name__ == '__main__':
    main()
