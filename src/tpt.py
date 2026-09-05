"""tpt.py — Inductance measurement via current-ramp slope (TPT method).

Supported hardware
------------------
- PSU   : GW Instek GPP-4323  (TCP/IP SCPI)
- Scope : R&S RTB2004         (TCP/IP SCPI)
- Board : NUCLEO-H503RB       (UART SCPI)

Probe scale conventions
-----------------------
- input_voltage_probe_scale  = 50  →  50:1 divider probe
- output_voltage_probe_scale = 10  →  10:1 probe (secondary winding, reserved for core loss)
- current_probe_scale = 0.1  →  100 mV/A probe: attenuation factor sent to scope

The RTB2004 scope has probe scales configured via SCPI during initialisation.
All V/div and trigger-level values are at probe-tip level — the scope applies the
probe ratio internally.  Waveform data returned by read_data() is also at probe-tip
level (the scope compensates before returning samples).
"""

import time
import json
import numpy as np
import matplotlib.pyplot as plt

from power_supply import PowerSupply
from oscilloscope import Oscilloscope
from board import Board


# ─── Core / material databases ───────────────────────────────────────────────

CORE_DATABASE = {
    "T18": {"Ae": 10.8e-6,  "le": 26.7e-3,  "name": "T-18 (Micrometals)"},
    "T26": {"Ae": 52.3e-6,  "le": 63.5e-3,  "name": "TX26/15/10"},
    "E32": {"Ae": 83.0e-6,  "le": 121.0e-3, "name": "E32/6/20"},
    "E42": {"Ae": 178.0e-6, "le": 97.0e-3,  "name": "E42/21/20"},
}

MATERIAL_DATABASE = {
    "3C90": {"mu_r": 2300, "B_sat": 0.35, "name": "Ferroxcube 3C90"},
    "3F3":  {"mu_r": 2000, "B_sat": 0.35, "name": "Ferroxcube 3F3"},
    "N87":  {"mu_r": 2200, "B_sat": 0.39, "name": "TDK N87"},
    "N97":  {"mu_r": 2300, "B_sat": 0.40, "name": "TDK N97"},
}


# Firmware deadtime between complementary gate pulses (tpt-scpi.c, 500 ns)
DEADTIME_S = 500e-9


def flux_to_voltage(B_peak_T, N, Ae_m2, frequency_hz, deadtime_s=DEADTIME_S):
    """Return the PSU voltage required to achieve a target peak flux density.

    For a symmetric bipolar square-wave drive the volt-second balance gives:

        V × T_eff = 2 × B_peak × N × Ae
        T_eff = T_half − deadtime   (deadtime reduces drive window each half-period)
        T_half = 1 / (2 × f)

        → V = 2 × B_peak × N × Ae / T_eff

    The firmware inserts a deadtime between complementary gate pulses so neither
    switch conducts.  During deadtime the inductor current freewheels through body
    diodes and the effective volt-seconds per half-period are reduced.  Residual
    resistive drops (switch R_ds_on, winding DCR) cause additional shortfall; the
    iterative flux control in measure_core_loss corrects for those automatically.

    Parameters
    ----------
    B_peak_T     : float — peak flux density [T]
    N            : int   — primary winding turns
    Ae_m2        : float — effective cross-sectional area [m²]
    frequency_hz : float — switching frequency [Hz]
    deadtime_s   : float — gate deadtime [s] (default: firmware DEADTIME_S)

    Returns
    -------
    float — required supply voltage [V]
    """
    T_half = 1.0 / (2.0 * frequency_hz)
    T_eff  = T_half - deadtime_s
    if T_eff <= 0:
        raise ValueError(
            f"Half-period ({T_half*1e9:.0f} ns) must be greater than deadtime ({deadtime_s*1e9:.0f} ns)"
        )
    return 2.0 * B_peak_T * N * Ae_m2 / T_eff


def theoretical_inductance(core_name, material_name, N, gap_m=0.0):
    """Return theoretical inductance [H] for a toroid (ungapped by default).

    Parameters
    ----------
    core_name     : str   — key in CORE_DATABASE
    material_name : str   — key in MATERIAL_DATABASE
    N             : int   — number of turns
    gap_m         : float — total air gap [m] (default 0, no gap)
    """
    mu0  = 4 * np.pi * 1e-7
    core = CORE_DATABASE[core_name]
    mat  = MATERIAL_DATABASE[material_name]
    if gap_m:
        mu_eff = mat["mu_r"] / (1.0 + mat["mu_r"] * gap_m / core["le"])
    else:
        mu_eff = mat["mu_r"]
    return mu0 * mu_eff * N**2 * core["Ae"] / core["le"]


def _is_clipped(arr, min_flat=5):
    """Return True if arr shows scope clipping (flat top or flat bottom).

    When the signal exceeds the scope's vertical range the ADC saturates and
    returns consecutive identical samples at the maximum or minimum value.
    This function detects that by looking for runs of ≥ min_flat samples
    within 0.5% of the array's peak or trough.
    """
    pk_pk = float(arr.max() - arr.min())
    if pk_pk < 1e-9:
        return False
    eps = pk_pk * 0.005
    for mask in (arr >= arr.max() - eps, arr <= arr.min() + eps):
        padded = np.concatenate(([False], mask, [False]))
        starts = np.where(~padded[:-1] &  padded[1:])[0]
        ends   = np.where( padded[:-1] & ~padded[1:])[0]
        if len(starts) > 0 and (ends - starts).max() >= min_flat:
            return True
    return False


def _smallest_range_fitting(scope, peak_volts, headroom=1.3):
    """Smallest available scope range that fits ±peak_volts, with headroom.

    PicoScope input ranges are ± full scale (NOT volts/div), so the signal
    peak must fit inside the range itself.  Picking the smallest range that
    still fits matters a lot on an 8-bit scope: the 2408B has only 256 codes
    across the range, so a signal using 8% of full scale is resolved with
    ~21 codes, while the same signal on a 5× smaller range gets ~106.

    `peak_volts` is the signal at the BNC — convert from physical units by
    dividing by the probe scale first (read_data multiplies by probe_scale).
    """
    needed = abs(peak_volts) * headroom
    ranges = sorted(scope.get_input_voltage_ranges())
    for r in ranges:
        if r >= needed:
            return r
    return ranges[-1]


