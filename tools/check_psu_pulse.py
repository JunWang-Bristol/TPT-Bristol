#!/usr/bin/env python3
"""
Check PSU state and verify pulse with PSU off.
"""
import sys
import os
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from power_supply import PowerSupply
from oscilloscope import Oscilloscope
from board import Board
import json

config_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
with open(config_path, 'r') as f:
    cfg = json.load(f)

psu = PowerSupply.factory(cfg['power_supply'], cfg['power_supply_port'])
scope = Oscilloscope.factory(cfg['oscilloscope'], cfg['oscilloscope_port'])
board = Board.factory(cfg['board'], cfg['board_port'])

print("=" * 70)
print("  PSU State Check")
print("=" * 70)

# Check PSU voltage and current with output off
for ch in [1, 2, 3]:
    try:
        v = psu.get_measured_voltage(ch)
        i = psu.get_measured_current(ch)
        print(f"CH{ch}: V={v:.3f}V  I={i*1e3:.3f}mA")
    except Exception as e:
        print(f"CH{ch}: Error - {e}")

# Now do a pulse capture with PSU explicitly OFF
print("\n[1] Ensuring PSU is OFF...")
for ch in [1, 2, 3]:
    try:
        psu.disable_output(ch)
    except:
        pass

time.sleep(0.5)

print("\n[2] Checking PSU state after disable:")
for ch in [1, 2, 3]:
    try:
        v = psu.get_measured_voltage(ch)
        i = psu.get_measured_current(ch)
        print(f"CH{ch}: V={v:.3f}V  I={i*1e3:.3f}mA")
    except Exception as e:
        print(f"CH{ch}: Error - {e}")

# Single pulse with PSU OFF
board.clear_pulses()
board.add_pulse(10e-6)

scope.set_probe_scale(0, 10)
scope.set_channel_configuration(0, 2.5, "DC", 0.0)
scope.set_channel_label(0, "V_pri")
scope.set_rising_trigger(0, 0.5)
scope.set_number_samples(5000)
scope.set_sampling_time(100e-9)

scope.start_single_acquisition()
time.sleep(2)

print("\n[3] Firing single pulse with PSU OFF...")
board.run_pulses(1)

time.sleep(0.5)

deadline = time.monotonic() + 10
while True:
    state = scope.get_acquisition_state()
    if state == "COMP":
        break
    if time.monotonic() > deadline:
        print("ERROR: Scope timeout")
        break
    time.sleep(0.1)

df = scope.read_data([0])
print(f"\n[4] Captured: V_pri range [{df['V_pri'].min():.3f}, {df['V_pri'].max():.3f}] V")
print(f"    High (>0.5V) for {(df['V_pri'] > 0.5).sum() * 100e-9 * 1e6:.2f} us")

# Now with PSU ON at 5V
print("\n[5] Enabling PSU at 5V...")
psu.set_source_voltage(1, 5.0)
psu.set_current_limit(1, 1.0)
psu.enable_output(1)
time.sleep(0.5)

print(f"    PSU CH1: V={psu.get_measured_voltage(1):.3f}V  I={psu.get_measured_current(1)*1e3:.3f}mA")

scope.start_single_acquisition()
time.sleep(2)

print("\n[6] Firing single pulse with PSU ON...")
board.run_pulses(1)

time.sleep(0.5)

deadline = time.monotonic() + 10
while True:
    state = scope.get_acquisition_state()
    if state == "COMP":
        break
    if time.monotonic() > deadline:
        print("ERROR: Scope timeout")
        break
    time.sleep(0.1)

df = scope.read_data([0])
print(f"\n[7] Captured: V_pri range [{df['V_pri'].min():.3f}, {df['V_pri'].max():.3f}] V")

# Analyze transitions
v = df['V_pri'].to_numpy()
t = df['time'].to_numpy()
transitions = []
for i in range(1, len(v)):
    if v[i-1] < 0.5 and v[i] >= 0.5:
        transitions.append((t[i], 'rise'))
    elif v[i-1] >= 0.5 and v[i] < 0.5:
        transitions.append((t[i], 'fall'))

print(f"    Transitions: {len(transitions)}")
for tm, dir in transitions[:10]:
    print(f"      {tm*1e6:.2f} us - {dir}")

psu.disable_output(1)
psu.close()
scope.close()
board.close()
print("\nDone.")
