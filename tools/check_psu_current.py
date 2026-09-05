#!/usr/bin/env python3
"""
Diagnostic: Check if current is actually flowing using PSU current reading.
"""
import sys
import os
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from power_supply import PowerSupply

config_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')

print("=" * 70)
print("  PSU Current Check")
print("=" * 70)

with open(config_path, 'r') as f:
    import json
    cfg = json.load(f)

psu = PowerSupply.factory(cfg['power_supply'], cfg['power_supply_port'])

print("\n[1] PSU connected")

# Check with output off
print("\n[2] Checking current with output OFF...")
v_off = psu.get_measured_voltage(1)
i_off = psu.get_measured_current(1)
print(f"    Voltage: {v_off:.3f} V")
print(f"    Current: {i_off*1e3:.3f} mA")

# Apply voltage
print("\n[3] Applying 2V DC...")
psu.set_source_voltage(1, 2.0)
psu.set_current_limit(1, 1.0)
psu.enable_output(1)
time.sleep(1.0)

print("\n[4] Checking current with 2V DC applied...")
v_on = psu.get_measured_voltage(1)
i_on = psu.get_measured_current(1)
print(f"    Voltage: {v_on:.3f} V")
print(f"    Current: {i_on*1e3:.3f} mA")

psu.disable_output(1)

print("\n[5] Analysis:")
if abs(i_on) < 0.001:
    print("    WARNING: PSU shows essentially zero current!")
    print("    This means the circuit is OPEN.")
    print("    Possible causes:")
    print("    - DUT (inductor) not connected between FIX_A and FIX_B")
    print("    - Fuse blown (F1 or F2)")
    print("    - Broken wire or bad connection")
    print("    - MOSFET not conducting")
else:
    print(f"    PSU current: {i_on*1e3:.2f} mA")
    print("    Current IS flowing.")
    print("    The current probe or scope channel is the problem.")

print("\nDone.")
