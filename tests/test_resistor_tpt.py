#!/usr/bin/env python3
"""
TPT simulacrum with a 220 ohm resistor
========================================
Runs the same TPT pulse structure as tpt.py CoreLossMeasurement
but with a resistor as DUT, measuring:
  - Ch A: voltage at FIX_A (midpoint)
  - Ch C: current through the resistor (P6021 at 2 mV/mA)

This validates:
  1. The 8-pulse TPT structure [T_stage1, T_half x 7]
  2. Scope arming, triggering, and capture flow
  3. Voltage/current correlation and pulse timing
"""

import sys
import os
import json
import time
import numpy as np
import matplotlib.pyplot as plt

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../src/')))

from boards.ST import NUCLEO_H503RB
from oscilloscopes.PicoScope import PicoScope2408B
from power_supplies.BK import BK9129B

# --- Configuration -----------------------------------------------------------
# Ports come from hardware_configuration.json so this script follows the rig
# instead of drifting out of date with hardcoded COM numbers.
_CONFIG_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, "hardware_configuration.json"))
with open(_CONFIG_PATH) as _f:
    _CONFIG = json.load(_f)

BOARD_PORT = _CONFIG["board_port"]
PSU_PORT = _CONFIG["power_supply_port"]

PSU_VOLTAGE = 15.0       # V
PSU_CURRENT_LIMIT = 0.5  # A
PSU_CHANNEL = 1

# TPT parameters — same structure as CoreLossMeasurement
FREQUENCY = 50_000       # Hz
T_HALF = 1.0 / (2.0 * FREQUENCY)  # 10 us
DC_BIAS_A = 0.0          # no DC bias for resistor
T_STAGE1 = T_HALF        # no bias -> T_stage1 = T_half
NUM_STEADY_PULSES = 7    # total = [T_stage1] + [T_half x 7] = 8 half-periods

# Scope
CH_VOLTAGE = "A"
CH_CURRENT = "C"
V_RANGE = 20.0           # V
I_RANGE = 1.0            # V

# P6021 at 2 mV/mA = 2 V/A -> probe_scale = 1/2 = 0.5
CURRENT_PROBE_SCALE = 0.5


def make_tpt_pulse_train(T_stage1, T_half, n_steady=7):
    """Replicate CoreLossMeasurement._make_tpt_pulse_train."""
    return [T_stage1] + [T_half] * n_steady


