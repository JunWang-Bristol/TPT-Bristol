#!/usr/bin/env python3
"""
TPT Pulse Train Test Script
============================
Sends a user-defined pulse train through the NUCLEO-H503RB (TPT SCPI firmware)
to the gate drivers.

This script assumes:
  - The connected MCU is flashed with the TPT_SCPI_Server firmware
  - The DC rail voltage is set separately (this script only drives the pulses)
  - pyvisa and pyvisa-py (or NI-VISA) are installed

Usage:
    python tests/test_tpt_pulse_train.py

Pin mapping on NUCLEO-H503RB:
    PB10  — PositivePulse (gate driver high-side)
    PB4   — NegativePulse (gate driver low-side)
    PA3   — USART3 RX
    PA4   — USART3 TX

Before running:
    1. pip install pyvisa pyvisa-py
    2. Flash the NUCLEO-H503RB with TPT_SCPI_Server firmware
    3. Connect Nucleo to PC via USB
    4. Update SERIAL_PORT below to match your setup
"""

import sys
import os
import json
import time

import context  # noqa: F401 — adds src/ to sys.path212

from boards.ST import NUCLEO_H503RB


# ============ CONFIGURATION ============
# Port comes from hardware_configuration.json so this script follows the rig
# instead of drifting out of date with a hardcoded COM number.
_CONFIG_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, "hardware_configuration.json"))
with open(_CONFIG_PATH) as _f:
    SERIAL_PORT = json.load(_f)["board_port"]
# =======================================


def input_float(prompt, default=None):
    """Prompt for a float value with optional default."""
    if default is not None:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    val = input(prompt).strip()
    if val == "" and default is not None:
        return default
    return float(val)


def input_int(prompt, default=None):
    """Prompt for an int value with optional default."""
    if default is not None:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    val = input(prompt).strip()
    if val == "" and default is not None:
        return default
    return int(val)


