#!/usr/bin/env python3
"""Hold a gate-drive pin in a steady state so it can be measured with a DMM.

The half-bridge is commanded but produces no output, and normal pulses are far
too short (10 us) to catch with a multimeter. This drives ONE pin effectively
continuously for N seconds by repeatedly firing a train whose long pulse lands
on that pin, so a DMM reads a steady DC level.

Pulse-index -> pin mapping (tpt-scpi.c TPT_AddPulse):
    even index -> PositivePulse_Pin  PB10  (high-side)
    odd  index -> NegativePulse_Pin  PB4   (low-side)

Usage:
    python tools/hold_gate_pins.py pos [seconds]   # hold PB10 high
    python tools/hold_gate_pins.py neg [seconds]   # hold PB4  high
    python tools/hold_gate_pins.py off             # both pins low

WARNING: this holds one half-bridge switch ON continuously. Run it with the
DC rails OFF (this script never enables the PSU) unless you specifically want
to measure the power stage, in which case use a low rail voltage and a tight
current limit -- a static on-state has no volt-second balance and will drive
the inductor into saturation.
"""
import os
import sys
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from boards.ST import NUCLEO_H503RB
import json

LONG = 0.05     # 50 ms, the firmware maximum
SHORT = 1e-5    # 10 us filler so the long pulse lands on an odd index


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "pos").lower()
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    with open(os.path.join(_TPT_ROOT, 'hardware_configuration.json')) as f:
        port = json.load(f)["board_port"]
    board = NUCLEO_H503RB(port)

    try:
        board.clear_pulses()
        if mode == "off":
            board.reset()
            print("Both pins driven LOW (reset_pins). PB10 and PB4 should read ~0 V.")
            return 0
        elif mode == "pos":
            board.add_pulse(LONG)                 # index 0 -> PB10
            hot, cold = "PB10 (PositivePulse, high-side)", "PB4"
        elif mode == "neg":
            board.add_pulse(SHORT)                # index 0 -> PB10 (brief)
            board.add_pulse(LONG)                 # index 1 -> PB4
            hot, cold = "PB4 (NegativePulse, low-side)", "PB10"
        else:
            print(f"unknown mode {mode!r}; use pos | neg | off")
            return 2

        reps = max(1, int(seconds / LONG))
        print(f"Holding {hot} HIGH for ~{seconds:.0f} s ...")
        print(f"  Measure with a DMM (reference = board GND, e.g. TP11):")
        print(f"    {hot:38} -> expect ~3.3 V")
        print(f"    {cold:38} -> expect ~0 V")
        print(f"  Then measure the gate-driver secondary side (U6 / U8 outputs)")
        print(f"  and the isolated DC/DC rails (U1 / U7) -- if PB10/PB4 toggle but")
        print(f"  the driver outputs do not, the driver domain has no supply.")
        print()

        t_end = time.time() + seconds
        fired = 0
        while time.time() < t_end:
            board.run_pulses(20)     # 20 x 50 ms = 1 s of near-continuous hold
            fired += 20
            time.sleep(1.05)         # let the board finish before the next command
        print(f"done ({fired} trains). Driving both pins low.")
        board.reset()
    finally:
        try:
            board.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