def main():
    print("=" * 60)
    print("  TPT Simulacrum - 220 ohm resistor")
    print("  Pulse structure: [T_stage1] + [T_half x 7]")
    print(f"  Frequency: {FREQUENCY/1e3:.0f} kHz  T_half: {T_HALF*1e6:.0f} us")
    print("=" * 60)

    psu = None
    board = None
    scope = None

    try:
        # -- Connect hardware -------------------------------------------------
        print("\n[1] Connecting hardware...")
        psu = BK9129B(PSU_PORT)
        board = NUCLEO_H503RB(BOARD_PORT)
        idn = board.get_identification()
        print(f"    Board: {idn.strip()}")
        scope = PicoScope2408B(None)
        print("    All connected.")

        # -- Configure PSU -----------------------------------------------------
        print(f"\n[2] PSU: ch {PSU_CHANNEL} = {PSU_VOLTAGE} V, "
              f"I_limit = {PSU_CURRENT_LIMIT} A")
        psu.set_source_voltage(PSU_CHANNEL, PSU_VOLTAGE)
        psu.set_current_limit(PSU_CHANNEL, PSU_CURRENT_LIMIT)
        psu.enable_output(PSU_CHANNEL)
        time.sleep(0.5)
        print(f"    Measured: {psu.get_measured_voltage(PSU_CHANNEL):.2f} V")

        # -- Configure scope ---------------------------------------------------
        print("\n[3] Configuring scope...")
        scope.set_channel_configuration(CH_VOLTAGE, V_RANGE, 0, 0.0)
        scope.set_channel_label(CH_VOLTAGE, "Voltage")

        scope.set_channel_configuration(CH_CURRENT, I_RANGE, 0, 0.0)
        scope.set_channel_label(CH_CURRENT, "Current")
        scope.set_probe_scale(CH_CURRENT, CURRENT_PROBE_SCALE)

        trigger_level = PSU_VOLTAGE * 0.5
        scope.set_rising_trigger(CH_VOLTAGE, trigger_level, delayed_samples=0, timeout=10000)

        # Build pulse train
        pulses = make_tpt_pulse_train(T_STAGE1, T_HALF, NUM_STEADY_PULSES)
        T_total = sum(pulses)

        # Timing: 1.5x total for headroom, 100 samples per half-period
        total_acq = T_total * 1.5
        n_samples = max(1000, int(total_acq / (T_HALF / 100.0)))
        dt = total_acq / n_samples
        scope.set_number_samples(n_samples)
        scope.set_sampling_time(dt)
        real_dt = scope.get_sampling_time()

        print(f"    Trigger: {trigger_level:.1f} V rising on ch A")
        print(f"    Samples: {n_samples}  dt: {real_dt*1e9:.0f} ns  "
              f"window: {n_samples*real_dt*1e6:.0f} us")

        # -- Load TPT pulse train ----------------------------------------------
        print(f"\n[4] Loading TPT pulse train ({len(pulses)} half-periods):")
        for i, p in enumerate(pulses):
            label = "T_stage1" if i == 0 else f"T_half"
            print(f"    Pulse {i}: {p*1e6:.1f} us  ({label})")
        board.clear_pulses()
        for p in pulses:
            board.add_pulse(p)
        readback = board.read_pulses()
        print(f"    Loaded {len(readback)} pulses on MCU")

        # -- Arm scope, fire, capture ------------------------------------------
        print("\n[5] Arming scope and firing TPT pulse train...")
        acq_time = n_samples * real_dt
        scope.set_acquisition_time(acq_time)
        scope.start_single_acquisition()
        time.sleep(2)

        board.run_pulses(1)
        print("    Pulses sent!")

        deadline = time.monotonic() + 15.0
        while True:
            state = scope.get_acquisition_state()
            if state == "COMP":
                break
            if time.monotonic() > deadline:
                print("    TIMEOUT!")
                return 1
            time.sleep(0.1)
        print("    Acquisition complete!")

        # -- Read data ---------------------------------------------------------
        print("\n[6] Reading waveform data...")
        df = scope.read_data([CH_VOLTAGE, CH_CURRENT])

        t = df["time"].to_numpy()
        V = df["Voltage"].to_numpy()
        I = df["Current"].to_numpy()

        print(f"    Voltage: [{V.min():.3f}, {V.max():.3f}] V  pk-pk={np.ptp(V):.3f} V")
        print(f"    Current: [{I.min():.4f}, {I.max():.4f}] A  pk-pk={np.ptp(I)*1e3:.2f} mA")

        # -- Analyze: find pulse edges -----------------------------------------
        print("\n[7] Analyzing TPT structure...")
        v_thresh = PSU_VOLTAGE * 0.5
        above = V > v_thresh
        rising = np.where(~above[:-1] & above[1:])[0]
        falling = np.where(above[:-1] & ~above[1:])[0]

        # Filter out ringing spikes: keep pulses wider than 30% of T_half
        min_samp = int(T_HALF * 0.3 / real_dt)
        valid_pulses = []
        for r in rising:
            falls_after = falling[falling > r]
            if len(falls_after) == 0:
                continue
            f = falls_after[0]
            if f - r >= min_samp:
                valid_pulses.append((r, f))

        print(f"    Detected {len(valid_pulses)} positive half-periods:")
        for i, (r, f) in enumerate(valid_pulses):
            width = (t[f] - t[r]) * 1e6
            V_avg = np.mean(V[r:f])
            I_avg = np.mean(I[r:f]) * 1e3
            print(f"      Pulse {i}: {t[r]*1e6:.1f}-{t[f]*1e6:.1f} us  "
                  f"width={width:.1f} us  V_avg={V_avg:.2f} V  I_avg={I_avg:.2f} mA")

        # Identify the "last cycle" (measurement window) — same as tpt.py
        if len(valid_pulses) >= 1:
            last_rise, last_fall = valid_pulses[-1]
            # Find end of the negative half after the last positive pulse
            next_rises = rising[rising > last_fall]
            i_end = int(next_rises[0]) if len(next_rises) > 0 else len(t) - 1
            i_start = int(last_rise)

            t_win = t[i_start:i_end]
            V_win = V[i_start:i_end]
            I_win = I[i_start:i_end]

            print(f"\n    Measurement window (last cycle): "
                  f"{t[i_start]*1e6:.1f}-{t[i_end]*1e6:.1f} us")
            print(f"    V_avg = {np.mean(V_win):.3f} V  I_avg = {np.mean(I_win)*1e3:.2f} mA")

            # For a resistor: P = V_avg * I_avg (should be purely resistive, no reactive)
            P_inst = V_win * I_win
            E_cycle = np.trapz(P_inst, t_win)
            T_cycle = t_win[-1] - t_win[0]
            print(f"    E_cycle = {E_cycle*1e6:.2f} uJ  (over {T_cycle*1e6:.1f} us)")

        # -- Plot --------------------------------------------------------------
        print("\n[8] Plotting...")
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True, figsize=(12, 8))

        t_us = t * 1e6

        # Voltage
        ax1.plot(t_us, V, 'b', linewidth=0.8)
        ax1.set_ylabel("Voltage [V]")
        ax1.set_title(f"TPT Simulacrum - 220 ohm @ {PSU_VOLTAGE} V, {FREQUENCY/1e3:.0f} kHz")
        ax1.grid(True, alpha=0.3)
        # Mark pulse boundaries
        for i, (r, f) in enumerate(valid_pulses):
            ax1.axvline(t[r]*1e6, color='green', alpha=0.3, linewidth=0.5)
        if len(valid_pulses) >= 1:
            ax1.axvspan(t[i_start]*1e6, t[i_end]*1e6, alpha=0.1, color='blue',
                        label='Measurement window')
            ax1.legend(fontsize=8)

        # Current
        ax2.plot(t_us, I * 1e3, 'r', linewidth=0.8)
        ax2.set_ylabel("Current [mA]")
        ax2.grid(True, alpha=0.3)
        if len(valid_pulses) >= 1:
            ax2.axvspan(t[i_start]*1e6, t[i_end]*1e6, alpha=0.1, color='blue')

        # Instantaneous power
        P = V * I * 1e3  # mW
        ax3.plot(t_us, P, 'g', linewidth=0.8)
        ax3.set_ylabel("Power [mW]")
        ax3.set_xlabel("Time [us]")
        ax3.grid(True, alpha=0.3)
        if len(valid_pulses) >= 1:
            ax3.axvspan(t[i_start]*1e6, t[i_end]*1e6, alpha=0.1, color='blue')

        plt.tight_layout()
        plt.savefig("resistor_tpt.png", dpi=150)
        print("    Saved: resistor_tpt.png")
        plt.show()

        print("\nDone!")
        return 0

    finally:
        if psu is not None:
            print("\n    Disabling PSU...")
            try:
                psu.disable_output(PSU_CHANNEL)
            except Exception:
                pass
        if board is not None:
            try:
                board.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
