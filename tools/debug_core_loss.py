#!/usr/bin/env python3
"""Run the TPT core-loss measurement (Wang et al. 2020 IECON).

Executes the full three-stage TPT procedure via CoreLossMeasurement.measure_core_loss():
  Phase 1 — flux targeting (voltage iteration to hit B_peak)
  Phase 2 — volt-second balancing (negative-rail trim for closed BH loop)
  Phase 3 — final capture and core-loss extraction
"""

import os
import sys

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement, theoretical_inductance

CORE      = "T26"
MATERIAL  = "3C90"
N1        = 10        # primary turns
N2        = 10        # secondary (sense) turns — matches current DUT wiring
FREQUENCY = 50_000    # Hz
VOLTAGE   = 5.0       # V (initial PSU rail; flux-targeting will adjust)
DC_BIAS   = 0.0       # A (no premagnetisation for first run)
TARGET_B  = None      # T (set e.g. 0.05 to engage flux targeting)

L_henry = theoretical_inductance(CORE, MATERIAL, N1)

config = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
meas = CoreLossMeasurement.from_config(config)

print(f"Core={CORE} {MATERIAL}  N1={N1} N2={N2}  V={VOLTAGE} f={FREQUENCY/1e3:.0f}kHz")
print(f"L_theory = {L_henry*1e6:.1f} uH   dI_expected ~ {VOLTAGE/(2*FREQUENCY*L_henry)*1e3:.0f} mA pp")

try:
    result = meas.measure_core_loss(
        voltage=VOLTAGE,
        frequency=FREQUENCY,
        N1=N1, N2=N2,
        core_name=CORE,
        L_henry=L_henry,
        dc_bias_A=DC_BIAS,
        target_B_peak_T=TARGET_B,
        balance=True,
        plot=False,
        save_csv="tpt_waveform.csv",
    )
finally:
    meas._disable_psu()
    meas.close()

if result is None:
    print("\nMeasurement failed — see logs above.")
    sys.exit(1)

print("\n=== RESULT ===")
print(f"  Q_cycle    = {result['Q_cycle']*1e6:.3f} uJ/cycle")
print(f"  P_core     = {result['P_core']*1e3:.3f} mW")
print(f"  B_peak     = {result['B_peak']*1e3:.2f} mT")
print(f"  H_peak     = {result['H_peak']:.2f} A/m")
print(f"  P_density  = {result['P_density']/1e3:.2f} kW/m³")