# ─── Base class ───────────────────────────────────────────────────────────────

class Measurement:
    """Open hardware connections and store probe-scale factors."""

    # PicoScope channel indices (0-based integers accepted by the driver)
    CH_VOLTAGE   = 0   # CH A — primary / input voltage  (probe scale from config)
    CH_SECONDARY = 1   # CH B — secondary winding        (probe scale from config)
    CH_CURRENT   = 3   # CH D — inductor current         (probe scale from config)

    # GPP-4323 output channels — CH1 positive rail, CH2 negative rail
    PSU_CHANNEL     = 1
    PSU_CHANNEL_NEG = 2

    def __init__(
        self,
        power_supply,
        oscilloscope,
        board,
        power_supply_port,
        oscilloscope_port,
        board_port,
        input_voltage_probe_scale=50,
        output_voltage_probe_scale=10,
        current_probe_scale=0.1,
    ):
        self.psu   = PowerSupply.factory(power_supply,  power_supply_port)
        self.scope = Oscilloscope.factory(oscilloscope, oscilloscope_port)
        self.board = Board.factory(board,               board_port)

        self.input_voltage_probe_scale  = float(input_voltage_probe_scale)
        self.output_voltage_probe_scale = float(output_voltage_probe_scale)
        self.current_probe_scale        = float(current_probe_scale)

        self.scope.set_probe_scale(self.CH_VOLTAGE, self.input_voltage_probe_scale)
        self.scope.set_probe_scale(self.CH_CURRENT, self.current_probe_scale)
        if hasattr(self.scope, 'set_probe_units'):
            self.scope.set_probe_units(self.CH_CURRENT, 'A')


    def _configure_psu(self, voltage):
        """Set voltage on both PSU channels and enable their outputs."""
        for ch in (self.PSU_CHANNEL, self.PSU_CHANNEL_NEG):
            self.psu.set_source_voltage(ch, voltage)
            self.psu.set_current_limit(ch, 3.0)
            self.psu.enable_output(ch)

    def _disable_psu(self):
        """Turn off both PSU output channels."""
        for ch in (self.PSU_CHANNEL, self.PSU_CHANNEL_NEG):
            self.psu.disable_output(ch)

    def close(self):
        """Release hardware connections so another Measurement can use them."""
        if hasattr(self.psu, 'visa_session'):
            try:
                self.psu.visa_session.close()
            except Exception:
                pass
        if hasattr(self.scope, 'close'):
            try:
                self.scope.close()
            except Exception:
                pass
        if hasattr(self.board, 'close'):
            try:
                self.board.close()
            except Exception:
                pass

    @classmethod
    def from_config(cls, config_path="hardware_configuration.json"):
        """Instantiate from a JSON configuration file.

        Keys this class does not take are IGNORED rather than fatal.  The file
        is shared with the ``opentpt`` engine, which reads settings these older
        classes know nothing about (``current_channel_skew_s`` was the first),
        and ``cls(**cfg)`` turned every such addition into a TypeError that
        broke every script here at import of the config — a failure with no
        relation to what the script was doing.
        """
        import inspect

        with open(config_path) as f:
            cfg = json.load(f)
        accepted = set(inspect.signature(cls.__init__).parameters) - {"self"}
        known = {k: v for k, v in cfg.items() if k in accepted}
        ignored = sorted(set(cfg) - accepted)
        if ignored:
            print(f"note: {cls.__name__} ignores config keys {ignored} "
                  f"(the opentpt engine reads them)")
        return cls(**known)


# ─── Inductance measurement ───────────────────────────────────────────────────