def input_pulse_train():
    """
    Interactive prompt to define a pulse train.

    Returns:
        list of float: pulse periods in seconds
    """
    print("\n--- Define Pulse Train ---")
    print("Enter pulse periods one at a time (in seconds or with units).")
    print("Examples:  0.00001  or  10e-6  for 10 µs")
    print("Type 'done' when finished, 'clear' to start over.\n")

    pulses = []
    while True:
        try:
            val = input(f"  Pulse {len(pulses) + 1}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if val in ("done", "d", ""):
            if not pulses:
                print("  No pulses entered yet. Enter at least one period.")
                continue
            break
        elif val in ("clear", "c"):
            pulses = []
            print("  Cleared all pulses.")
            continue
        elif val == "q":
            return None

        try:
            period = float(val)
            pulses.append(period)
            print(f"    Added pulse {len(pulses)}: {period:.6e} s  ({period * 1e6:.2f} µs)")
        except ValueError:
            print(f"    Invalid value '{val}'. Enter a number in seconds (e.g. 10e-6).")

    return pulses


def print_pulse_summary(pulses):
    """Print a summary table of the pulse train."""
    print(f"\n  {'#':<5} {'Period (s)':<15} {'Period (µs)':<15}")
    print(f"  {'—' * 5} {'—' * 15} {'—' * 15}")
    for i, p in enumerate(pulses, 1):
        print(f"  {i:<5} {p:<15.6e} {p * 1e6:<15.2f}")
    total = sum(pulses)
    print(f"  {'—' * 35}")
    print(f"  Total: {total:.6e} s  ({total * 1e6:.2f} µs)")
    print(f"  Number of pulses: {len(pulses)}")


def preset_symmetric(frequency, num_steady_pairs=7):
    """
    Generate a symmetric TPT pulse train from a frequency.

    This is a convenience function that creates alternating positive/negative
    half-periods, similar to what tpt.py calculates.

    Args:
        frequency: switching frequency in Hz
        num_steady_pairs: number of positive+negative period pairs

    Returns:
        list of float: pulse periods in seconds
    """
    half_period = 1.0 / (2.0 * frequency)
    pulses = []
    for _ in range(num_steady_pairs):
        pulses.append(half_period)  # positive half-period
        pulses.append(half_period)  # negative half-period
    return pulses


def main():
    print("=" * 55)
    print("  TPT Pulse Train Test")
    print("  NUCLEO-H503RB → Gate Drivers (PB10 / PB4)")
    print("=" * 55)
    print(f"  Serial Port: {SERIAL_PORT}")
    print("=" * 55)

    # Connect to board
    try:
        print("\n[1] Connecting to NUCLEO-H503RB...")
        board = NUCLEO_H503RB(SERIAL_PORT)
        print("    ✓ Connected!")
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\nTroubleshooting:")
        print(f"  1. Is the serial port correct? (current: {SERIAL_PORT})")
        print("  2. Is the Nucleo connected via USB?")
        print("  3. Is another program holding the port?")
        print("  4. Is the TPT_SCPI_Server firmware flashed?")
        print("  5. pip install pyvisa pyvisa-py")
        return 1

    # Show board info
    try:
        idn = board.get_identification()
        print(f"    ID: {idn.strip()}")
    except Exception:
        pass

    try:
        min_p = board.get_minimum_period()
        max_p = board.get_maximum_period()
        print(f"    Min pulse period: {min_p:.6e} s  ({min_p * 1e6:.2f} µs)")
        print(f"    Max pulse period: {max_p:.6e} s  ({max_p * 1e6:.2f} µs)")
    except Exception:
        min_p, max_p = 5e-7, 0.05

    # Interactive loop
    pulses = []
    while True:
        print("\n" + "-" * 55)
        print("Options:")
        print("  [1] Enter pulse train manually")
        print("  [2] Generate symmetric train from frequency")
        print("  [3] Run the current pulse train")
        print("  [4] View current pulse train")
        print("  [5] Clear pulse train")
        print("  [6] Read pulse count")
        print("  [q] Quit")

        try:
            choice = input("\nChoice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if choice in ("q", "quit", "exit"):
            print("Resetting board and exiting...")
            try:
                board.reset()
            except Exception:
                pass
            break

        elif choice == "1":
            # If we already have pulses, ask if user wants to keep them
            if pulses:
                print(f"\n  Current pulse train has {len(pulses)} pulses.")
                keep = input("  Keep existing pulse train? [Y/n]: ").strip().lower()
                if keep not in ("", "y", "yes"):
                    new_pulses = input_pulse_train()
                    if new_pulses is not None:
                        pulses = new_pulses
            else:
                new_pulses = input_pulse_train()
                if new_pulses is not None:
                    pulses = new_pulses
            
            if not pulses:
                continue

            # Validate against firmware limits
            invalid = False
            for i, p in enumerate(pulses):
                if p < min_p or p > max_p:
                    print(f"  ⚠ Pulse {i + 1} ({p:.6e} s) is outside "
                          f"allowed range [{min_p:.6e}, {max_p:.6e}]")
                    invalid = True
            if invalid:
                print("  Fix the values and try again.")
                continue

            print_pulse_summary(pulses)

            confirm = input("\n  Load this pulse train to MCU? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                # Only clear, don't reset (reset would clear current_number_pulses)
                board.clear_pulses()
                for p in pulses:
                    board.add_pulse(pulse_period=p)
                print(f"  ✓ Loaded {len(pulses)} pulses to MCU")

                # Verify
                try:
                    read_back = board.read_pulses()
                    if len(read_back) == len(pulses):
                        print(f"  ✓ Verified: {len(read_back)} pulses loaded")
                    else:
                        print(f"  ⚠ Warning: Expected {len(pulses)} pulses, MCU has {len(read_back)}")
                except Exception as e:
                    print(f"  (Could not verify: {e})")

        elif choice == "2":
            freq = input_float("  Frequency (Hz)", default=50000)
            pairs = input_int("  Number of period pairs", default=7)
            pulses = preset_symmetric(freq, pairs)

            print(f"\n  Generated {len(pulses)} pulses at {freq:.0f} Hz:")
            print_pulse_summary(pulses)

            confirm = input("\n  Load this pulse train to MCU? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                # Only clear, don't reset (reset would clear current_number_ pulses)
                board.clear_pulses()
                for p in pulses:
                    board.add_pulse(pulse_period=p)
                print(f"  ✓ Loaded {len(pulses)} pulses to MCU")

                # Verify
                try:
                    read_back = board.read_pulses()
                    if len(read_back) == len(pulses):
                        print(f"  ✓ Verified: {len(read_back)} pulses loaded")
                    else:
                        print(f"  ⚠ Warning: Expected {len(pulses)} pulses, MCU has {len(read_back)}")
                except Exception as e:
                    print(f"  (Could not verify: {e})")

        elif choice == "3":
            # Check if pulses are loaded first
            try:
                current = board.read_pulses()
                if not current:
                    print("  ⚠ No pulses loaded! Use option 1 or 2 first.")
                    continue
                print(f"  {len(current)} pulses loaded on MCU")
            except Exception as e:
                print(f"  ⚠ Could not verify pulses: {e}")
                confirm = input("  Continue anyway? [y/N]: ").strip().lower()
                if confirm not in ("y", "yes"):
                    continue

            reps = input_int("  Number of repetitions", default=1)
            print(f"  Running pulse train ({reps} repetition(s))...")
            board.run_pulses(number_repetitions=reps)
            print("  ✓ Pulse train sent!")
            
            # Flush any potential output from the MCU mechanism
            time.sleep(0.1)
            board.flush_buffer()

            try:
                count = board.count_trains()
                print(f"  Total trains executed: {count}")
            except Exception:
                pass

        elif choice == "4":
            try:
                current = board.read_pulses()
                if current:
                    print(f"\n  Current pulse train ({len(current)} pulses):")
                    print_pulse_summary(current)
                else:
                    print("  No pulses loaded.")
            except Exception as e:
                print(f"  Error reading pulses: {e}")

        elif choice == "5":
            board.clear_pulses()
            print("  ✓ Pulse train cleared")
            try:
                read_back = board.read_pulses()
                if len(read_back) == 0:
                    print("  ✓ Verified: MCU has 0 pulses")
                else:
                    print(f"  ⚠ Warning: MCU still has {len(read_back)} pulses")
            except Exception:
                pass

        elif choice == "6":
            try:
                count = board.count_trains()
                print(f"  Total trains executed: {count}")
            except Exception as e:
                print(f"  Error: {e}")

        else:
            print(f"  Unknown option '{choice}'")

    try:
        board.close()
    except Exception:
        pass

    print("Goodbye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
