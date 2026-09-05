#!/usr/bin/env python3
"""
Verify single pulse timing.
"""
import sys
import os
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement

config_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')

print("=" * 70)
print("  Single Pulse Timing Verification")
print("=" * 70)

meas = CoreLossMeasurement.from_config(config_path)

# Clear and add a single 10us pulse
meas.board.clear_pulses()
meas.board.add_pulse(10e-6)

print(f"\n[1] Added 1 pulse of 10us")
print(f"    Board minimum period: {meas.board.get_minimum_period()*1e9:.0f} ns")

# Read back pulses
pulses = meas.board.read_pulses()
print(f"    Pulses stored: {pulses}")
print(f"    Sum: {sum(pulses)*1e6:.1f} us")

# Configure scope for single pulse capture
scope = meas.scope
scope.set_probe_scale(0, 10)
scope.set_channel_configuration(0, 2.5, "DC", 0.0)
scope.set_channel_label(0, "V_pri")
scope.set_rising_trigger(0, 0.5)
scope.set_number_samples(5000)
scope.set_sampling_time(100e-9)  # 100ns/sample = 500us total

print(f"\n[2] Scope configured: 500us capture at 100ns/sample")

# Arm scope and fire
scope.start_single_acquisition()
time.sleep(2)

print(f"\n[3] Firing single pulse...")
meas.board.run_pulses(1)

# Wait for completion
time.sleep(0.5)

# Poll for completion
deadline = time.monotonic() + 10
while True:
    state = scope.get_acquisition_state()
    if state == "COMP":
        break
    if time.monotonic() > deadline:
        print("ERROR: Scope timeout")
        break
    time.sleep(0.1)

# Read data
df = scope.read_data([0])

print(f"\n[4] Data captured: {len(df)} samples")
print(f"    V_pri range: [{df['V_pri'].min():.3f}, {df['V_pri'].max():.3f}] V")

# Save for analysis
df.to_csv("single_pulse.csv", index=False)
print(f"    Saved: single_pulse.csv")

# Simple analysis
t = df["time"].to_numpy()
v = df["V_pri"].to_numpy()

# Find where voltage is high
threshold = 1.0  # V
high_mask = v > threshold
low_mask = v < -threshold

print(f"\n[5] Analysis:")
print(f"    High (>1V) for: {high_mask.sum()} samples = {high_mask.sum() * 100e-9 * 1e6:.2f} us")
print(f"    Low (<-1V) for: {low_mask.sum()} samples = {low_mask.sum() * 100e-9 * 1e6:.2f} us")

# Find transitions
transitions = []
for i in range(1, len(v)):
    if v[i-1] < threshold and v[i] >= threshold:
        transitions.append((t[i], 'low->high'))
    elif v[i-1] >= threshold and v[i] < threshold:
        transitions.append((t[i], 'high->low'))

print(f"    Transitions found: {len(transitions)}")
for i, (tm, dir) in enumerate(transitions[:10]):
    print(f"      {i}: {tm*1e6:.2f} us - {dir}")

# Check pulse count
time.sleep(0.1)
count = meas.board.count_trains()
print(f"\n[6] Pulse train count: {count}")

meas.close()
print("\nDone.")
