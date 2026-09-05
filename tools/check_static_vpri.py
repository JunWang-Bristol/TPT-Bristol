#!/usr/bin/env python3
"""Static V_pri diagnostic — fires NO pulses, just reads the scope.

If the V_pri probe is correctly on TP16 (FIX_A) with GND clip on TP11 / TP12,
and the BK PSU is wired to the board's J9 V+ / Com terminals, this should show:

  Pulses OFF, PSU OFF : V_pri ~ 0 V (everything floats / DUT pulls FIX_A to GND)
  Pulses OFF, PSU ON  : V_pri ~ 0 V (both FETs off, FIX_A floats — DUT pulls to GND)
  Pulses ON,  PSU ON  : V_pri swings +-10 V  ← the only case that should be active

If V_pri reads 5-10 V even with no pulses firing, the probe is on a static rail
(e.g. gate-driver Vcc) and is not measuring the half-bridge output.
"""

import os
import sys
import time

import numpy as np

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement


def capture_static(meas, label):
    scope = meas.scope
    scope.set_probe_scale(meas.CH_VOLTAGE,   meas.input_voltage_probe_scale)
    scope.set_probe_scale(meas.CH_SECONDARY, meas.output_voltage_probe_scale)
    scope.set_channel_configuration(meas.CH_VOLTAGE,   5.0, 'DC', 0.0)
    scope.set_channel_configuration(meas.CH_SECONDARY, 1.0, 'DC', 0.0)
    scope.set_channel_label(meas.CH_VOLTAGE,   'V_pri')
    scope.set_channel_label(meas.CH_SECONDARY, 'V_sec')
    # No-pulse capture: simple time trigger via a low rising edge that won't
    # actually fire — fall through to AUTO. Use auto by setting the trigger
    # outside the channel's range.
    scope.set_rising_trigger(meas.CH_VOLTAGE, -50.0)  # never triggers
    scope.set_number_samples(1000)
    scope.set_sampling_time(1e-5)  # 10us/sample = 10ms total

    scope.start_single_acquisition()
    time.sleep(2.0)  # wait — should auto-trigger if scope supports it

    # Force-stop and read whatever we have
    deadline = time.monotonic() + 3
    while True:
        s = scope.get_acquisition_state()
        if s == 'COMP':
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.1)

    df = scope.read_data([meas.CH_VOLTAGE, meas.CH_SECONDARY])
    if df is None or df.empty:
        print(f'  {label}: no data')
        return
    v = df['V_pri'].to_numpy()
    s = df['V_sec'].to_numpy()
    print(f'  {label}:')
    print(f'    V_pri: mean={v.mean():+6.3f}  std={v.std():.3f}  min={v.min():+6.3f}  max={v.max():+6.3f}')
    print(f'    V_sec: mean={s.mean():+6.3f}  std={s.std():.3f}  min={s.min():+6.3f}  max={s.max():+6.3f}')


def main():
    config = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    meas = CoreLossMeasurement.from_config(config)

    try:
        # Disable PSU
        meas.psu.disable_output(meas.PSU_CHANNEL)
        meas.psu.disable_output(meas.PSU_CHANNEL_NEG)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     0.0)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, 0.0)
        time.sleep(0.5)

        # Make sure no pulses are queued
        meas.board.clear_pulses()

        print('[1] PSU OFF, no pulses (board completely idle)')
        capture_static(meas, 'idle, no PSU')

        meas.psu.set_source_voltage(meas.PSU_CHANNEL,     10.0)
        meas.psu.set_source_voltage(meas.PSU_CHANNEL_NEG, 10.0)
        meas.psu.set_current_limit (meas.PSU_CHANNEL,     3.0)
        meas.psu.set_current_limit (meas.PSU_CHANNEL_NEG, 3.0)
        meas.psu.enable_output(meas.PSU_CHANNEL)
        meas.psu.enable_output(meas.PSU_CHANNEL_NEG)
        time.sleep(0.5)

        v1 = meas.psu.get_measured_voltage(1)
        v2 = meas.psu.get_measured_voltage(2)
        i1 = meas.psu.get_measured_current(1)
        i2 = meas.psu.get_measured_current(2)
        print(f'\n  PSU readback at terminals: CH1={v1:.2f}V/{i1*1e3:.1f}mA   CH2={v2:.2f}V/{i2*1e3:.1f}mA')
        print(f'  (If CH1 > 0V but CH1 current is ~0, the rail wire to the board is OPEN — check J9 V+.)')
        print(f'  (Typical board idle current with both FETs off: < 1 mA leakage.)')

        print('\n[2] PSU ON @ 10V, no pulses (FETs both off, board idle)')
        capture_static(meas, 'idle with PSU on')

        print('\n=== Diagnosis ===')
        print('  Both readings near 0 V                 -> probe is on FIX_A correctly.')
        print('                                            Then run debug_core_loss.py.')
        print('  Both readings non-zero (e.g. ~5-10 V)  -> probe is on a static rail (Vcc/etc),')
        print('                                            NOT on FIX_A. Move the tip.')
        print('  Idle-with-PSU > Idle-no-PSU            -> probe IS on FIX_A but rail is')
        print('                                            leaking somewhere; suspect FET short.')
    finally:
        meas.psu.disable_output(meas.PSU_CHANNEL)
        meas.psu.disable_output(meas.PSU_CHANNEL_NEG)
        meas.close()


if __name__ == '__main__':
    main()
