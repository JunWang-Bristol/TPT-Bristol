#!/usr/bin/env python3
"""
Check all scope channels to find current probe
"""

import sys
import os
import time

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from oscilloscope import Oscilloscope
from board import Board
from power_supply import PowerSupply

config_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')

with open(config_path) as f:
    cfg = json.load(f)

print("Checking all scope channels for signal...")
print("Make sure PSU is outputting voltage and pulses are firing\n")

psu = PowerSupply.factory(cfg['power_supply'], cfg['power_supply_port'])
board = Board.factory(cfg['board'], cfg['board_port'])
scope = Oscilloscope.factory(cfg['oscilloscope'], cfg['oscilloscope_port'])

# Set PSU to 5V
psu.set_source_voltage(1, 5.0)
psu.set_current_limit(1, 0.5)
psu.enable_output(1)
time.sleep(1)

# Configure all 4 channels
for ch in range(4):
    scope.set_channel_configuration(ch, 2.0, "DC", 0.0)
    scope.set_channel_label(ch, f"Ch{ch}")

# Fire single pulse
board.clear_pulses()
board.add_pulse(10e-6)
board.add_pulse(10e-6)

scope.set_number_samples(1000)
scope.set_sampling_time(100e-9)
scope.set_rising_trigger(0, 1.0)
scope.start_single_acquisition()
time.sleep(1)
board.run_pulses(1)

# Wait
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    if scope.get_acquisition_state() == "COMP":
        break
    time.sleep(0.1)

if scope.get_acquisition_state() == "COMP":
    # Read all channels
    labels = ['A', 'B', 'C', 'D']
    for ch in range(4):
        try:
            df = scope.read_data([ch])
            col = df.columns[1]  # First column after 'time'
            data = df[col].to_numpy()
            print(f"Channel {labels[ch]} ({ch}): range = [{data.min():.3f}, {data.max():.3f}] V  pk-pk = {data.max()-data.min():.3f} V")
        except Exception as e:
            print(f"Channel {labels[ch]} ({ch}): ERROR - {e}")
else:
    print("Acquisition timeout")

psu.disable_output(1)
board.close()
print("\nDone.")
