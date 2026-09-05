import os
#!/usr/bin/env python3
"""
Detailed waveform analysis.
"""
import pandas as pd
import numpy as np

df = pd.read_csv(os.path.join(_TPT_ROOT, "debug_waveform.csv"))
t = df["time"].to_numpy()
v_pri = df["V_pri"].to_numpy()
v_sec = df["V_sec"].to_numpy()
current = df["Current"].to_numpy()

print(f"Total time: {t[-1]*1e6:.1f} us")
print(f"Sample interval: {(t[1]-t[0])*1e9:.1f} ns")
print(f"Total samples: {len(t)}")

print(f"\nV_pri range: [{v_pri.min():.2f}, {v_pri.max():.2f}] V")
print(f"V_sec range: [{v_sec.min():.2f}, {v_sec.max():.2f}] V")
print(f"Current range: [{current.min():.6f}, {current.max():.6f}] A")

# Show waveform at key points
print("\n--- V_pri waveform (first 100 samples) ---")
for i in range(min(100, len(t))):
    if i % 10 == 0:
        print(f"  t={t[i]*1e6:6.2f}us: V_pri={v_pri[i]:7.3f}V  V_sec={v_sec[i]:7.3f}V  I={current[i]*1e6:8.3f}uA")

# Find where V_pri is positive vs negative
positive_mask = v_pri > 0
negative_mask = v_pri < 0

print(f"\nV_pri positive for: {positive_mask.sum()} samples = {positive_mask.sum() * (t[1]-t[0])*1e6:.2f} us")
print(f"V_pri negative for: {negative_mask.sum()} samples = {negative_mask.sum() * (t[1]-t[0])*1e6:.2f} us")

# Find transitions
transitions = []
for i in range(1, len(v_pri)):
    if v_pri[i-1] < 0 and v_pri[i] >= 0:
        transitions.append((t[i], '0->+', v_pri[i]))
    elif v_pri[i-1] >= 0 and v_pri[i] < 0:
        transitions.append((t[i], '+>0-', v_pri[i]))

print(f"\nFound {len(transitions)} zero crossings:")
for i, (time, direction, val) in enumerate(transitions[:20]):
    print(f"  {i}: {time*1e6:7.2f} us  {direction}  ({val:.3f}V)")

# Measure time between transitions
if len(transitions) > 1:
    print("\n--- Time intervals ---")
    for i in range(1, len(transitions)):
        dt = transitions[i][0] - transitions[i-1][0]
        print(f"  Interval {i}: {dt*1e6:.2f} us ({transitions[i-1][1]} to {transitions[i][1]})")
