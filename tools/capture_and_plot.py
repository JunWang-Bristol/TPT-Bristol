#!/usr/bin/env python3
"""Verify the new firmware's pulse pattern at TP4/TP5.

CH A (CH_VOLTAGE=0)   -> TP5 (SD line) — should toggle anti-phase to PWM
CH B (CH_SECONDARY=1) -> TP4 (PWM input) — should toggle anti-phase to SD
"""
import os, sys, time
_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from tpt import CoreLossMeasurement


VOLTAGE = 5.0
FREQ    = 50_000
N_PULSE = 8
T_HALF  = 1.0 / (2.0 * FREQ)


def main():
    cfg = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    m = CoreLossMeasurement.from_config(cfg)

    try:
        m.psu.set_source_voltage(1, VOLTAGE); m.psu.set_source_voltage(2, VOLTAGE)
        m.psu.set_current_limit(1, 3.0);      m.psu.set_current_limit(2, 3.0)
        m.psu.enable_output(1);               m.psu.enable_output(2)
        time.sleep(0.4)

        m.board.clear_pulses()
        for _ in range(N_PULSE):
            m.board.add_pulse(T_HALF)

        s = m.scope
        s.set_probe_scale(m.CH_VOLTAGE,   10.0)   # TP5 (SD)
        s.set_probe_scale(m.CH_SECONDARY, 10.0)   # TP4 (PWM)
        s.set_channel_configuration(m.CH_VOLTAGE,   2.0, 'DC', 0.0)
        s.set_channel_configuration(m.CH_SECONDARY, 2.0, 'DC', 0.0)
        s.set_channel_label(m.CH_VOLTAGE,   'SD_TP5')
        s.set_channel_label(m.CH_SECONDARY, 'PWM_TP4')
        s.set_rising_trigger(m.CH_SECONDARY, 1.0)   # trigger on PWM rising
        s.set_number_samples(2000)
        s.set_sampling_time(100e-9)

        s.start_single_acquisition()
        time.sleep(1.5)
        m.board.run_pulses(1)
        deadline = time.monotonic() + 8
        while True:
            if s.get_acquisition_state() == 'COMP':
                break
            if time.monotonic() > deadline:
                print('TIMEOUT'); return
            time.sleep(0.1)

        df = s.read_data([m.CH_VOLTAGE, m.CH_SECONDARY])
        df.to_csv('capture_plot.csv', index=False)

        t   = df['time'].to_numpy() * 1e6
        sd  = df['SD_TP5'].to_numpy()
        pwm = df['PWM_TP4'].to_numpy()

        fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
        ax[0].plot(t, sd, color='C2', linewidth=1.0)
        ax[0].axhline(0, color='gray', linewidth=0.4, linestyle=':')
        ax[0].set_ylabel('SD at TP5 (PB4) [V]')
        ax[0].set_title(f'Pin pattern verification (zip-style logic) — 8 pulses x {T_HALF*1e6:.0f} us @ {FREQ/1e3:.0f} kHz')
        ax[0].grid(alpha=0.3)

        ax[1].plot(t, pwm, color='C0', linewidth=1.0)
        ax[1].axhline(0, color='gray', linewidth=0.4, linestyle=':')
        ax[1].set_ylabel('PWM at TP4 (PB10) [V]')
        ax[1].set_xlabel('time [us]')
        ax[1].grid(alpha=0.3)

        plt.tight_layout()
        out = os.path.abspath('capture_plot.png')
        plt.savefig(out, dpi=120)
        print(f'Saved: {out}')

        print(f'SD_TP5 (PB4):  min={sd.min():+6.2f}  max={sd.max():+6.2f}  mean={sd.mean():+5.2f}')
        print(f'PWM_TP4(PB10): min={pwm.min():+6.2f}  max={pwm.max():+6.2f}  mean={pwm.mean():+5.2f}')

        print('\nPer-pulse (10us slots):')
        for k in range(8):
            mask = (t >= k*10) & (t < (k+1)*10)
            if mask.sum()<3: continue
            sm = sd[mask].mean()
            pm = pwm[mask].mean()
            print(f'  pulse {k} ({"EVEN" if k%2==0 else " ODD"}): SD mean={sm:+6.2f}  PWM mean={pm:+6.2f}')
    finally:
        m.psu.disable_output(1); m.psu.disable_output(2)
        m.close()


if __name__ == '__main__':
    main()
