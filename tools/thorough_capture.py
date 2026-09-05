"""Thorough capture and analysis of the burst.

Strategy: trigger on V_sec rising edge (no auto-timeout) so we KNOW the
captured window contains the burst. Slower sample rate (250 ns/sample)
so 2000 samples cover 500 us — far more than the 80 us burst — so even
if trigger position is uncertain we still see the whole event.
"""
import os, sys, time
_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from tpt import CoreLossMeasurement


m = CoreLossMeasurement.from_config(os.path.join(_TPT_ROOT, 'hardware_configuration.json'))
try:
    m.psu.set_source_voltage(1, 5.0); m.psu.set_source_voltage(2, 5.0)
    m.psu.set_current_limit(1, 3.0); m.psu.set_current_limit(2, 3.0)
    m.psu.enable_output(1); m.psu.enable_output(2)
    time.sleep(0.4)

    m.board.clear_pulses()
    for _ in range(8):
        m.board.add_pulse(10e-6)

    s = m.scope
    CH_PWM = 2   # CH C = PWM probe on TP4
    s.set_probe_scale(m.CH_VOLTAGE,   10.0)   # CH A on TP16 (FIX_A)
    s.set_probe_scale(m.CH_SECONDARY, 10.0)   # CH B on TP18 (V_sec)
    s.set_probe_scale(CH_PWM,         10.0)   # CH C on TP4 (PWM)
    s.set_probe_scale(m.CH_CURRENT,   m.current_probe_scale)   # CH D current
    s.set_channel_configuration(m.CH_VOLTAGE,   2.0, 'DC', 0.0)
    s.set_channel_configuration(m.CH_SECONDARY, 1.0, 'DC', 0.0)
    s.set_channel_configuration(CH_PWM,         2.0, 'DC', 0.0)
    s.set_channel_configuration(m.CH_CURRENT,   0.5, 'DC', 0.0)
    s.set_channel_label(m.CH_VOLTAGE,   'V_pri')
    s.set_channel_label(m.CH_SECONDARY, 'V_sec')
    s.set_channel_label(CH_PWM,         'PWM')
    s.set_channel_label(m.CH_CURRENT,   'I')
    # Trigger on CH C (PWM at TP4) rising at +0.2V scope-side = +2V real.
    # PWM swings 0..+5V real cleanly, so this is a guaranteed reliable trigger.
    s.set_rising_trigger(CH_PWM, 0.2, timeout=0)
    s.set_number_samples(2000)
    s.set_sampling_time(100e-9)   # 100 ns -> 200 us window

    print('Arming, sleep, fire one 8-pulse burst (50 kHz, 80 us total)')
    s.start_single_acquisition()
    time.sleep(1.5)
    m.board.run_pulses(1)
    deadline = time.monotonic() + 6
    state = ''
    while True:
        state = s.get_acquisition_state()
        if state == 'COMP': break
        if time.monotonic() > deadline:
            print(f'TIMEOUT (state={state}) - trigger never fired on real V_sec edge')
            sys.exit(1)
        time.sleep(0.05)

    df = s.read_data([m.CH_VOLTAGE, m.CH_SECONDARY, CH_PWM, m.CH_CURRENT])
    df.to_csv('thorough_capture.csv', index=False)
    print('Columns:', df.columns.tolist())
    t  = df['time'].to_numpy() * 1e6
    vp = df['V_pri'].to_numpy()
    vs = df['V_sec'].to_numpy()
    pwm = df['PWM'].to_numpy()
    i  = df['I'].to_numpy()
    dt_us = t[1] - t[0]
    fs   = 1.0/(dt_us*1e-6)
    print(f'\nCaptured {len(t)} samples, dt={dt_us*1000:.0f} ns, fs={fs/1e6:.2f} MHz, total span={t[-1]:.1f} us')
    print(f'V_pri: min={vp.min():+.3f} max={vp.max():+.3f} pp={vp.max()-vp.min():.3f} V')
    print(f'V_sec: min={vs.min():+.3f} max={vs.max():+.3f} pp={vs.max()-vs.min():.3f} V')
    print(f'PWM  : min={pwm.min():+.3f} max={pwm.max():+.3f} pp={pwm.max()-pwm.min():.3f} V')
    print(f'I    : min={i.min()*1e3:+.1f} max={i.max()*1e3:+.1f} pp={(i.max()-i.min())*1e3:.1f} mA')

    # Use PWM rising edges as ground truth for pulse boundaries
    threshold_v = 1.0   # PWM crosses 1V cleanly between 0 and 5V
    above = pwm > threshold_v
    rising = np.where((~above[:-1]) & above[1:])[0]
    falling = np.where(above[:-1] & (~above[1:]))[0]
    print(f'\nPWM rising edges: {len(rising)}, falling edges: {len(falling)}')
    if len(rising) > 1:
        gaps = np.diff(rising) * dt_us
        print(f'  Gaps between rising edges: mean={gaps.mean():.2f}us  median={np.median(gaps):.2f}us')
        print(f'  Expected gap = 20us (10us high + 10us low)')
        print(f'  Edge times (us): {[f"{t[r]:.2f}" for r in rising[:10]]}')

    # Now correlate: during PWM HIGH (Q1 on), V_pri should be at +V; during LOW, at 0V (or -V if bipolar topology).
    # Compute average V_pri during HIGH and LOW phases of PWM
    if rising.any() and falling.any():
        print('\nPer-pulse V_pri/V_sec/I averages (relative to PWM phase):')
        for k in range(min(8, len(rising))):
            r = rising[k]
            # find next falling
            f_after = falling[falling > r]
            if not len(f_after): break
            f = f_after[0]
            high_mask = slice(r, f)  # PWM HIGH = Q1 ON
            print(f'  Pulse {k} HIGH(Q1):  t={t[r]:6.2f}-{t[f]:6.2f}us  '
                  f'V_pri_mean={vp[high_mask].mean():+.3f}  '
                  f'V_sec_mean={vs[high_mask].mean():+.3f}  '
                  f'I_mean={i[high_mask].mean()*1e3:+.1f}mA')
            # next rising
            r_after = rising[rising > f]
            if not len(r_after): break
            r2 = r_after[0]
            low_mask = slice(f, r2)
            print(f'  Pulse {k} LOW (Q2):  t={t[f]:6.2f}-{t[r2]:6.2f}us  '
                  f'V_pri_mean={vp[low_mask].mean():+.3f}  '
                  f'V_sec_mean={vs[low_mask].mean():+.3f}  '
                  f'I_mean={i[low_mask].mean()*1e3:+.1f}mA')

    # Plot 0..120us (covers the 80us burst with margin)
    sl = (t >= 0) & (t < 120)
    fig, ax = plt.subplots(4, 1, figsize=(15, 11), sharex=True)
    ax[0].plot(t[sl], pwm[sl], color='C2', linewidth=1.0)
    ax[0].axhline(0, color='gray', lw=0.4, ls=':')
    ax[0].set_ylabel('PWM at TP4 [V]\n(MCU command)')
    ax[0].set_title(f'Capture with INDUCTOR load (T26 3C90) — triggered on PWM TP4')
    ax[0].grid(alpha=0.3)
    ax[1].plot(t[sl], vp[sl], color='C0', linewidth=1.0)
    ax[1].axhline(0, color='gray', lw=0.4, ls=':')
    ax[1].set_ylabel('V_pri (TP16-TP17) [V]')
    ax[1].grid(alpha=0.3)
    ax[2].plot(t[sl], vs[sl], color='C1', linewidth=1.0)
    ax[2].axhline(0, color='gray', lw=0.4, ls=':')
    ax[2].set_ylabel('V_sec (TP18-TP19) [V]')
    ax[2].grid(alpha=0.3)
    ax[3].plot(t[sl], i[sl]*1e3, color='C3', linewidth=1.0)
    ax[3].axhline(0, color='gray', lw=0.4, ls=':')
    ax[3].set_ylabel('I [mA]')
    ax[3].set_xlabel('time [us]')
    ax[3].grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig('thorough_capture.png', dpi=120)
    print(f'\nSaved thorough_capture.png')

finally:
    m.psu.disable_output(1); m.psu.disable_output(2)
    m.close()