class InductanceMeasurement(Measurement):
    """Measure inductance by fitting L = V_avg / (dI/dt) on the first positive pulse."""

    # ── private helpers ───────────────────────────────────────────────────────

    def _make_pulse_train(self, T_half, num_pulses):
        """Return a balanced list of equal half-period durations (always even count)."""
        n = num_pulses if num_pulses % 2 == 0 else num_pulses + 1
        return [T_half] * n

    def _configure_scope(self, voltage, T_half, num_pulses, L_estimate=None):
        """Configure scope for inductance measurement.

        All V/div and trigger values are at probe-tip level.  The scope applies
        its configured probe ratio internally.

        Parameters
        ----------
        voltage    : float        — PSU supply voltage [V] (sets V/div and trigger)
        T_half     : float        — half-period [s]
        num_pulses : int          — number of half-periods
        L_estimate : float | None — inductance estimate [H] for current scale;
                                    if None a conservative 0.5 A/div is used
        """
        scope = self.scope

        # Voltage range: the DUT is driven to the full rail voltage, so the signal
        # peaks at ~±voltage.  This is ± FULL SCALE, not volts/div.
        V_probe = scope.probe_scale.get(self.CH_VOLTAGE, 1.0) or 1.0
        V_scale = _smallest_range_fitting(scope, voltage / V_probe)

        # Current range: expected peak swing ΔI = V·T_half/L.  ΔI is in AMPS, so
        # convert to the volts actually seen at the BNC (amps = volts × probe_scale)
        # before choosing a range, then take the smallest range that fits.
        cur_probe_scale = scope.probe_scale.get(self.CH_CURRENT, 1.0) or 1.0
        I_peak = (voltage * T_half / L_estimate
                  if L_estimate is not None and L_estimate > 0
                  else 0.5)          # conservative fallback when L is unknown
        I_scale = _smallest_range_fitting(scope, I_peak / cur_probe_scale)

        # Note: set_channel_configuration() overwrites channel_labels with the raw channel
        # integer argument.  Call set_channel_label() AFTER it to set the correct names.
        scope.set_channel_configuration(self.CH_VOLTAGE, V_scale, "DC", 0.0)
        scope.set_channel_configuration(self.CH_CURRENT, I_scale, "AC", 0.0)

        # Trigger: 30% of supply voltage, clamped to 3% of actual scope range
        # (PicoScope BNC-level signal is small with high-attenuation differential probes)
        actual_range = scope.get_channel_configuration(self.CH_VOLTAGE).input_voltage_range
        probe_scale = scope.probe_scale.get(self.CH_VOLTAGE, 1.0)
        trigger_level = min(voltage * 0.3, actual_range * probe_scale * 0.03)
        scope.set_channel_label(self.CH_VOLTAGE, "Voltage")
        scope.set_channel_label(self.CH_CURRENT, "Current")

        # Trigger on current channel — more reliable with high-attenuation voltage probes
        cur_probe_scale = scope.probe_scale.get(self.CH_CURRENT, 1.0)
        if L_estimate is not None and L_estimate > 0:
            I_peak_bnc = (voltage * T_half / L_estimate) / cur_probe_scale
            cur_trigger = max(0.02, I_peak_bnc * 0.3)
        else:
            cur_range = scope.get_channel_configuration(self.CH_CURRENT).input_voltage_range
            cur_trigger = cur_range * 0.10
        scope.set_rising_trigger(self.CH_CURRENT, cur_trigger)

        # Timing: 50 samples per half-period is plenty for OLS regression.
        # 1.5x total time gives post-trigger headroom.
        total_time = T_half * num_pulses
        dt_target  = T_half / 50.0
        n_samples  = max(500, int(total_time * 1.5 / dt_target))
        scope.set_number_samples(n_samples)
        scope.set_sampling_time(dt_target)

        print(
            f"Scope config — V/div: {V_scale:.3f} V  I/div: {I_scale*1e3:.0f} mA"
            f"  trigger: {trigger_level:.2f} V  acqTime: {total_time*1.5*1e6:.0f} us"
            f"  samples: {n_samples}"
        )

    def _fire_and_capture(self, T_half, num_pulses, timeout_s=10.0):
        """Arm scope → fire pulse train → wait → return DataFrame or None."""
        board = self.board
        scope = self.scope

        # Load pulse train onto the board (firmware alternates +/− automatically)
        board.clear_pulses()
        for T in self._make_pulse_train(T_half, num_pulses):
            board.add_pulse(T)

        acq_time = scope.number_samples * scope.sampling_time
        scope.set_acquisition_time(acq_time)
        scope.start_single_acquisition()
        # Fire pulses repeatedly — scope trigger may need several attempts to
        # catch the burst, especially with PicoScope and direct (non-attenuated)
        # probe connections where DC bias can be near the trigger level.
        time.sleep(1)
        for _ in range(10):
            board.run_pulses(1)
            time.sleep(0.2)

        deadline = time.monotonic() + timeout_s
        while True:
            time.sleep(0.1)
            state = scope.get_acquisition_state()
            if state == "COMP":
                break
            if time.monotonic() > deadline:
                print(
                    f"ERROR: Scope never reached COMP (last state: {state}).\n"
                    "  -> Check trigger level, probe connections, and that CH1 is on the voltage node."
                )
                return None

        # Extend read timeout for the data transfer (slow on some scopes/backends).
        scope.set_read_timeout(30000)
        df = scope.read_data([self.CH_VOLTAGE, self.CH_CURRENT])
        scope.reset_read_timeout()
        if df is None or df.empty:
            print("ERROR: Scope returned empty data.")
            return None
        if "Voltage" not in df.columns or "Current" not in df.columns:
            print(f"ERROR: Expected columns 'Voltage' and 'Current', got {df.columns.tolist()}")
            return None
        return df

    def _extract_inductance(self, t, V, I, voltage):
        """Fit L = V_avg / (dI/dt) on the middle 50% of the first positive pulse.

        Parameters
        ----------
        t, V, I  : 1-D numpy arrays — time [s], voltage [V], current [A]
        voltage  : float — PSU supply voltage [V], used as edge-detection reference

        Returns
        -------
        float | None — inductance in henries, or None on failure.
        """
        v_pkpk = np.max(V) - np.min(V)
        if v_pkpk < 0.05:
            print(
                f"ERROR: Voltage pk-pk {v_pkpk:.3f} V is too small — "
                "scope may not have triggered or probe is disconnected."
            )
            return None

        # Use 20% of the peak voltage as the edge threshold.
        # (lower threshold avoids missing short pulses with attenuating probes)
        v_thresh = np.max(V) * 0.2
        above    = np.where(V > v_thresh)[0]
        if len(above) == 0:
            print("ERROR: No rising edge detected in voltage waveform.")
            return None
        rise_idx = above[0]

        after_rise = np.where(V[rise_idx:] < v_thresh)[0]
        if len(after_rise) == 0:
            print("ERROR: No falling edge detected — pulse continues to end of record.")
            return None
        fall_idx = rise_idx + after_rise[0]

        pulse_len = fall_idx - rise_idx
        if pulse_len < 5:
            print(f"ERROR: First positive pulse only {pulse_len} samples — too short for regression.")
            return None

        # Middle 50% of the pulse (skip 25% at each edge to avoid switching ringing)
        margin = pulse_len // 4
        start  = rise_idx + margin
        end    = fall_idx - margin

        t_win = t[start:end]
        I_win = I[start:end]
        V_win = V[start:end]

        # Ordinary least-squares: dI/dt
        t_c   = t_win - t_win.mean()
        denom = np.sum(t_c ** 2)
        if denom < 1e-30:
            print("ERROR: Regression window is degenerate (zero time span).")
            return None
        slope = np.sum(t_c * (I_win - I_win.mean())) / denom   # A/s

        if abs(slope) < 1.0:   # sanity: at least 1 A/s
            print(
                f"ERROR: Current slope {slope:.3g} A/s is too small — "
                "check current probe and connections."
            )
            return None

        V_avg = np.mean(V_win)   # V
        L = abs(V_avg / slope)   # H
        return L

    # ── public interface ──────────────────────────────────────────────────────

    def measure_inductance(self, voltage, frequency, num_pulses=8, plot=True,
                           L_estimate=None):
        """Measure inductance via current-ramp slope.

        Parameters
        ----------
        voltage    : float        — PSU supply voltage [V]
        frequency  : float        — switching frequency [Hz]  (T_half = 1/(2f))
        num_pulses : int          — number of half-periods; rounded up to even if odd
        plot       : bool         — display waveform plot after measurement
        L_estimate : float | None — inductance estimate [H] used to pre-scale the
                                    current channel (e.g. theoretical value); if None
                                    a conservative 0.5 A/div is used

        Returns
        -------
        float | None — inductance in henries, or None on failure.
        """
        T_half = 1.0 / (2.0 * frequency)

        print(
            f"\nInductance measurement: V={voltage} V  f={frequency/1e3:.1f} kHz"
            f"  T_half={T_half*1e6:.1f} us  pulses={num_pulses}"
        )

        self._configure_psu(voltage)
        try:
            self._configure_scope(voltage, T_half, num_pulses, L_estimate)

            for attempt in range(5):
                df = self._fire_and_capture(T_half, num_pulses)
                if df is None:
                    return None
                if not _is_clipped(df["Current"].to_numpy()):
                    break
                # Double the current channel range
                cur_range = self.scope.get_channel_configuration(self.CH_CURRENT).input_voltage_range
                new_range = cur_range * 2.0
                print(f"  WARNING: Current clipped — retrying at {new_range:.3f} V range")
                self.scope.set_channel_configuration(self.CH_CURRENT, new_range, "DC", 0.0)
                self.scope.set_channel_label(self.CH_CURRENT, "Current")
            else:
                print("  WARNING: Current still clipped after 5 attempts — results may be wrong.")

            t = df["time"].to_numpy()
            V = df["Voltage"].to_numpy()
            I = df["Current"].to_numpy()
            print(f"[data] V range: [{V.min():.4f}, {V.max():.4f}] V   "
                  f"I range: [{I.min():.4f}, {I.max():.4f}] A")

            L = self._extract_inductance(t, V, I, voltage)
            if L is None:
                return None

            print(f"L = {L * 1e6:.1f} uH")

            if plot:
                _plot_inductance(t, V, I, L, T_half)

            return L
        finally:
            self._disable_psu()

    def demagnetize(self, voltage=5.0, frequency=5000, steps=8):
        """Demagnetize the core using decreasing-amplitude pulse bursts.

        Parameters
        ----------
        voltage   : float — initial PSU voltage [V]
        frequency : float — base switching frequency [Hz]
        steps     : int   — number of amplitude steps (linearly tapered to zero)
        """
        print(f"Demagnetizing: V={voltage} V  f={frequency} Hz  steps={steps}")
        self._configure_psu(voltage)
        try:
            board  = self.board
            T_base = 1.0 / (2.0 * frequency)
            for step in range(steps, 0, -1):
                T = T_base * step / steps
                board.clear_pulses()
                for _ in range(8):
                    board.add_pulse(T)
                board.run_pulses(1)
                time.sleep(0.05)
            print("Demagnetisation complete.")
        finally:
            self._disable_psu()


