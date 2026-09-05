import unittest
import context  # noqa: F401
from power_supply import PowerSupply
import random
import time
import os
import json


class BoardsTests(unittest.TestCase):
    """
    Power supply tests that work with both BK9129B and GPP4323.
    
    GPP4323 specs:
    - CH1: 0-32V, 0-3A
    - CH2: 0-32V, 0-3A
    - CH3: 0-5V, 0-1A
    - CH4: 0-15V, 0-1A
    
    BK9129B specs:
    - CH1: 0-31V, 0-3.1A
    - CH2: 0-31V, 0-3.1A
    - CH3: 0-6V, 0-3.1A
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.abspath(os.path.join(os.getcwd(), os.path.dirname(__file__), os.pardir, "hardware_configuration.json"))) as f:
            cls.configuration = json.load(f)
            print(cls.configuration)

        cls.psut = PowerSupply.factory(cls.configuration['power_supply'], cls.configuration["power_supply_port"])
        cls.psut.reset()
        cls.power_supply_type = cls.configuration['power_supply']
        print(f"Starting tests for {cls.power_supply_type}")

    @classmethod
    def tearDownClass(cls):
        # These tests fuzz OVP limits, source voltages and output states with random
        # values.  Leaving them that way silently breaks the next measurement run:
        # an OVP left below the target rail makes set_source_voltage() a no-op, so
        # the rail sits at 0 V and the half-bridge never switches — which looks
        # exactly like a hardware fault.  Restore a predictable state.
        try:
            channels = cls.psut.get_available_channels()
            cls.psut.disable_all_outputs()
            for channel in channels:
                cls.psut.disable_output(channel)
                cls.psut.set_voltage_limit(channel, cls.psut.get_maximum_source_voltage(channel))
            cls.psut.set_all_source_voltages([0] * len(channels))
            print("  PSU restored: outputs off, voltages 0 V, OVP at maximum")
        except Exception as exception:
            print(f"  WARNING: could not restore PSU state: {exception}")
        print(f"\nFinishing tests for {cls.configuration['power_supply']}")

    def test_version(self):
        version = self.psut.get_version()
        # GPP4323 returns version like "1.XX", BK9129B returns "1991.1"
        self.assertIsNotNone(version)
        self.assertTrue(len(version.strip()) > 0)

    def test_maximum_voltage(self):
        print("test_maximum_voltage")
        if self.power_supply_type == "GPP4323":
            # GPP4323: CH1=32V, CH2=32V, CH3=5V, CH4=15V
            self.assertEqual(32, self.psut.get_maximum_source_voltage(1))
            self.assertEqual(32, self.psut.get_maximum_source_voltage(2))
            self.assertEqual(5, self.psut.get_maximum_source_voltage(3))
            self.assertEqual(15, self.psut.get_maximum_source_voltage(4))
        else:
            # BK9129B: CH1=31V, CH2=31V, CH3=6V
            self.assertEqual(31, self.psut.get_maximum_source_voltage(1))
            self.assertEqual(31, self.psut.get_maximum_source_voltage(2))
            self.assertEqual(6, self.psut.get_maximum_source_voltage(3))

    def test_minimum_voltage(self):
        print("test_minimum_voltage")
        available_channels = self.psut.get_available_channels()
        for channel in available_channels:
            minimum_source_voltage = self.psut.get_minimum_source_voltage(channel)
            self.assertEqual(0, minimum_source_voltage)

    def test_maximum_current(self):
        print("test_maximum_current")
        if self.power_supply_type == "GPP4323":
            # GPP4323: CH1=3A, CH2=3A, CH3=1A, CH4=1A
            self.assertEqual(3, self.psut.get_maximum_source_current(1))
            self.assertEqual(3, self.psut.get_maximum_source_current(2))
            self.assertEqual(1, self.psut.get_maximum_source_current(3))
            self.assertEqual(1, self.psut.get_maximum_source_current(4))
        else:
            # BK9129B: CH1=3.1A, CH2=3.1A, CH3=3.1A
            self.assertEqual(3.1, self.psut.get_maximum_source_current(1))
            self.assertEqual(3.1, self.psut.get_maximum_source_current(2))
            self.assertEqual(3.1, self.psut.get_maximum_source_current(3))

    def test_minimum_current(self):
        print("test_minimum_current")
        available_channels = self.psut.get_available_channels()
        for channel in available_channels:
            minimum_source_current = self.psut.get_minimum_source_current(channel)
            self.assertEqual(0, minimum_source_current)

    def test_source_voltage(self):
        print("test_source_voltage")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            channel = random.choice(available_channels)
            maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
            minimum_source_voltage = self.psut.get_minimum_source_voltage(channel)
            # GPP4323 has 3 decimal place precision for voltage
            if self.power_supply_type == "GPP4323":
                voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 3)
            else:
                voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
            self.psut.set_source_voltage(channel, voltage)
            read_voltage = self.psut.get_source_voltage(channel)
            self.assertEqual(voltage, read_voltage)

    def test_all_source_voltages(self):
        print("test_all_source_voltages")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            voltages = []
            for channel in available_channels:
                maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
                minimum_source_voltage = self.psut.get_minimum_source_voltage(channel)
                # GPP4323 has 3 decimal place precision for voltage
                if self.power_supply_type == "GPP4323":
                    voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 3)
                else:
                    voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
                voltages.append(voltage)
            self.psut.set_all_source_voltages(voltages)
            read_voltage = self.psut.get_all_source_voltages()
            self.assertEqual(voltages, read_voltage)

    def test_voltage_limit(self):
        print("test_voltage_limit")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            channel = random.choice(available_channels)
            maximum_voltage_limit = self.psut.get_maximum_source_voltage(channel)
            # GPP4323 OVP has minimum of 0.5V and 1 decimal precision
            if self.power_supply_type == "GPP4323":
                minimum_voltage_limit = 0.5
                limit = round(random.uniform(minimum_voltage_limit, maximum_voltage_limit), 1)
            else:
                minimum_voltage_limit = self.psut.get_minimum_source_voltage(channel)
                limit = round(random.uniform(minimum_voltage_limit, maximum_voltage_limit), 4)
            self.psut.set_voltage_limit(channel, limit)
            read_limit = self.psut.get_voltage_limit(channel)
            self.assertEqual(limit, read_limit)

    def test_current_limit(self):
        print("test_current_limit")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            channel = random.choice(available_channels)
            maximum_current_limit = self.psut.get_maximum_source_current(channel)
            minimum_current_limit = self.psut.get_minimum_source_current(channel)
            limit = round(random.uniform(minimum_current_limit, maximum_current_limit), 4)
            self.psut.set_current_limit(channel, limit)
            read_limit = self.psut.get_current_limit(channel)
            # Use assertAlmostEqual with 3 decimal places due to device rounding
            self.assertAlmostEqual(limit, read_limit, 3)

    def test_measured_voltage(self):
        print("test_measured_voltage")
        if self.configuration['power_supply'] == 'BK9129B':
            self.skipTest("Voltage measurement unreliable without load on BK9129B")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            channel = random.choice(available_channels)
            maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
            minimum_source_voltage = self.psut.get_minimum_source_voltage(channel)
            voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
            self.psut.set_source_voltage(channel, voltage)
            self.psut.enable_output(channel)
            time.sleep(1)  # Wait for output to settle
            for aux_channel in available_channels:
                if aux_channel == channel:
                    self.assertTrue(self.psut.is_output_enabled(aux_channel))
                else:
                    self.assertFalse(self.psut.is_output_enabled(aux_channel))

            read_voltage = self.psut.get_measured_voltage(channel)
            self.psut.disable_output(channel)
            self.assertAlmostEqual(voltage, read_voltage, 1)

    def test_all_measured_voltages(self):
        print("test_all_measured_voltages")
        if self.configuration['power_supply'] == 'BK9129B':
            self.skipTest("Voltage measurement unreliable without load on BK9129B")
        available_channels = self.psut.get_available_channels()
        for x in range(10):
            voltages = []
            for channel in available_channels:
                maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
                minimum_source_voltage = self.psut.get_minimum_source_voltage(channel)
                voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
                voltages.append(voltage)
            self.psut.set_all_source_voltages(voltages)
            self.psut.enable_all_outputs()
            time.sleep(1)  # Wait for outputs to settle
            for aux_channel in available_channels:
                self.assertTrue(self.psut.is_output_enabled(aux_channel))

            read_voltages = self.psut.get_all_measured_voltages()
            print(f"  Set: {voltages}, Read: {read_voltages}")
            self.psut.disable_all_outputs()
            for index in range(0, len(available_channels)):
                self.assertAlmostEqual(voltages[index], read_voltages[index], 1)

    def test_measured_current(self):
        print("test_measured_current")
        # This test require a resistor (recommended 1kOhm) in channel 1
        # First check if resistor is connected (5V / 1kOhm = 5mA expected)
        self.psut.set_source_voltage(1, 5.0)
        self.psut.enable_output(1)
        time.sleep(1)
        test_current = self.psut.get_measured_current(1)
        self.psut.disable_output(1)
        if test_current < 0.004:  # Less than 4mA means no 1kOhm resistor connected
            self.skipTest("Resistor not connected to channel 1")
        
        resistance = 1000
        for x in range(10):
            channel = 1
            maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
            voltage = round(random.uniform(maximum_source_voltage / 2, maximum_source_voltage), 4)
            self.psut.set_source_voltage(channel, voltage)
            self.psut.enable_output(channel)
            time.sleep(1)
            read_current = self.psut.get_measured_current(channel)
            self.psut.disable_output(channel)
            self.assertAlmostEqual(voltage / resistance, read_current, 2)

    def test_all_measured_currents(self):
        print("test_all_measured_currents")
        # This test require a resistor (recommended 1kOhm) in channel 1
        # First check if resistor is connected (5V / 1kOhm = 5mA expected)
        self.psut.set_source_voltage(1, 5.0)
        self.psut.enable_output(1)
        time.sleep(1)
        test_current = self.psut.get_measured_current(1)
        self.psut.disable_output(1)
        if test_current < 0.004:  # Less than 4mA means no 1kOhm resistor connected
            self.skipTest("Resistor not connected to channel 1")
        
        available_channels = self.psut.get_available_channels()
        resistance = 1000
        for x in range(10):
            maximum_source_voltage = self.psut.get_maximum_source_voltage(1)
            minimum_source_voltage = self.psut.get_minimum_source_voltage(1)
            voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
            # Create voltage list with correct number of channels
            voltages = [voltage] + [0] * (len(available_channels) - 1)
            self.psut.set_all_source_voltages(voltages)
            self.psut.enable_all_outputs()
            time.sleep(1)
            for aux_channel in available_channels:
                self.assertTrue(self.psut.is_output_enabled(aux_channel))

            read_currents = self.psut.get_all_measured_currents()
            self.psut.disable_all_outputs()
            for index in range(0, len(available_channels)):
                self.assertAlmostEqual(voltages[index] / resistance, read_currents[index], 2)

    def test_measured_power(self):
        print("test_measured_power")
        # This test require a resistor (recommended 1kOhm) in channel 1
        # First check if resistor is connected (5V / 1kOhm = 5mA expected)
        self.psut.set_source_voltage(1, 5.0)
        self.psut.enable_output(1)
        time.sleep(1)
        test_current = self.psut.get_measured_current(1)
        self.psut.disable_output(1)
        if test_current < 0.004:  # Less than 4mA means no 1kOhm resistor connected
            self.skipTest("Resistor not connected to channel 1")
        
        resistance = 1000
        for x in range(10):
            channel = 1
            maximum_source_voltage = self.psut.get_maximum_source_voltage(channel)
            voltage = round(random.uniform(maximum_source_voltage / 2, maximum_source_voltage), 4)
            self.psut.set_source_voltage(channel, voltage)
            self.psut.enable_output(channel)
            time.sleep(1)
            read_power = self.psut.get_measured_power(channel)
            self.psut.disable_output(channel)
            self.assertAlmostEqual(voltage**2 / resistance, read_power, 1)

    def test_all_measured_powers(self):
        print("test_all_measured_powers")
        # This test require a resistor (recommended 1kOhm) in channel 1
        # First check if resistor is connected (5V / 1kOhm = 5mA expected)
        self.psut.set_source_voltage(1, 5.0)
        self.psut.enable_output(1)
        time.sleep(1)
        test_current = self.psut.get_measured_current(1)
        self.psut.disable_output(1)
        if test_current < 0.004:  # Less than 4mA means no 1kOhm resistor connected
            self.skipTest("Resistor not connected to channel 1")
        
        available_channels = self.psut.get_available_channels()
        resistance = 1000
        for x in range(10):
            maximum_source_voltage = self.psut.get_maximum_source_voltage(1)
            minimum_source_voltage = self.psut.get_minimum_source_voltage(1)
            voltage = round(random.uniform(minimum_source_voltage, maximum_source_voltage), 4)
            # Create voltage list with correct number of channels
            voltages = [voltage] + [0] * (len(available_channels) - 1)
            self.psut.set_all_source_voltages(voltages)
            self.psut.enable_all_outputs()
            time.sleep(1)
            for aux_channel in available_channels:
                self.assertTrue(self.psut.is_output_enabled(aux_channel))

            read_powers = self.psut.get_all_measured_powers()
            self.psut.disable_all_outputs()
            for index in range(0, len(available_channels)):
                self.assertAlmostEqual(voltages[index]**2 / resistance, read_powers[index], 1)

    def test_series_mode(self):
        print("test_series_mode")
        result = self.psut.enable_series_mode()
        self.assertTrue(result)
        result = self.psut.is_series_mode_enabled()
        self.assertTrue(result)
        result = self.psut.disable_series_mode()
        self.assertTrue(result)
        result = self.psut.is_series_mode_enabled()
        self.assertFalse(result)

    def test_parallel_mode(self):
        print("test_parallel_mode")
        result = self.psut.enable_parallel_mode()
        self.assertTrue(result)
        result = self.psut.is_parallel_mode_enabled()
        self.assertTrue(result)
        result = self.psut.disable_parallel_mode()
        self.assertTrue(result)
        result = self.psut.is_parallel_mode_enabled()
        self.assertFalse(result)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
