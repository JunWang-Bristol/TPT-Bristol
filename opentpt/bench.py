"""The instrument layer: startup checks, one capture, current auto-ranging.

The drivers underneath (``src/board.py``, ``src/oscilloscope.py``,
``src/power_supply.py``) are unchanged — what this adds is the handful of
things the bench taught us the hard way:

* **Startup checks that must pass before any point is measured.**  Firmware on
  the board drifts from the source tree; the PSU's OVP can be left below the
  target rail by an interrupted test run, after which ``set_source_voltage``
  silently does nothing and the rail reads 0 V.  Both are indistinguishable
  from a hardware fault once a sweep is running, so they are checked up front.

* **A timebase derived from the half-period, never from a fixed sample count.**
  Fixing ``n_samples`` and solving for ``dt`` drove the timebase to 7-8 ns at
  200 kHz — past what the 2408B sustains with three channels — and the capture
  came back as noise that still looked like data.

* **Auto-ranging that also detects a missed burst.**  A capture that misses the
  train entirely returns a flat V_pri and then produces a confident, wrong
  number downstream.  Widening the range fixes clipping; only a swing check
  catches the miss.

:class:`ReplayBench` implements the same interface over saved CSVs, so the
engine, the procedures and the QC policy can all be exercised — and regression
tested — with no hardware attached.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np

from . import qc

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Capture:
    """One acquisition, in physical units at the probe tip."""

    t: np.ndarray
    v_pri: np.ndarray
    v_sec: np.ndarray
    current: np.ndarray
    i_range_V: float = 0.0        # scope range on the current channel, at the BNC
    i_full_scale_A: float = 0.0   # that range converted to amps
    clipped: bool = False
    dt_s: float = 0.0
    n_samples: int = 0

    @property
    def current_lsb_A(self):
        """Amps per ADC code — the 2408B has 256 codes across the range."""
        return 2.0 * self.i_full_scale_A / 256.0 if self.i_full_scale_A else 0.0

    def to_frame(self):
        import pandas as pd
        return pd.DataFrame({"time": self.t, "V_pri": self.v_pri,
                             "V_sec": self.v_sec, "Current": self.current})


class BenchError(RuntimeError):
    """The bench cannot be used as configured."""


# ─── Real hardware ────────────────────────────────────────────────────────────

class HardwareBench:
    """Half-bridge + scope + PSU, driven as one unit."""

    CH_VOLTAGE = 0      # CH A — primary voltage
    CH_SECONDARY = 1    # CH B — secondary (sense) winding
    CH_CURRENT = 3      # CH D — primary current
    # CH C (index 2) is deliberately left free: it is the channel to put on TP4
    # to split an MCU fault from a power-stage fault without disassembly.

    PSU_POS = 1
    PSU_NEG = 2
    PSU_OVP_MAX = 31.0

    SAMPLES_PER_HALF_PERIOD = 50
    MIN_DT_S = 16e-9            # below this the 2408B cannot sustain 3 channels

    def __init__(self, config_path=None, bus=None):
        sys.path.insert(0, str(_ROOT / "src"))
        from power_supply import PowerSupply
        from oscilloscope import Oscilloscope
        from board import Board
        import json

        cfg_path = Path(config_path or _ROOT / "hardware_configuration.json")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.config = cfg
        self.bus = bus

        self.psu = PowerSupply.factory(cfg["power_supply"], cfg["power_supply_port"])
        self.scope = Oscilloscope.factory(cfg["oscilloscope"], cfg["oscilloscope_port"])
        self.board = Board.factory(cfg["board"], cfg["board_port"])

        self.v_pri_probe = float(cfg.get("input_voltage_probe_scale", 10))
        self.v_sec_probe = float(cfg.get("output_voltage_probe_scale", 10))
        self.i_probe = float(cfg.get("current_probe_scale", 2.0))

        for ch, scale in ((self.CH_VOLTAGE, self.v_pri_probe),
                          (self.CH_SECONDARY, self.v_sec_probe),
                          (self.CH_CURRENT, self.i_probe)):
            self.scope.set_probe_scale(ch, scale)
        if hasattr(self.scope, "set_probe_units"):
            self.scope.set_probe_units(self.CH_CURRENT, "A")

        # Current-channel timing offset, in seconds, positive meaning the
        # current record lags the voltage records.  Core loss is the small
        # IN-PHASE part of an almost purely reactive signal, so a fixed skew
        # rotates reactive power into apparent loss: P_err ~ S·ω·τ.  At 100 kHz
        # with S ~ 2 VA against a ~0.5 W loss, 10 ns is already ~3 % and 100 ns
        # is ~30 %.  Clamp probes routinely carry 10-30 ns of propagation
        # delay, so leaving this at zero is an assumption, not a default.
        # Calibrate against an air coil, whose true loop area is exactly zero.
        # Applied in :meth:`_deskew` on the captured array, NOT through
        # ``scope.set_channel_skew`` — see that method for why the driver's
        # implementation is unusable for a calibrated (non-round) value.
        self.i_skew_s = float(cfg.get("current_channel_skew_s", 0.0) or 0.0)

    # ── startup ──────────────────────────────────────────────────────────────

    def startup_checks(self, limits) -> List[dict]:
        """Verify the bench before the first point.  Returns one dict per check.

        Any check with ``ok=False`` and ``fatal=True`` must abort the run: past
        that point every symptom looks like a measurement problem.
        """
        results = []

        def record(name, ok, detail, fatal=False):
            r = {"check": name, "ok": bool(ok), "detail": detail, "fatal": fatal}
            results.append(r)
            if self.bus:
                self.bus.emit("bench_check", **r)
            return r

        try:
            idn = self.board.get_identification()
            min_period = self.board.get_minimum_period()
            ok = "OPEN_TPT" in str(idn)
            record("firmware", ok, f"{idn} · min period {float(min_period)*1e9:.0f} ns",
                   fatal=not ok)
        except Exception as exc:                      # noqa: BLE001
            record("firmware", False, f"board did not answer: {exc!r}", fatal=True)

        for ch in (self.PSU_POS, self.PSU_NEG):
            try:
                ovp = float(self.psu.get_voltage_limit(ch))
                ok = ovp >= limits.psu_max_V
                detail = f"CH{ch} OVP {ovp:.2f} V (need >= {limits.psu_max_V:.0f} V)"
                if not ok:
                    self.psu.set_voltage_limit(ch, self.PSU_OVP_MAX)
                    ovp = float(self.psu.get_voltage_limit(ch))
                    ok = ovp >= limits.psu_max_V
                    detail += f" — raised to {ovp:.2f} V"
                record(f"psu_ovp_ch{ch}", ok, detail, fatal=not ok)
            except Exception as exc:                  # noqa: BLE001
                record(f"psu_ovp_ch{ch}", False, f"PSU did not answer: {exc!r}",
                       fatal=True)

        # The isolated 12 V gate-driver supply cannot be read over any bus, and
        # without it the board still counts pulse trains while V_pri stays flat.
        # It is verified implicitly by the first capture's swing gate, so it is
        # recorded here as a reminder rather than silently assumed.
        record("gate_drive", True,
               "not electrically readable — verified by the first capture's "
               "V_pri swing gate (a flat V_pri means the 12 V driver rail is dead)")
        return results

    # ── power ────────────────────────────────────────────────────────────────

    def set_rails(self, voltage, i_limit, v_neg=None):
        """Set the two rails; equal by default, they match to ~0.3 % on this rig.

        ``v_neg`` exists for asymmetric excitation.  With duty ≠ 0.5 the two
        half-periods differ in length, so equal rails leave a net volt-second
        per cycle: the flux walks, the core saturates within a few cycles and
        the minor loop stops being valid.  Balance needs
        ``V_neg · T_neg = V_pos · T_pos``, i.e. a negative rail trimmed by
        ``duty / (1 - duty)`` — which is only possible because the two supply
        channels are independent.
        """
        targets = {self.PSU_POS: voltage,
                   self.PSU_NEG: voltage if v_neg is None else v_neg}
        for ch, V in targets.items():
            self.psu.set_source_voltage(ch, V)
            self.psu.set_current_limit(ch, i_limit)
            self.psu.enable_output(ch)
        time.sleep(0.5)

    def rails_off(self):
        for ch in (self.PSU_POS, self.PSU_NEG):
            try:
                self.psu.disable_output(ch)
            except Exception:                          # noqa: BLE001
                pass

    # ── capture ──────────────────────────────────────────────────────────────

    def capture(self, pulses, i_range_V, *, frequency=None, timeout_s=10.0,
                v_range=5.0) -> Optional[Capture]:
        """Fire one pulse train and read three channels back."""
        board, scope = self.board, self.scope
        T_half = 1.0 / (2.0 * frequency) if frequency else min(pulses)

        try:
            board.flush_buffer()
        except Exception:                              # noqa: BLE001
            pass
        board.clear_pulses()
        for p in pulses:
            board.add_pulse(p)

        total = float(sum(pulses))
        dt = max(T_half / self.SAMPLES_PER_HALF_PERIOD, self.MIN_DT_S)
        n_samples = int(np.clip((total * 1.4) / dt, 1000, 8000))

        scope.set_probe_scale(self.CH_VOLTAGE, self.v_pri_probe)
        scope.set_probe_scale(self.CH_SECONDARY, self.v_sec_probe)
        scope.set_probe_scale(self.CH_CURRENT, self.i_probe)
        scope.set_channel_configuration(self.CH_VOLTAGE, v_range, "DC", 0.0)
        scope.set_channel_configuration(self.CH_SECONDARY, v_range, "DC", 0.0)
        scope.set_channel_configuration(self.CH_CURRENT, i_range_V, "DC", 0.0)
        scope.set_channel_label(self.CH_VOLTAGE, "V_pri")
        scope.set_channel_label(self.CH_SECONDARY, "V_sec")
        scope.set_channel_label(self.CH_CURRENT, "Current")
        scope.set_number_samples(n_samples)
        scope.set_sampling_time(dt)
        scope.set_number_pre_trigger_samples(200)
        scope.set_rising_trigger(self.CH_VOLTAGE, 0.2, timeout=3000)

        scope.start_single_acquisition()
        time.sleep(1.0)
        board.run_pulses(1)

        deadline = time.monotonic() + timeout_s
        while scope.get_acquisition_state() != "COMP":
            if time.monotonic() > deadline:
                return None
            time.sleep(0.1)

        df = scope.read_data([self.CH_VOLTAGE, self.CH_SECONDARY, self.CH_CURRENT])
        if df is None or df.empty:
            return None

        t = df["time"].to_numpy()
        current = self._deskew(t, df["Current"].to_numpy())
        full = i_range_V * self.i_probe
        return Capture(
            t=t,
            v_pri=df["V_pri"].to_numpy(),
            v_sec=df["V_sec"].to_numpy(),
            current=current,
            i_range_V=i_range_V,
            i_full_scale_A=full,
            clipped=qc.is_clipped(current, full),
            dt_s=dt, n_samples=n_samples,
        )

    def _deskew(self, t, current):
        """Delay the current record by ``i_skew_s``, by interpolation.

        Deliberately NOT ``scope.set_channel_skew``.  That driver implements a
        skew by resampling the whole capture onto a grid of
        ``gcd(skew, sampling_interval)`` — for an arbitrary calibrated value
        the gcd collapses to a few picoseconds and the upsample factor runs to
        10^5-10^6.  A 4000-sample capture then becomes hundreds of millions of
        points and the run hangs; measured here, 5.842 ns against a 2 us
        interval asks for 4x10^9 samples.  Only skews that happen to divide the
        sample interval neatly are survivable, which a calibration result will
        not generally do.

        Interpolating onto ``t - skew`` costs one pass, is exact to the
        interpolation, and does not care whether the skew is a nice number.
        Edges are held rather than wrapped: a roll would move the end of the
        record to the beginning, which is not what a delay does.
        """
        if not self.i_skew_s:
            return current
        return np.interp(t - self.i_skew_s, t, current,
                         left=current[0], right=current[-1])

    def current_ranges(self):
        return sorted(self.scope.get_input_voltage_ranges())

    def acquire(self, pulses, *, frequency, i_expected_A, expected_vpri_pp,
                retries=3, headroom=2.5) -> Optional[Capture]:
        """Capture with the current range widened until nothing clips.

        Guards two independent failure modes with one loop: clipping (widen and
        retry) and a capture that missed the burst (retry at the same range —
        widening would not help and would only cost resolution).
        """
        ranges = self.current_ranges()
        need = abs(i_expected_A) * headroom / self.i_probe
        candidates = [r for r in ranges if r >= need] or [ranges[-1]]

        last = None
        for r in candidates:
            for _ in range(retries):
                cap = self.capture(pulses, r, frequency=frequency)
                if cap is None:
                    continue
                last = cap
                if float(np.ptp(cap.v_pri)) < 0.5 * expected_vpri_pp:
                    continue                    # missed the burst — try again
                if not cap.clipped:
                    return cap
                break                           # clipped — widen the range
        return last

    def demagnetize(self, voltage=5.0, frequency=5e3, steps=8, i_limit=1.0):
        """Decaying-amplitude AC train, then rails off."""
        self.set_rails(voltage, i_limit)
        try:
            T_base = 1.0 / (2.0 * frequency)
            for step in range(steps, 0, -1):
                self.board.clear_pulses()
                for _ in range(8):
                    self.board.add_pulse(T_base * step / steps)
                self.board.run_pulses(1)
                time.sleep(0.05)
        finally:
            self.rails_off()

    def close(self):
        self.rails_off()
        for obj in (self.psu, self.scope, self.board):
            closer = getattr(obj, "close", None)
            if closer is None:
                session = getattr(obj, "visa_session", None)
                closer = getattr(session, "close", None) if session else None
            if closer:
                try:
                    closer()
                except Exception:                      # noqa: BLE001
                    pass


# ─── Offline replay ───────────────────────────────────────────────────────────

class ReplayBench:
    """A bench backed by saved CSVs — for tests, dry runs and demos.

    Serves the closest stored capture to whatever the engine asks for, so a
    whole procedure can be exercised end to end without instruments.  It does
    not pretend to simulate physics: if no capture is close enough, it returns
    ``None`` and the engine records the point as a capture failure, exactly as
    it would on a bench that missed the burst.
    """

    CH_VOLTAGE, CH_SECONDARY, CH_CURRENT = 0, 1, 3
    PSU_POS, PSU_NEG = 1, 2

    def __init__(self, captures, *, i_probe=2.0, bus=None, tolerance=0.35):
        """``captures``: list of ``(frequency_Hz, csv_path)``."""
        import pandas as pd
        self.bus = bus
        self.i_probe = i_probe
        self.tolerance = tolerance
        self._store = []
        for freq, path in captures:
            df = pd.read_csv(path)
            self._store.append((float(freq), df, str(path)))
        self.rail_V = 0.0
        self.calls = []

    def startup_checks(self, limits):
        r = {"check": "replay", "ok": True, "fatal": False,
             "detail": f"{len(self._store)} stored captures — no hardware in use"}
        if self.bus:
            self.bus.emit("bench_check", **r)
        return [r]

    def set_rails(self, voltage, i_limit, v_neg=None):
        self.rail_V = voltage
        self.rail_neg_V = voltage if v_neg is None else v_neg

    def rails_off(self):
        self.rail_V = 0.0

    def current_ranges(self):
        return [0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0]

    def capture(self, pulses, i_range_V, *, frequency=None, **kw):
        self.calls.append({"frequency": frequency, "i_range": i_range_V,
                           "n_pulses": len(pulses)})
        if frequency is None:
            return None
        best, err = None, None
        for f, df, path in self._store:
            e = abs(f - frequency) / frequency
            if err is None or e < err:
                best, err = (df, path), e
        if best is None or err > self.tolerance:
            return None
        df, path = best
        current = df["Current"].to_numpy()
        full = i_range_V * self.i_probe
        t = df["time"].to_numpy()
        return Capture(
            t=t, v_pri=df["V_pri"].to_numpy(), v_sec=df["V_sec"].to_numpy(),
            current=current, i_range_V=i_range_V, i_full_scale_A=full,
            clipped=qc.is_clipped(current, full),
            dt_s=float(t[1] - t[0]) if t.size > 1 else 0.0, n_samples=int(t.size),
        )

    def acquire(self, pulses, *, frequency, i_expected_A, expected_vpri_pp,
                retries=3, headroom=2.5):
        ranges = self.current_ranges()
        need = abs(i_expected_A) * headroom / self.i_probe
        candidates = [r for r in ranges if r >= need] or [ranges[-1]]
        last = None
        for r in candidates:
            cap = self.capture(pulses, r, frequency=frequency)
            if cap is None:
                continue
            last = cap
            if not cap.clipped:
                return cap
        return last

    def demagnetize(self, **kw):
        self.calls.append({"demagnetize": kw})

    def close(self):
        pass