# ─── Core loss measurement ────────────────────────────────────────────────────

class CoreLossMeasurement(Measurement):
    """Measure core loss via B-H loop integration (TPT method, Wang et al. 2019).

    Hardware channels
    -----------------
    CH1 (CH_VOLTAGE=0)   : primary voltage    (probe scale from config)
    CH2 (CH_SECONDARY=1) : secondary winding  (probe scale from config)
    CH4 (CH_CURRENT=3)   : primary current    (probe scale from config)

    TPT burst structure — 8 half-periods total (even, balanced net flux)
    ----------------------------------------------------------------------
    [T_stage1, T_half × 7]
      Pulse 0 (+, T_stage1): Stage I  — builds DC pre-magnetisation
      Pulses 1-7 (alternating): Stages II/III — 3.5 AC half-periods
    For dc_bias_A = 0: T_stage1 = T_half (uniform burst).
    For dc_bias_A > 0: T_stage1 = T_half + dc_bias_A × L / V.

    Measurement window: last complete positive + negative half-period pair
    (the 3rd AC cycle, i.e., pulses 5+6 counting from pulse 1).

    Key equations (Wang et al. 2019)
    ---------------------------------
    B(t) = 1/(N2·Ae) × ∫ V_sec dt          [T]
    H(t) = N1 · I(t) / le                  [A/m]
    Q     = |N1/N2 × ∫ I(t)·V_sec(t) dt|  [J/cycle]  (independent of Ae, le)
    """

    # ── private helpers ───────────────────────────────────────────────────────

    def _make_tpt_pulse_train(self, T_stage1, T_half):
        """[T_stage1] + [T_half × 7] — 8 half-periods (always even)."""
        return [T_stage1] + [T_half] * 7

    def _configure_scope(self, voltage, N1, N2, T_total, L_henry=None):
        """Set up all three channels and trigger for the core-loss burst."""
        scope = self.scope

        # Set probe scale for secondary channel (not set by Measurement.__init__)
        scope.set_probe_scale(self.CH_SECONDARY, self.output_voltage_probe_scale)

        # Voltage ranges.  The half-bridge drives the DUT between both rails, so
        # V_pri peaks at ~±voltage (plus ringing overshoot).  These are ± FULL
        # SCALE, not volts/div: a ±10 V swing needs the ±20 V range, not ±5 V.
        V_pri_probe = scope.probe_scale.get(self.CH_VOLTAGE, 1.0) or 1.0
        V_sec_probe = scope.probe_scale.get(self.CH_SECONDARY, 1.0) or 1.0
        V_pri_scale = _smallest_range_fitting(scope, voltage / V_pri_probe)
        V_sec_scale = _smallest_range_fitting(scope, (N2 / N1 * voltage) / V_sec_probe)

        # Current range: ΔI = V·T_half/L is in AMPS; convert to volts at the BNC
        # (amps = volts × probe_scale) before picking the smallest range that fits.
        T_half_est = T_total / 8.0
        cur_probe_scale = scope.probe_scale.get(self.CH_CURRENT, 1.0) or 1.0
        I_peak = (voltage * T_half_est / L_henry
                  if L_henry is not None and L_henry > 0
                  else 0.2)          # conservative fallback
        I_scale = _smallest_range_fitting(scope, I_peak / cur_probe_scale)

        # Note: set_channel_configuration() resets channel labels; set them after.
        scope.set_channel_configuration(self.CH_VOLTAGE,   V_pri_scale, "DC", 0.0)
        scope.set_channel_configuration(self.CH_SECONDARY, V_sec_scale, "DC", 0.0)
        scope.set_channel_configuration(self.CH_CURRENT,   I_scale,     "DC", 0.0)

        # Trigger: 30% of supply voltage, clamped to 3% of actual scope range
        # (PicoScope BNC-level signal is small with high-attenuation differential probes)
        actual_range = scope.get_channel_configuration(self.CH_VOLTAGE).input_voltage_range
        probe_scale = scope.probe_scale.get(self.CH_VOLTAGE, 1.0)
        trigger_level = min(voltage * 0.3, actual_range * probe_scale * 0.03)
        scope.set_channel_label(self.CH_VOLTAGE,   "V_pri")
        scope.set_channel_label(self.CH_SECONDARY, "V_sec")
        scope.set_channel_label(self.CH_CURRENT,   "Current")

        # Trigger on voltage channel — more reliable than current for small signals
        # Use 20% of supply voltage as trigger level (with 10:1 probe, scope sees V/10)
        V_probe_scale = scope.probe_scale.get(self.CH_VOLTAGE, 1.0)
        volt_trigger = voltage * 0.2 / V_probe_scale
        scope.set_rising_trigger(self.CH_VOLTAGE, volt_trigger)

        # 100 samples per half-period, 1.5× total burst for headroom
        T_half_est = T_total / 8.0
        dt_target  = T_half_est / 100.0
        n_samples  = max(1000, int(T_total * 1.5 / dt_target))
        scope.set_number_samples(n_samples)
        scope.set_sampling_time(dt_target)

        print(
            f"Scope config — ranges (± full scale): V_pri ±{V_pri_scale:g} V"
            f"  V_sec ±{V_sec_scale:g} V  I ±{I_scale:g} V"
            f"  V_trig: {volt_trigger:.3f} V (CH_VOLTAGE)"
            f"  acqTime: {T_total * 1.5 * 1e6:.0f} us  samples: {n_samples}"
        )

    def _fire_and_capture(self, pulses, timeout_s=10.0):
        """Load pulse train, arm scope, fire, poll for COMP, read data."""
        board, scope = self.board, self.scope

        board.clear_pulses()
        for T in pulses:
            board.add_pulse(T)

        acq_time = scope.number_samples * scope.sampling_time
        scope.set_acquisition_time(acq_time)
        scope.start_single_acquisition()
        # 2 s delay: scope must fully arm before pulses arrive.
        time.sleep(2)

        board.run_pulses(1)

        deadline = time.monotonic() + timeout_s
        while True:
            time.sleep(0.1)
            state = scope.get_acquisition_state()
            if state == "COMP":
                break
            if time.monotonic() > deadline:
                print(f"ERROR: Scope timed out (last state: {state}).")
                return None

        scope.set_read_timeout(30000)
        df = scope.read_data([self.CH_VOLTAGE, self.CH_SECONDARY, self.CH_CURRENT])
        scope.reset_read_timeout()

        if df is None or df.empty:
            print("ERROR: Scope returned empty data.")
            return None
        missing = {"V_pri", "V_sec", "Current"} - set(df.columns)
        if missing:
            print(f"ERROR: Missing columns {missing} — got {df.columns.tolist()}")
            return None
        return df

    def _find_last_cycle(self, t, V_pri, T_half, voltage, T_total):
        """Return (i_start, i_end) sample indices of the last full +/− half-period pair.

        Uses voltage threshold crossings on V_pri to find pulse edges, which
        works regardless of DC bias (unlike B zero-crossing detection).
        Restricts search to within the burst window (t <= T_total) to avoid
        picking up post-burst freewheeling ringing.
        """
        # Clip search to the burst window — post-burst freewheeling can ring
        # above the threshold and produce false edges beyond T_total.
        n_burst  = int(np.searchsorted(t, T_total))

        # Use 30% of the robust peak (90th percentile) for edge detection.
        # This avoids false thresholds from ringing spikes.
        v_burst = V_pri[:n_burst]
        v_peak = np.percentile(v_burst[v_burst > 0], 90) if np.sum(v_burst > 0) > 10 else np.max(v_burst)
        v_thresh = v_peak * 0.3
        above    = V_pri[:n_burst] > v_thresh

        # Rising edges (False→True) and falling edges (True→False)
        rising  = np.where(~above[:-1] &  above[1:])[0]
        falling = np.where( above[:-1] & ~above[1:])[0]

        if len(rising) < 1 or len(falling) < 1:
            return None, None

        # Reject ringing spikes: keep only positive pulses wider than 30% of T_half.
        dt       = t[1] - t[0]
        min_samp = int(T_half * 0.3 / dt)

        valid_pulses = []   # (rise_idx, fall_idx) for each genuine positive half-period
        for r in rising:
            falls_after = falling[falling > r]
            if len(falls_after) == 0:
                continue
            f = falls_after[0]
            if f - r >= min_samp:
                valid_pulses.append((r, f))

        if not valid_pulses:
            return None, None

        # Last genuine positive half-period
        last_rise, last_fall = valid_pulses[-1]

        # Cycle start = start of last positive half.
        # Cycle end   = next rising edge after last_fall (within burst).
        # The first rising edge after the negative half is either the next positive pulse,
        # or the ringing spike at the end of the negative half — both correctly mark
        # the boundary of one complete +/− cycle.
        next_rises = rising[rising > last_fall]
        i_end   = int(next_rises[0]) if len(next_rises) > 0 else n_burst - 1
        i_start = int(last_rise)

        return i_start, i_end

    def _balance_voltages(self, pulses, T_half, voltage, T_total, L_henry,
                          tol=0.02, max_iter=10):
        """Iteratively adjust CH2 (negative rail) to balance volt-seconds.

        A symmetric H-bridge drive produces zero net current drift over one
        full cycle.  Any mismatch between the positive and negative volt-second
        products shows up as a net drift δI = I[end] − I[start] in the last
        captured cycle.  This method adjusts V_neg (CH2) to minimise δI.

        Parameters
        ----------
        pulses   : list of float — half-period durations [s] for the burst
        T_half   : float         — nominal half-period [s] (for edge filtering)
        voltage  : float         — positive rail voltage [V] (CH1, fixed)
        T_total  : float         — total burst duration [s] (for window clipping)
        L_henry  : float         — inductance estimate [H] (for correction step)
        tol      : float         — relative convergence criterion |δI|/ΔI_peak
        max_iter : int           — maximum iterations

        Returns
        -------
        float — final V_neg value left on PSU CH2
        """
        V_neg = voltage
        for iteration in range(max_iter):
            self.psu.set_source_voltage(self.PSU_CHANNEL_NEG, V_neg)

            df = self._fire_and_capture(pulses)
            if df is None:
                print("  [balance] WARNING: capture failed — stopping.")
                break

            t     = df["time"].to_numpy()
            V_pri = df["V_pri"].to_numpy()
            I     = df["Current"].to_numpy()

            i_start, i_end = self._find_last_cycle(t, V_pri, T_half, voltage, T_total)
            if i_start is None:
                print("  [balance] WARNING: cannot find measurement window — stopping.")
                break

            I_win   = I[i_start:i_end]
            delta_I = float(I[i_end] - I[i_start])
            I_peak  = (np.max(I_win) - np.min(I_win)) / 2.0

            if I_peak < 1e-4:
                print("  [balance] WARNING: current ripple too small — stopping.")
                break

            rel = abs(delta_I) / I_peak
            print(
                f"  [balance] iter {iteration + 1}: V_neg={V_neg:.3f} V  "
                f"dI={delta_I * 1e3:.2f} mA  ({rel * 100:.1f}%)"
            )

            if rel < tol:
                print(f"  [balance] converged in {iteration + 1} iteration(s).")
                break

            correction = 0.5 * delta_I * L_henry / T_half
            V_neg = max(0.5, min(2.0 * voltage, V_neg + correction))

        self.psu.set_source_voltage(self.PSU_CHANNEL_NEG, V_neg)
        return V_neg

    def _extract_core_loss(self, t, V_pri, V_sec, I, N1, N2, Ae, le, T_half, voltage, T_total):
        """Compute B(t), H(t), Q for the steady-state measurement cycle.

        Parameters
        ----------
        t, V_pri, V_sec, I : 1-D numpy arrays (time [s], voltages [V], current [A])
        N1, N2             : primary / secondary turns
        Ae                 : effective cross-section [m²]
        le                 : effective path length [m]
        T_half             : half-period [s] (used to locate the measurement window)

        Returns
        -------
        dict with keys: t, B, H, Q, B_peak, H_peak, B_full, H_full — or None on failure.
        """
        # B(t) from cumulative integration of secondary voltage
        dt   = np.diff(t, prepend=t[0])
        B    = np.cumsum(V_sec * dt) / (N2 * Ae)

        # Remove linear drift (integration offset) so the loop is centred
        p = np.polyfit(t, B, 1)
        B = B - np.polyval(p, t)

        H = N1 * I / le   # A/m

        # Find the last complete +/− cycle using voltage-edge detection
        i_start, i_end = self._find_last_cycle(t, V_pri, T_half, voltage, T_total)
        if i_start is None:
            print("ERROR: Cannot locate the last voltage cycle in V_pri.")
            print(f"  V_pri range: [{V_pri.min():.3f}, {V_pri.max():.3f}] V")
            return None

        if i_end - i_start < 20:
            print(f"ERROR: Measurement window only {i_end - i_start} samples — too short.")
            return None

        print(f"  Measurement window: {t[i_start]*1e6:.1f}–{t[i_end]*1e6:.1f} µs  ({(t[i_end]-t[i_start])*1e6:.1f} µs)")

        t_c   = t[i_start:i_end]
        V_c   = V_sec[i_start:i_end]
        I_c   = I[i_start:i_end]
        B_c   = B[i_start:i_end]
        H_c   = H[i_start:i_end]

        # Q = (N1/N2) × ∫ I(t) · V_sec(t) dt  [J/cycle]
        Q = abs((N1 / N2) * np.trapezoid(I_c * V_c, t_c))

        B_peak = (np.max(B_c) - np.min(B_c)) / 2.0   # half swing [T]
        H_peak = (np.max(H_c) - np.min(H_c)) / 2.0   # half swing [A/m]

        return {
            "t":       t_c,
            "B":       B_c,
            "H":       H_c,
            "Q":       Q,
            "B_peak":  B_peak,
            "H_peak":  H_peak,
            "B_full":  B,
            "H_full":  H,
        }

    # ── public interface ──────────────────────────────────────────────────────

    def measure_core_loss(self, voltage, frequency, N1, N2, core_name,
                          L_henry=None, dc_bias_A=0.0, plot=True, save_csv=None,
                          balance=True, balance_tol=0.02, balance_max_iter=10,
                          target_B_peak_T=None, flux_tol=0.02, flux_max_iter=5):
        """Measure core loss via B-H loop area integration (TPT method).

        Parameters
        ----------
        voltage          : float — initial PSU supply voltage [V]
        frequency        : float — AC switching frequency [Hz]
        N1               : int   — primary winding turns
        N2               : int   — secondary (sense) winding turns
        core_name        : str   — key in CORE_DATABASE (provides Ae, le, volume)
        L_henry          : float — inductance estimate [H]; required for dc_bias and balancing
        dc_bias_A        : float — desired DC pre-magnetisation current [A] (0 = no bias)
        plot             : bool  — display B-H loop and waveforms
        balance          : bool  — iteratively adjust CH2 to equalise volt-seconds
        balance_tol      : float — convergence criterion for balancing (|δI|/ΔI_peak)
        balance_max_iter : int   — maximum balancing iterations
        target_B_peak_T  : float — if set, iteratively adjust voltage until B_peak matches [T]
        flux_tol         : float — convergence criterion for flux targeting (|ΔB|/B_target)
        flux_max_iter    : int   — maximum flux-targeting iterations

        Returns
        -------
        dict | None — keys: Q_cycle [J], P_core [W], B_peak [T],
                      H_peak [A/m], P_density [W/m³]
        """
        core   = CORE_DATABASE[core_name]
        Ae, le = core["Ae"], core["le"]
        Vc     = Ae * le        # approximate core volume [m³]

        T_half = 1.0 / (2.0 * frequency)

        if dc_bias_A > 0 and L_henry is not None:
            T_stage1 = T_half + dc_bias_A * L_henry / voltage
        else:
            T_stage1 = T_half

        pulses  = self._make_tpt_pulse_train(T_stage1, T_half)
        T_total = sum(pulses)

        print(
            f"\nCore loss: V={voltage:.3f} V  f={frequency/1e3:.1f} kHz"
            f"  N1={N1}  N2={N2}  core={core_name}  bias={dc_bias_A:.2f} A"
        )
        if target_B_peak_T is not None:
            print(f"  Target B_peak = {target_B_peak_T*1e3:.1f} mT (iterative flux control)")
        print(
            f"  T_half={T_half*1e6:.1f} us  T_stage1={T_stage1*1e6:.1f} us"
            f"  burst={T_total*1e6:.1f} us  pulses={len(pulses)}"
        )

        df     = None
        result = None

        self._configure_psu(voltage)
        try:
            # ── Phase 1: Flux targeting ───────────────────────────────────────────
            # Iterate voltage to hit the requested B_peak.  Balance is not applied
            # here — we only need a B_peak estimate, which is valid even from a
            # slightly unbalanced waveform.
            if target_B_peak_T is not None:
                print(f"  Phase 1 — Targeting B_peak = {target_B_peak_T*1e3:.1f} mT...")
                for flux_iter in range(flux_max_iter):
                    self._configure_scope(voltage, N1, N2, T_total, L_henry)

                    for attempt in range(5):
                        df = self._fire_and_capture(pulses)
                        if df is None:
                            return None
                        if not _is_clipped(df["Current"].to_numpy()):
                            break
                        cur_range = self.scope.get_channel_configuration(self.CH_CURRENT).input_voltage_range
                        new_range = cur_range * 2.0
                        print(f"  WARNING: Current clipped — retrying at {new_range:.3f} V range")
                        self.scope.set_channel_configuration(self.CH_CURRENT, new_range, "DC", 0.0)
                        self.scope.set_channel_label(self.CH_CURRENT, "Current")
                    else:
                        print("  WARNING: Current still clipped after 5 attempts.")

                    flux_result = self._extract_core_loss(
                        df["time"].to_numpy(), df["V_pri"].to_numpy(),
                        df["V_sec"].to_numpy(), df["Current"].to_numpy(),
                        N1, N2, Ae, le, T_half, voltage, T_total,
                    )
                    if flux_result is None:
                        return None

                    Bp  = flux_result["B_peak"]
                    err = abs(Bp - target_B_peak_T) / target_B_peak_T
                    print(
                        f"  [flux] iter {flux_iter + 1}: V={voltage:.3f} V"
                        f"  B={Bp*1e3:.1f} mT  err={err*100:.1f}%"
                    )
                    if err < flux_tol:
                        print(f"  [flux] converged in {flux_iter + 1} iteration(s).")
                        break
                    if flux_iter < flux_max_iter - 1:
                        voltage = min(30.0, voltage * target_B_peak_T / Bp)
                        self.psu.set_source_voltage(self.PSU_CHANNEL,     voltage)
                        self.psu.set_source_voltage(self.PSU_CHANNEL_NEG, voltage)

            # ── Phase 2: Volt-second balance ──────────────────────────────────────
            # Now that the voltage is set for the correct flux level, symmetrise the
            # positive and negative half-cycles by adjusting the CH2 (negative) rail.
            self._configure_scope(voltage, N1, N2, T_total, L_henry)
            if balance and L_henry is not None:
                print("  Phase 2 — Balancing volt-seconds (adjusting CH2 negative rail)...")
                self._balance_voltages(
                    pulses, T_half, voltage, T_total, L_henry,
                    tol=balance_tol, max_iter=balance_max_iter,
                )
            elif balance and L_henry is None:
                print("  [balance] Skipped — L_henry required for correction step.")

            # ── Phase 3: Final measurement capture ────────────────────────────────
            print("  Phase 3 — Final measurement capture...")
            for attempt in range(5):
                df = self._fire_and_capture(pulses)
                if df is None:
                    return None
                if not _is_clipped(df["Current"].to_numpy()):
                    break
                cur_range = self.scope.get_channel_configuration(self.CH_CURRENT).input_voltage_range
                new_range = cur_range * 2.0
                print(f"  WARNING: Current clipped — retrying at {new_range:.3f} V range")
                self.scope.set_channel_configuration(self.CH_CURRENT, new_range, "DC", 0.0)
                self.scope.set_channel_label(self.CH_CURRENT, "Current")
            else:
                print("  WARNING: Current still clipped after 5 attempts — results may be wrong.")

            t     = df["time"].to_numpy()
            V_pri = df["V_pri"].to_numpy()
            V_sec = df["V_sec"].to_numpy()
            I     = df["Current"].to_numpy()

            print(
                f"  V_pri: [{V_pri.min():.3f}, {V_pri.max():.3f}] V"
                f"  V_sec: [{V_sec.min():.4f}, {V_sec.max():.4f}] V"
                f"  I: [{I.min():.3f}, {I.max():.3f}] A"
            )

            result = self._extract_core_loss(
                t, V_pri, V_sec, I, N1, N2, Ae, le, T_half, voltage, T_total
            )
            if result is None:
                return None

            Q  = result["Q"]
            Bp = result["B_peak"]
            Hp = result["H_peak"]
            P  = Q * frequency

            print(f"  Q_cycle  = {Q * 1e6:.2f} uJ")
            print(f"  B_peak   = {Bp * 1e3:.1f} mT   H_peak = {Hp:.0f} A/m")
            print(f"  P_core   = {P * 1e3:.2f} mW  at {frequency / 1e3:.1f} kHz")
            if Vc > 0:
                print(f"  P_density = {P / Vc / 1e3:.1f} kW/m^3")

            if save_csv:
                df.to_csv(save_csv, index=False)
                print(f"  Waveform saved to {save_csv}")

            if plot:
                _plot_bh_loop(
                    result["t"], result["B"], result["H"],
                    result["B_full"], result["H_full"],
                    t, V_sec, I, Q
                )

            return {
                "Q_cycle":   Q,
                "P_core":    P,
                "B_peak":    Bp,
                "H_peak":    Hp,
                "P_density": P / Vc,
            }
        finally:
            self._disable_psu()


