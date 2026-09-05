#!/usr/bin/env python3
"""
Resistor verification test
===========================
Sends pulses through the half-bridge into a 220 ohm resistor and captures
voltage (PicoScope ch A) and current (PicoScope ch C) to verify the setup.

Controls the BK9129B PSU to supply the DC bus voltage.
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
R_LOAD = 220  # ohm

# PSU: 5 V on channel 1, conservative current limit
PSU_VOLTAGE = 15.0  # V
PSU_CURRENT_LIMIT = 0.5  # A
PSU_CHANNEL = 1

# Pulse train: 8 half-periods at 50 kHz  ->  T_half = 10 us
FREQUENCY = 50_000  # Hz
NUM_PULSES = 8       # half-periods (must be even)
T_HALF = 1.0 / (2.0 * FREQUENCY)

# Scope settings
CH_VOLTAGE = "A"   # voltage across the resistor
CH_CURRENT = "C"   # current probe

V_RANGE = 20.0      # V
I_RANGE = 1.0       # V
# P6021: 2 mV/mA = 2 V/A  ->  to convert scope volts to amps: divide by 2
# probe_scale is a multiplier applied to raw volts, so 0.5 gives amps
CURRENT_PROBE_SCALE = 0.5


def main():
    print("=" * 60)
    print("  Resistor verification test (220 ohm)")
    print("  PicoScope ch A = voltage, ch C = current probe")
    print("  BK9129B PSU on ch 1 @ 5 V")
    print("=" * 60)

    psu = None
    board = None
    scope = None

    try:
        # -- Connect to PSU -----------------------------------------------
        print("\n[1] Connecting to BK9129B PSU...")
        psu = BK9129B(PSU_PORT)
        print(f"    Connected!")

        # -- Connect to board ----------------------------------------------
        print("\n[2] Connecting to NUCLEO-H503RB...")
        board = NUCLEO_H503RB(BOARD_PORT)
        idn = board.get_identification()
        print(f"    Board ID: {idn.strip()}")

        # -- Connect to PicoScope -----------------------------------------
        print("\n[3] Connecting to PicoScope 2408B...")
        scope = PicoScope2408B(None)
        print("    Connected!")

        # -- Configure PSU -------------------------------------------------
        print(f"\n[4] Configuring PSU: ch {PSU_CHANNEL} = {PSU_VOLTAGE} V, "
              f"I_limit = {PSU_CURRENT_LIMIT} A")
        psu.set_source_voltage(PSU_CHANNEL, PSU_VOLTAGE)
        psu.set_current_limit(PSU_CHANNEL, PSU_CURRENT_LIMIT)
        psu.enable_output(PSU_CHANNEL)
        time.sleep(0.5)  # let the output settle
        v_meas = psu.get_measured_voltage(PSU_CHANNEL)
        print(f"    Output enabled. Measured voltage: {v_meas:.2f} V")

        # -- Configure scope channels --------------------------------------
        print("\n[5] Configuring scope...")

        # Channel A: voltage across resistor, DC coupled
        scope.set_channel_configuration(CH_VOLTAGE, V_RANGE, 0, 0.0)
        scope.set_channel_label(CH_VOLTAGE, "Voltage")

        # Channel C: current probe, DC coupled
        scope.set_channel_configuration(CH_CURRENT, I_RANGE, 0, 0.0)
        scope.set_channel_label(CH_CURRENT, "Current")
        scope.set_probe_scale(CH_CURRENT, CURRENT_PROBE_SCALE)

        # Trigger on ch A rising edge at 50% of supply voltage
        trigger_level = PSU_VOLTAGE * 0.5
        scope.set_rising_trigger(CH_VOLTAGE, trigger_level, delayed_samples=0, timeout=10000)

        # Timing: capture the full pulse train with margin
        total_time = T_HALF * NUM_PULSES * 1.5
        n_samples = 5000
        dt = total_time / n_samples
        scope.set_number_samples(n_samples)
        scope.set_sampling_time(dt)
        real_dt = scope.get_sampling_time()

        print(f"    Ch A: +/-{V_RANGE} V  (voltage)")
        print(f"    Ch C: +/-{I_RANGE} V  (current, probe scale x{CURRENT_PROBE_SCALE})")
        print(f"    Trigger: ch A rising, {trigger_level:.1f} V, 10 s timeout")
        print(f"    Samples: {n_samples}  dt: {real_dt*1e9:.0f} ns  "
              f"window: {n_samples*real_dt*1e6:.0f} us")

        # -- Load pulse train onto board -----------------------------------
        print(f"\n[6] Loading pulse train: {NUM_PULSES} half-periods at "
              f"{FREQUENCY/1e3:.0f} kHz (T_half = {T_HALF*1e6:.0f} us)...")
        board.clear_pulses()
        for _ in range(NUM_PULSES):
            board.add_pulse(T_HALF)
        readback = board.read_pulses()
        print(f"    Loaded {len(readback)} pulses on MCU")

        # -- Arm scope, fire pulses, capture -------------------------------
        print("\n[7] Arming scope and firing pulses...")

        acq_time = n_samples * real_dt
        scope.set_acquisition_time(acq_time)
        scope.start_single_acquisition()

        # Give the scope time to arm before sending pulses
        time.sleep(2)

        board.run_pulses(1)
        print("    Pulses sent! Waiting for acquisition...")

        deadline = time.monotonic() + 15.0
        while True:
            state = scope.get_acquisition_state()
            if state == "COMP":
                break
            if time.monotonic() > deadline:
                print("    TIMEOUT: Scope never completed acquisition.")
                print("    Check trigger level, probe connections.")
                return 1
            time.sleep(0.1)

        print("    Acquisition complete!")

        # -- Read data -----------------------------------------------------
        print("\n[8] Reading waveform data...")
        df = scope.read_data([CH_VOLTAGE, CH_CURRENT])

        t = df["time"].to_numpy()
        V = df["Voltage"].to_numpy()
        I = df["Current"].to_numpy()

        print(f"    Voltage:  min={V.min():.4f} V   max={V.max():.4f} V   "
              f"pk-pk={V.max()-V.min():.4f} V")
        print(f"    Current:  min={I.min():.4f} A   max={I.max():.4f} A   "
              f"pk-pk={I.max()-I.min():.4f} A")

        # Quick sanity check: V/I ~ 220 ohm?
        V_pk = np.max(np.abs(V))
        I_pk = np.max(np.abs(I))
        if I_pk > 1e-4:
            R_measured = V_pk / I_pk
            print(f"\n    V_peak / I_peak = {V_pk:.3f} / {I_pk:.4f} = "
                  f"{R_measured:.1f} ohm  (expected ~{R_LOAD} ohm)")
        else:
            print(f"\n    Current too small to estimate R (I_peak = {I_pk:.6f} A)")
            print("    Check: current probe connection, probe scale setting")

        # -- Analysis ------------------------------------------------------
        print("\n[9] Analyzing...")

        # Find pulse regions using voltage threshold
        v_thresh = (V.max() + V.min()) / 2
        above = V > v_thresh

        # Compute average V and I during positive pulses vs idle
        if np.any(above) and np.any(~above):
            V_pulse_avg = np.mean(V[above])
            I_pulse_avg = np.mean(I[above])
            V_idle_avg = np.mean(V[~above])
            I_idle_avg = np.mean(I[~above])
            delta_V = V_pulse_avg - V_idle_avg
            delta_I = I_pulse_avg - I_idle_avg

            print(f"    During positive pulses:  V_avg = {V_pulse_avg:.4f} V  "
                  f"I_avg = {I_pulse_avg*1e3:.4f} mA")
            print(f"    During idle/negative:    V_avg = {V_idle_avg:.4f} V  "
                  f"I_avg = {I_idle_avg*1e3:.4f} mA")
            print(f"    Delta V = {delta_V:.4f} V   Delta I = {delta_I*1e3:.4f} mA")
            if abs(delta_I) > 1e-6:
                R_est = abs(delta_V / delta_I)
                print(f"    Estimated R = delta_V / delta_I = {R_est:.1f} ohm  "
                      f"(expected ~{R_LOAD} ohm)")

        # Raw scope voltage on current channel (before probe_scale)
        I_raw_V = I / CURRENT_PROBE_SCALE
        print(f"\n    Raw current ch voltage: min={I_raw_V.min()*1e3:.2f} mV  "
              f"max={I_raw_V.max()*1e3:.2f} mV  pk-pk={np.ptp(I_raw_V)*1e3:.2f} mV")
        print(f"    At P6021 10 mV/mA, pk-pk = {np.ptp(I_raw_V)*1e3:.2f} mV "
              f"-> {np.ptp(I_raw_V)*1e3/10:.2f} mA")

        # -- Plot ----------------------------------------------------------
        print("\n[10] Plotting...")
        fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))

        t_us = t * 1e6

        ax1.plot(t_us, V, 'b', linewidth=0.8)
        ax1.set_ylabel("Voltage [V]")
        ax1.set_title(f"Resistor verification - 220 ohm @ {PSU_VOLTAGE} V, "
                      f"{FREQUENCY/1e3:.0f} kHz")
        ax1.grid(True, alpha=0.3)

        ax2.plot(t_us, I * 1e3, 'r', linewidth=0.8)
        ax2.set_ylabel("Current [mA]")
        ax2.set_xlabel("Time [us]")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig("resistor_verification.png", dpi=150)
        print("    Saved: resistor_verification.png")
        plt.show()

        print("\nDone!")
        return 0

    finally:
        # Always disable PSU and clean up
        if psu is not None:
            print("\n    Disabling PSU output...")
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
