import os
#!/usr/bin/env python3
"""
Analyze debug waveform to verify pulse timing.
"""
import pandas as pd
import numpy as np

df = pd.read_csv(os.path.join(_TPT_ROOT, "debug_waveform.csv"))
t = df["time"].to_numpy()
v_pri = df["V_pri"].to_numpy()

# Find zero crossings to measure pulse width
threshold = 0.5  # V

# Find transitions
crossings = []
for i in range(1, len(v_pri)):
    if v_pri[i-1] < threshold and v_pri[i] >= threshold:
        crossings.append((t[i], 'rising'))
    elif v_pri[i-1] > threshold and v_pri[i] <= threshold:
        crossings.append((t[i], 'falling'))

print(f"Found {len(crossings)} threshold crossings")
print("\nFirst 10 crossings:")
for i, (time, direction) in enumerate(crossings[:10]):
    print(f"  {i}: {time*1e6:.2f} us - {direction}")

if len(crossings) >= 2:
    # Measure pulse widths
    widths = []
    for i in range(1, len(crossings)):
        if crossings[i][1] == 'falling' and crossings[i-1][1] == 'rising':
            widths.append(crossings[i][0] - crossings[i-1][0])
        elif crossings[i][1] == 'rising' and crossings[i-1][1] == 'falling':
            widths.append(crossings[i][0] - crossings[i-1][0])
    
    if widths:
        print(f"\nPulse widths (us):")
        for i, w in enumerate(widths[:8]):
            print(f"  Pulse {i}: {w*1e6:.2f} us")
        print(f"\n  Mean: {np.mean(widths)*1e6:.2f} us")
        print(f"  Std:  {np.std(widths)*1e6:.2f} us")
        
        expected = 10.0  # us
        print(f"\n  Expected: {expected:.2f} us")
        print(f"  Actual/Expected: {np.mean(widths)*1e6/expected:.2f}")

# Check V_sec waveform
v_sec = df["V_sec"].to_numpy()
print(f"\nV_sec statistics:")
print(f"  Mean: {v_sec.mean():.3f} V")
print(f"  Std:  {v_sec.std():.3f} V")
print(f"  Min:  {v_sec.min():.3f} V")
print(f"  Max:  {v_sec.max():.3f} V")