# ─── Plot helpers ──────────────────────────────────────────────────────────────

def _plot_inductance(t, V, I, L, T_half=None):
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 6))

    ax1.plot(t * 1e6, V, linewidth=1.0, color="tab:blue", label="Voltage (V)")
    ax1.set_ylabel("Voltage (V)")
    ax1.legend(loc="upper right")
    ax1.grid(True, alpha=0.4)
    if T_half is not None:
        for k in range(20):
            ax1.axvline(k * T_half * 1e6, color="gray", linewidth=0.5, linestyle="--")

    ax2.plot(t * 1e6, I * 1e3, linewidth=1.0, color="tab:orange", label="Current (mA)")
    ax2.set_ylabel("Current (mA)")
    ax2.set_xlabel("Time (us)")
    ax2.legend(loc="upper right")
    ax2.grid(True, alpha=0.4)

    fig.suptitle(f"Inductance Measurement — L = {L * 1e6:.1f} uH", fontweight="bold")
    plt.tight_layout()
    plt.show()
    plt.close('all')


def _plot_bh_loop(t_c, B_c, H_c, B_full, H_full, t_full, V_sec, I, Q):
    """Three-panel plot: B-H loop, secondary voltage, primary current."""
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15, 5))

    # B-H loop — full waveform in grey, steady-state cycle highlighted
    ax1.plot(H_full * 1e-3, B_full * 1e3, color="lightsteelblue", linewidth=0.7)
    ax1.plot(H_c * 1e-3, B_c * 1e3, color="tab:blue", linewidth=1.5,
             label=f"Q = {Q * 1e6:.1f} uJ")
    ax1.fill(H_c * 1e-3, B_c * 1e3, alpha=0.15, color="tab:blue")
    ax1.set_xlabel("H (kA/m)")
    ax1.set_ylabel("B (mT)")
    ax1.set_title("B-H Loop")
    ax1.legend()
    ax1.grid(True, alpha=0.4)

    # Secondary voltage vs time
    ax2.plot(t_full * 1e6, V_sec, color="tab:green", linewidth=1.0)
    ax2.axvspan(t_c[0] * 1e6, t_c[-1] * 1e6, alpha=0.12, color="tab:blue",
                label="Measurement window")
    ax2.set_xlabel("Time (us)")
    ax2.set_ylabel("V_sec (V)")
    ax2.set_title("Secondary Voltage")
    ax2.legend()
    ax2.grid(True, alpha=0.4)

    # Primary current vs time
    ax3.plot(t_full * 1e6, I * 1e3, color="tab:orange", linewidth=1.0)
    ax3.axvspan(t_c[0] * 1e6, t_c[-1] * 1e6, alpha=0.12, color="tab:blue",
                label="Measurement window")
    ax3.set_xlabel("Time (us)")
    ax3.set_ylabel("Current (mA)")
    ax3.set_title("Primary Current")
    ax3.legend()
    ax3.grid(True, alpha=0.4)

    fig.suptitle(f"Core Loss Measurement — Q = {Q * 1e6:.1f} uJ", fontweight="bold")
    plt.tight_layout()
    plt.show()
    plt.close('all')


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Fixed test-fixture parameters ────────────────────────────────────────
    CORE     = "T26"
    MATERIAL = "3C90"
    N_TURNS  = 10   # primary turns
    N2       = 10   # secondary (sense) winding turns

    core     = CORE_DATABASE[CORE]
    Ae, le   = core["Ae"], core["le"]

    L_theory = theoretical_inductance(CORE, MATERIAL, N_TURNS)
    print(f"\nCore: {CORE}/{MATERIAL}  N1={N_TURNS}  N2={N2}")
    print(f"  Ae = {Ae * 1e6:.1f} mm²   le = {le * 1e3:.1f} mm")
    print(f"  Theoretical L = {L_theory * 1e6:.1f} uH")

    # ── User inputs: target flux density and switching frequency ─────────────
    print()
    B_peak_mT    = float(input("Enter peak flux density [mT]: "))
    frequency_kHz = float(input("Enter test frequency    [kHz]: "))

    B_peak_T  = B_peak_mT  * 1e-3
    FREQ      = frequency_kHz * 1e3
    T_half    = 1.0 / (2.0 * FREQ)
    VOLTAGE   = flux_to_voltage(B_peak_T, N_TURNS, Ae, FREQ)

    print()
    print(f"  B_peak   = {B_peak_mT:.1f} mT")
    print(f"  Freq     = {frequency_kHz:.1f} kHz   T_half = {T_half * 1e6:.2f} us")
    print(f"  Voltage  = {VOLTAGE:.3f} V  (calculated from V = 4·B·N·Ae·f)")

    PSU_MAX_V = 30.0
    if VOLTAGE > PSU_MAX_V:
        print(f"\nERROR: Required voltage ({VOLTAGE:.1f} V) exceeds PSU limit ({PSU_MAX_V} V).")
        print("       Reduce B_peak or frequency.")
        raise SystemExit(1)

    mat    = MATERIAL_DATABASE[MATERIAL]
    B_sat  = mat["B_sat"]
    if B_peak_T >= B_sat:
        print(f"\nWARNING: B_peak ({B_peak_mT:.0f} mT) >= B_sat ({B_sat*1e3:.0f} mT) for {MATERIAL}.")
        print("         Core will saturate.  Proceed with caution.")

    # ── Measure inductance at low flux (10 V / 5 kHz) ───────────────────────
    VOLTAGE_L = 10.0
    FREQ_L    = 5e3

    meas = InductanceMeasurement.from_config("hardware_configuration.json")
    L = meas.measure_inductance(VOLTAGE_L, FREQ_L, num_pulses=8, plot=False,
                                L_estimate=L_theory)

    if L is not None:
        error_pct = (L - L_theory) / L_theory * 100
        print(f"Measured  L = {L * 1e6:.1f} uH  ({error_pct:+.1f}% vs theory)")

    # Release hardware before opening new connections
    meas.close()

    # ── Core loss measurement at user-specified flux and frequency ────────────
    loss_meas = CoreLossMeasurement.from_config("hardware_configuration.json")
    result = loss_meas.measure_core_loss(
        voltage         = VOLTAGE,
        frequency       = FREQ,
        N1              = N_TURNS,
        N2              = N2,
        core_name       = CORE,
        L_henry         = L,
        dc_bias_A       = 0.0,
        plot            = False,
        save_csv        = "../core_loss_waveform.csv",
        target_B_peak_T = B_peak_T,   # iteratively adjust voltage to hit the requested flux
    )
    if result:
        print(f"\nP_core = {result['P_core']*1e3:.2f} mW  "
              f"B_peak = {result['B_peak']*1e3:.1f} mT")
