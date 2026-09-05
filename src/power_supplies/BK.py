import pyvisa

from power_supply import PowerSupply


class BK(PowerSupply):
    def __init__(self, port):
        rm = pyvisa.ResourceManager()
        # print(rm.list_resources())
        if 'COM' in port:
            port = port.split('COM')[1]

        self.visa_session = rm.open_resource(f'ASRL{port}::INSTR')

        self.visa_session.timeout = 60000  # milliseconds (increased for self-test)
        self.visa_session.read_termination = '\n'
        self.visa_session.baud_rate = 9600

        # Line ending is DETECTED, not assumed.  This supply has been observed
        # to accept only '\r\n' — with '\n' it never sees a complete command and
        # every query times out, which looks exactly like a dead instrument: the
        # port opens, nothing ever answers, and a 60 s timeout means each failed
        # query stalls for a full minute.  Hardcoding either ending turns a
        # 30-second settings difference into a hardware fault hunt, so probe for
        # the one that actually works and remember it.
        self._detect_write_termination()

        # Put device into remote control mode
        self.visa_session.write('SYST:REM')
        self.visa_session.write('*WAI')

        test = self.visa_session.query('*TST?')
        assert test == "0", f"Initial test return fail: {test}"

    def _detect_write_termination(self, candidates=('\r\n', '\n'), probe='*IDN?',
                                  probe_timeout_ms=2000):
        """Pick the line ending this supply actually answers to.

        Tries each candidate with a short timeout and keeps the first that
        produces a reply.  If none does, leaves the first candidate in place and
        lets the caller's own error surface — the failure is then genuinely the
        instrument, not the framing.
        """
        original_timeout = self.visa_session.timeout
        try:
            for ending in candidates:
                self.visa_session.write_termination = ending
                self.visa_session.timeout = probe_timeout_ms
                try:
                    self._flush_silently()
                    if self.visa_session.query(probe).strip():
                        self.write_termination_detected = ending
                        return ending
                except Exception:                              # noqa: BLE001
                    continue
            self.visa_session.write_termination = candidates[0]
            self.write_termination_detected = None
            return None
        finally:
            self.visa_session.timeout = original_timeout

    def _flush_silently(self):
        """Drop anything already waiting, so a probe reads its own reply."""
        original_timeout = self.visa_session.timeout
        self.visa_session.timeout = 120
        try:
            while True:
                self.visa_session.read_raw()
        except Exception:                                      # noqa: BLE001
            pass
        finally:
            self.visa_session.timeout = original_timeout

        self.voltages = None
        self.channels = None

    def reset(self):
        self.visa_session.write('*RST')

    def get_version(self):
        return self.visa_session.query('SYST:VERS?')

    def get_available_channels(self):
        return self.channels

    def check_channel(self, channel):
        assert channel in self.channels or channel in [str(x) for x in self.channels], f"Wrong channel index: {channel}"

    def enable_output(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        self.visa_session.write('CHAN:OUTP:STAT 1')
        self.visa_session.write('*WAI')
        # Also enable the master output (required for any channel to deliver power)
        self.visa_session.write('OUTP:STAT 1')
        self.visa_session.write('*WAI')
        return self.visa_session.query('CHAN:OUTP:STAT?') == '1'

    def is_output_enabled(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        return self.visa_session.query('CHAN:OUTP:STAT?') == '1'

    def enable_all_outputs(self):
        self.visa_session.write('OUTP:STAT 1')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:STAT?') == '1'

    def is_series_mode_enabled(self):
        return self.visa_session.query('OUTP:SER?') == '1'

    def enable_series_mode(self):
        self.visa_session.write('OUTP:SER 1')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:SER?') == '1'

    def disable_series_mode(self):
        self.visa_session.write('OUTP:SER 0')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:SER?') == '0'

    def is_parallel_mode_enabled(self):
        return self.visa_session.query('OUTP:PARA?') == '1'

    def enable_parallel_mode(self):
        self.visa_session.write('OUTP:PARA 1')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:PARA?') == '1'

    def disable_parallel_mode(self):
        self.visa_session.write('OUTP:PARA 0')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:PARA?') == '0'

    def disable_output(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        self.visa_session.write('CHAN:OUTP:STAT 0')
        self.visa_session.write('*WAI')
        return self.visa_session.query('CHAN:OUTP:STAT?') == '0'

    def disable_all_outputs(self):
        self.visa_session.write('OUTP:STAT 0')
        self.visa_session.write('*WAI')
        return self.visa_session.query('OUTP:STAT?') == '0'

    def set_all_source_voltages(self, voltages):
        self.voltages = voltages
        self.visa_session.write(f'APP:VOLT {voltages[0]},{voltages[1]},{voltages[2]}')
        self.visa_session.write('*WAI')
        return self.visa_session.query('*OPC?') == '1'

    def set_source_voltage(self, channel, voltage):
        self.check_channel(channel)
        self.voltages[channel - 1] = voltage
        return self.set_all_source_voltages(self.voltages)

    def get_all_source_voltages(self):
        voltages_str = self.visa_session.query('APP:VOLT?')
        voltages = [float(x) for x in voltages_str.split(',')]
        return voltages

    def get_source_voltage(self, channel):
        self.check_channel(channel)
        return self.get_all_source_voltages()[channel - 1]

    def set_current_limit(self, channel, limit):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        self.visa_session.write(f'CURR {limit}')
        self.visa_session.write('*WAI')
        return self.visa_session.query('*OPC?') == '1'

    def get_current_limit(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('CURR?'))
        return limit

    def get_maximum_source_current(self, channel=1):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('CURR? MAX'))
        return limit

    def get_minimum_source_current(self, channel=1):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('CURR? MIN'))
        return limit

    def set_voltage_limit(self, channel, limit):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        self.visa_session.write(f'VOLT:LIMIT {limit}')
        self.visa_session.write('*WAI')
        return self.visa_session.query('*OPC?') == '1'

    def get_voltage_limit(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('VOLT:LIMIT?'))
        return limit

    def get_maximum_source_voltage(self, channel=1):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('VOLT:LIMIT? MAX'))
        return limit

    def get_minimum_source_voltage(self, channel=1):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        limit = float(self.visa_session.query('VOLT:LIMIT? MIN'))
        return limit

    def get_measured_voltage(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        voltage = float(self.visa_session.query('MEAS:VOLT?'))
        return voltage

    def get_all_measured_voltages(self):
        voltages_str = self.visa_session.query('MEAS:ALL?')
        values = [float(x) for x in voltages_str.split(',')]
        # MEAS:ALL? returns V1,I1,V2,I2,V3,I3 for 3-channel PSU
        # Extract only voltage values (every other starting from index 0)
        voltages = values[0::2]
        return voltages

    def get_measured_current(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        current = float(self.visa_session.query('MEAS:CURR?'))
        return current

    def get_all_measured_currents(self):
        currents_str = self.visa_session.query('MEAS:CURR:ALL?')
        currents = [float(x) for x in currents_str.split(',')]
        return currents

    def get_measured_power(self, channel):
        self.check_channel(channel)
        self.visa_session.write(f'INST:NSEL {channel}')
        self.visa_session.write('*WAI')
        power = float(self.visa_session.query('MEAS:POW?'))
        return power

    def get_all_measured_powers(self):
        powers_str = self.visa_session.query('MEAS:POW? ALL')
        powers = [float(x) for x in powers_str.split(',')]
        return powers


class BK9129B(BK):
    def __init__(self, port):
        super().__init__(port)
        self.channels = [1, 2, 3]
        self.voltages = [0] * len(self.channels)
