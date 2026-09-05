#!/usr/bin/env python3
"""V_pri probe diagnostic.

Fires the same pulse train twice — once with PSU disabled, once with PSU enabled
at 10 V on both rails — and reports V_pri statistics. Use this to confirm:

  1. With PSU OFF  : V_pri should sit near 0 V (no rails to switch).
  2. With PSU ON   : V_pri should swing close to +10 V / -10 V (clean ±10 V square wave).

If ON looks like OFF, the half-bridge isn't switching (gate driver, enable line,
or wiring problem). If V_pri swings only +1 V instead of ±10 V, the probe is on
the wrong node or has a bad DC offset.
"""

import os
import sys
import time

import numpy as np

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement


VOLTAGE   = 10.0
FREQUENCY = 50_000
N_PULSES  = 8     # 1 stage-I + 7 alternating half-periods


def fire_and_capture(meas, T_half):
    scope = meas.scope
    board = meas.board

    board.clear_pulses()
    for _ in range(N_PULSES):
        board.add_pulse(T_half)

    T_total = N_PULSES * T_half
    n_samples = 2000
    dt = (T_total * 1.5) / n_samples
    scope.set_number_samples(n_samples)
    scope.set_sampling_time(dt)

    scope.set_probe_scale(meas.CH_VOLTAGE, meas.input_voltage_probe_scale)
    scope.set_probe_scale(meas.CH_CURRENT, meas.current_probe_scale)
    scope.set_channel_configuration(meas.CH_VOLTAGE, 5.0, 'DC', 0.0)   # ±25 V range with 10:1
    scope.set_channel_configuration(meas.CH_CURRENT, 1.0, 'DC', 0.0)
    scope.set_channel_label(meas.CH_VOLTAGE, 'V_pri')
    scope.set_channel_label(meas.CH_CURRENT, 'Current')
    scope.set_rising_trigger(meas.CH_VOLTAGE, 1.0)

    scope.start_single_acquisition()
    time.sleep(1.5)
    board.run_pulses(1)

    deadline = time.monotonic() + 8
    while True:
        if scope.get_acquisition_state() == 'COMP':
            break
        if time.monotonic() > deadline:
            return None
        time.sleep(0.1)

    return scope.read_data([meas.CH_VOLTAGE, meas.CH_CURRENT])


def report(label, df):
    if df is None:
        print(f'  {label}: capture timed out — pulses may not have fired')
        return
    v = df['V_pri'].to_numpy()
    i = df['Current'].to_numpy()
    print(f'  {label}:')
    print(f'    V_pri: mean={v.mean():+.3f}  min={v.min():+.3f}  max={v.max():+.3f}  pp={v.max()-v.min():.2f}')
    print(f'           |V_pri|>3V {(np.abs(v)>3).mean()*100:5.1f}% of samples')
    print(f'           |V_pri|>1V {(np.abs(v)>1).mean()*100:5.1f}% of samples')
    print(f'    I:    min={i.min():+.4f}  max={i.max():+.4f}  pp={i.max()-i.min():.4f} A')


def main():
    config = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    meas = CoreLossMeasurement.from_config(config)

    T_half = 1.0 / (2.0 * FREQUENCY)
    print(f'Pulse train: {N_PULSES} pulses x {T_half*1e6:.1f} us = {N_PULSES*T_half*1e6:.0f} us total')

    try:
        # Belt and braces — make sure both rails are off.
        meas.psu.disable_output(meas.PSU_CHANNEL)
        meas.psu.disable_output(meas.PSU_CHANNEL_NEG)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     0.0)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, 0.0)
        time.sleep(0.5)

        print('\n[1] PSU OFF — pulses fired with no rails. V_pri should be ~0 V.')
        df_off = fire_and_capture(meas, T_half)
        report('OFF', df_off)
        if df_off is not None:
            df_off.to_csv('vpri_off.csv', index=False)

        print(f'\n[2] PSU ON  ({VOLTAGE:.1f} V on CH1 and CH2). V_pri should swing close to +-{VOLTAGE:.0f} V.')
        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     VOLTAGE)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, VOLTAGE)
        meas.psu.set_current_limit (meas.PSU_CHANNEL,     3.0)
        meas.psu.set_current_limit (meas.PSU_CHANNEL_NEG, 3.0)
        meas.psu.enable_output(meas.PSU_CHANNEL)
        meas.psu.enable_output(meas.PSU_CHANNEL_NEG)
        time.sleep(0.5)

        v_meas_1 = meas.psu.get_measured_voltage(1)
        v_meas_2 = meas.psu.get_measured_voltage(2)
        i_meas_1 = meas.psu.get_measured_current(1)
        i_meas_2 = meas.psu.get_measured_current(2)
        print(f'  PSU readback: CH1 V={v_meas_1:.2f} V I={i_meas_1*1e3:.1f} mA   CH2 V={v_meas_2:.2f} V I={i_meas_2*1e3:.1f} mA')

        df_on = fire_and_capture(meas, T_half)
        report('ON ', df_on)
        if df_on is not None:
            df_on.to_csv('vpri_on.csv', index=False)

        print('\n=== Verdict ===')
        if df_off is not None and df_on is not None:
            v_off_rms = float(np.std(df_off['V_pri']))
            v_on_pp  = float(df_on['V_pri'].max() - df_on['V_pri'].min())
            print(f'  V_pri stddev OFF: {v_off_rms:.3f} V    V_pri pp ON: {v_on_pp:.2f} V')
            if v_on_pp < 5.0:
                print(f'  WARNING: ON pp swing < 5 V — half-bridge is not driving ±{VOLTAGE} V across the inductor.')
                print('  Check probe location (must be across DUT, not on a rail), gate driver, and wiring.')
            elif v_on_pp > 0.5 * VOLTAGE * 2 * 0.8:
                print(f'  OK: ON pp swing {v_on_pp:.1f} V is within 20% of 2*VOLTAGE — probe and half-bridge look good.')
    finally:
        meas.psu.disable_output(meas.PSU_CHANNEL)
        meas.psu.disable_output(meas.PSU_CHANNEL_NEG)
        meas.close()


if __name__ == '__main__':
    main()
