#!/usr/bin/env python3
"""Standalone TPT capture with the user's probe setup:
  CH A (V_pri):  tip TP16 (FIX_A),  GND clip TP17
  CH B (V_sec):  tip TP18,          GND clip TP19  (differential across secondary)
  CH D (Current): clamp probe (100 mV/A)

No firmware changes. Robust trigger config to avoid the previous timeout.
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
N2        = 1
FREQ      = 50_000
VOLTAGE   = 5.0
N_PULSE   = 8
T_HALF    = 1.0 / (2.0 * FREQ)


def main():
    cfg = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    m = CoreLossMeasurement.from_config(cfg)

    L_th = theoretical_inductance(CORE, MATERIAL, N1)
    print(f'Core={CORE} {MATERIAL}  N1={N1} N2={N2}  V={VOLTAGE} f={FREQ/1e3:.0f}kHz')
    print(f'L_theory={L_th*1e6:.1f} uH   dI_expected={VOLTAGE/(2*FREQ*L_th)*1e3:.0f} mA pp')

    try:
        m.psu.set_source_voltage(1, VOLTAGE); m.psu.set_source_voltage(2, VOLTAGE)
        m.psu.set_current_limit(1, 3.0);      m.psu.set_current_limit(2, 3.0)
        m.psu.enable_output(1);               m.psu.enable_output(2)
        time.sleep(0.4)

        m.board.clear_pulses()
        for _ in range(N_PULSE):
            m.board.add_pulse(T_HALF)

        s = m.scope
        s.set_probe_scale(m.CH_VOLTAGE,   m.input_voltage_probe_scale)
        s.set_probe_scale(m.CH_SECONDARY, m.output_voltage_probe_scale)
        s.set_probe_scale(m.CH_CURRENT,   m.current_probe_scale)
        # Wider ranges to handle the bipolar +-5V swing on V_pri
        s.set_channel_configuration(m.CH_VOLTAGE,   2.0, 'DC', 0.0)   # +-20V on V_pri
        s.set_channel_configuration(m.CH_SECONDARY, 1.0, 'DC', 0.0)
        s.set_channel_configuration(m.CH_CURRENT,   0.5, 'DC', 0.0)
        s.set_channel_label(m.CH_VOLTAGE,   'V_pri')
        s.set_channel_label(m.CH_SECONDARY, 'V_sec')
        s.set_channel_label(m.CH_CURRENT,   'I')

        # Trigger on V_sec (CH B) rising at small positive scope-side threshold.
        # V_sec is differential across the secondary so it has clean edges
        # synchronised with the half-bridge transitions. timeout=2000 ms makes
        # the scope auto-capture if no edge is detected in time, so we never
        # miss the burst.
        s.set_rising_trigger(m.CH_SECONDARY, 0.02, timeout=2000)
        s.set_number_samples(2000)
        s.set_sampling_time(100e-9)

        print('\nArming scope and firing burst...')
        s.start_single_acquisition()
        time.sleep(1.5)
        m.board.run_pulses(1)
        deadline = time.monotonic() + 10
        while True:
            state = s.get_acquisition_state()
            if state == 'COMP':
                break
            if time.monotonic() > deadline:
                print(f'TIMEOUT (last state: {state})')
                return
            time.sleep(0.1)

        df = s.read_data([m.CH_VOLTAGE, m.CH_SECONDARY, m.CH_CURRENT])
        df.to_csv('tpt_inductor.csv', index=False)

        t  = df['time'].to_numpy() * 1e6
        vp = df['V_pri'].to_numpy()
        vs = df['V_sec'].to_numpy()
        i  = df['I'].to_numpy()

        # Compute B and H for BH plot
        Ae = CORE_DATABASE[CORE]['Ae']
        le = CORE_DATABASE[CORE]['le']
        dt = (t[1] - t[0]) * 1e-6
        B = np.cumsum(vs * dt) / (N2 * Ae)
        # Remove linear drift
        p = np.polyfit(t * 1e-6, B, 1)
        B = B - np.polyval(p, t * 1e-6)
        H = N1 * i / le

        fig, ax = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
        ax[0].plot(t, vp, color='C0', linewidth=1.2)
        ax[0].axhline(0, color='gray', linewidth=0.5, linestyle=':')
        ax[0].set_ylabel('V_pri (TP16-TP17) [V]', fontsize=11)
        ax[0].set_title(f'TPT capture — T26 3C90, N1=10 N2=1, V={VOLTAGE}V, f={FREQ/1e3:.0f}kHz, {N_PULSE} pulses', fontsize=12)
        ax[0].grid(alpha=0.3)

        ax[1].plot(t, vs, color='C1', linewidth=1.2)
        ax[1].axhline(0, color='gray', linewidth=0.5, linestyle=':')
        ax[1].set_ylabel('V_sec (TP18-TP19) [V]\n(differential, floating)', fontsize=11)
        ax[1].grid(alpha=0.3)

        ax[2].plot(t, i*1e3, color='C3', linewidth=1.2)
        ax[2].axhline(0, color='gray', linewidth=0.5, linestyle=':')
        ax[2].set_ylabel('I [mA]', fontsize=11)
        ax[2].set_xlabel('time [us]', fontsize=11)
        ax[2].grid(alpha=0.3)

        plt.tight_layout()
        out = os.path.abspath('tpt_inductor.png')
        plt.savefig(out, dpi=120)
        print(f'\nSaved: {out}')

        print(f'\nV_pri: {vp.min():+6.2f}..{vp.max():+6.2f} V  (mean {vp.mean():+5.2f})')
        print(f'V_sec: {vs.min():+6.3f}..{vs.max():+6.3f} V  (mean {vs.mean():+5.3f})')
        print(f'I:     {i.min()*1e3:+6.1f}..{i.max()*1e3:+6.1f} mA (mean {i.mean()*1e3:+5.1f})')
        print(f'B_peak (half swing): {(B.max()-B.min())/2*1e3:.2f} mT')
        print(f'H_peak (half swing): {(H.max()-H.min())/2:.2f} A/m')

        # Core loss energy over full burst
        Q = abs((N1/N2) * np.trapezoid(i * vs, t*1e-6))
        print(f'Q (full burst, N1/N2 * integral I*Vsec): {Q*1e6:.2f} uJ')
    finally:
        m.psu.disable_output(1); m.psu.disable_output(2)
        m.close()


if __name__ == '__main__':
    main()
