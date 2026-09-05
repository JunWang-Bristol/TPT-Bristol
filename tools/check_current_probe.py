#!/usr/bin/env python3
"""
Diagnostic: Check if current probe is working.
Applies DC voltage and reads current channel.
"""
import sys
import os
import time
import json

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from power_supply import PowerSupply
from oscilloscope import Oscilloscope
from board import Board

config_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')

print("=" * 70)
print("  Current Probe Diagnostic")
print("=" * 70)

with open(config_path, 'r') as f:
    cfg = json.load(f)

psu = PowerSupply.factory(cfg['power_supply'], cfg['power_supply_port'])
scope = Oscilloscope.factory(cfg['oscilloscope'], cfg['oscilloscope_port'])
board = Board.factory(cfg['board'], cfg['board_port'])

print("\n[1] Hardware connected")

# Configure scope for DC measurement on current channel
scope.set_probe_scale(2, cfg.get('current_probe_scale', 0.1))
scope.set_channel_configuration(2, 0.1, "DC", 0.0)
scope.set_channel_label(2, "Current")
scope.set_number_samples(1000)
scope.set_sampling_time(1e-6)

# Read current with PSU OFF
print("\n[2] Reading current channel with PSU OFF...")
scope.start_single_acquisition()
time.sleep(0.5)
df = scope.read_data([2])
current_off = df['Current'].to_numpy()
print(f"    Mean: {current_off.mean():.6f} A")
print(f"    Std:  {current_off.std():.6f} A")
print(f"    Min:  {current_off.min():.6f} A")
print(f"    Max:  {current_off.max():.6f} A")

# Apply small DC voltage
print("\n[3] Applying 2V DC...")
psu.set_source_voltage(1, 2.0)
psu.set_current_limit(1, 1.0)
psu.enable_output(1)
time.sleep(1.0)

print("\n[4] Reading current channel with 2V DC...")
scope.start_single_acquisition()
time.sleep(0.5)
df = scope.read_data([2])
current_on = df['Current'].to_numpy()
print(f"    Mean: {current_on.mean():.6f} A")
print(f"    Std:  {current_on.std():.6f} A")
print(f"    Min:  {current_on.min():.6f} A")
print(f"    Max:  {current_on.max():.6f} A")

psu.disable_output(1)

print("\n[5] Analysis:")
if abs(current_on.mean() - current_off.mean()) < 0.001:
    print("    WARNING: No change in current reading with voltage applied!")
    print("    Possible causes:")
    print("    - Current probe battery is dead")
    print("    - Current probe not clamped on correct wire")
    print("    - DUT is open circuit (not connected)")
    print("    - Fuse blown")
else:
    print(f"    Current changed by {(current_on.mean() - current_off.mean())*1e3:.2f} mA")
    print("    Current probe appears to be working.")

psu.close()
scope.close()
board.close()
print("\nDone.")
