#!/usr/bin/env python3
"""Empirical pin-to-FIX_A test.

Sends very long pulses (so we can see DC steady states) and reads V_pri.
This shows whether PB10 alone is driving the gate, or PB4 contributes anything,
and whether FIX_A actually goes anywhere besides 0..+V_top under pulse control.
"""
import os, sys, time
_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))
from tpt import CoreLossMeasurement
import numpy as np

VOLTAGE = 10.0
T_LONG  = 100e-6   # 100 us per pulse — long enough to see DC steady state at scope

def configure_capture(meas, n_pulses, dt_per_sample):
    s = meas.scope
    n_samples = 2000
    s.set_probe_scale(meas.CH_VOLTAGE, meas.input_voltage_probe_scale)
    s.set_probe_scale(meas.CH_CURRENT, meas.current_probe_scale)
    s.set_channel_configuration(meas.CH_VOLTAGE, 5.0, 'DC', 0.0)
    s.set_channel_configuration(meas.CH_CURRENT, 1.0, 'DC', 0.0)
    s.set_channel_label(meas.CH_VOLTAGE, 'V_pri')
    s.set_channel_label(meas.CH_CURRENT, 'I')
    s.set_rising_trigger(meas.CH_VOLTAGE, 1.0)
    s.set_number_samples(n_samples)
    s.set_sampling_time(dt_per_sample)

def fire_and_capture(meas, durations):
    meas.board.clear_pulses()
    for d in durations:
        meas.board.add_pulse(d)
    meas.scope.start_single_acquisition()
    time.sleep(1.5)
    meas.board.run_pulses(1)
    deadline = time.monotonic()+10
    while True:
        if meas.scope.get_acquisition_state() == 'COMP': break
        if time.monotonic() > deadline: return None
        time.sleep(0.1)
    return meas.scope.read_data([meas.CH_VOLTAGE, meas.CH_CURRENT])

def stats(df, t_start_us, t_end_us):
    if df is None: return 'no data'
    t = df['time'].to_numpy()*1e6
    mask = (t >= t_start_us) & (t < t_end_us)
    if mask.sum() == 0: return f'no samples in {t_start_us}..{t_end_us}us'
    v = df['V_pri'].to_numpy()[mask]
    i = df['I'].to_numpy()[mask]
    return f'V_pri mean={v.mean():+6.3f} min={v.min():+6.3f} max={v.max():+6.3f}   I mean={i.mean():+6.4f}'

def main():
    config = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    meas = CoreLossMeasurement.from_config(config)
    try:
        # PSU on
        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     VOLTAGE)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, VOLTAGE)
        meas.psu.set_current_limit (meas.PSU_CHANNEL,     3.0)
        meas.psu.set_current_limit (meas.PSU_CHANNEL_NEG, 3.0)
        meas.psu.enable_output(meas.PSU_CHANNEL)
        meas.psu.enable_output(meas.PSU_CHANNEL_NEG)
        time.sleep(0.5)

        configure_capture(meas, 4, 200e-9)

        # Test 1: single LONG positive (even index 0) — PB10=HIGH, PB4=LOW for 100us
        print('\n[1] One long EVEN pulse (PB10 HIGH, PB4 LOW for 100us)')
        df = fire_and_capture(meas, [T_LONG])
        if df is not None: df.to_csv('pins_even.csv', index=False)
        print('   t=0..100us  :', stats(df, 0, 100))
        print('   t=100..400us:', stats(df, 100, 400))

        # Test 2: TWO pulses — first EVEN (PB10), second ODD (PB4)
        print('\n[2] Two long pulses: EVEN then ODD')
        df = fire_and_capture(meas, [T_LONG, T_LONG])
        if df is not None: df.to_csv('pins_even_odd.csv', index=False)
        print('   t=0..100us:  EVEN pulse   :', stats(df, 0, 100))
        print('   t=100..200us: ODD pulse   :', stats(df, 100, 200))
        print('   t=200..400us: after both  :', stats(df, 200, 400))

        # Test 3: single LONG odd pulse — should ONLY happen if we send 2 pulses
        # (firmware always starts at index 0 = even). Send one zero-length (skip)?
        # Easier: send [tiny, T_LONG] so the first pulse is brief and the 100us is the ODD one.
        print('\n[3] Two pulses: tiny EVEN (1us) then long ODD (100us)')
        df = fire_and_capture(meas, [1e-6, T_LONG])
        if df is not None: df.to_csv('pins_tiny_odd.csv', index=False)
        print('   t=0..1us:   tiny even  :', stats(df, 0, 1))
        print('   t=1..101us: long odd   :', stats(df, 1, 101))
        print('   t=101..400us: after    :', stats(df, 101, 400))
    finally:
        meas.psu.disable_output(meas.PSU_CHANNEL)
        meas.psu.disable_output(meas.PSU_CHANNEL_NEG)
        meas.close()

if __name__ == '__main__':
    main()
