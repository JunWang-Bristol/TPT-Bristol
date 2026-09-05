"""Planning the excitation: what rails and what pulse list a point needs.

Pure functions — they decide whether a point is *feasible* before the bench is
touched, which is what lets the engine refuse an impossible point at validation
instead of discovering it half-way through a sweep.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

DEFAULT_DEADTIME_S = 500e-9
DEFAULT_PULSES = 8

#: How the DC operating point is established.  See :func:`pulse_train`.
BIAS_METHODS = ("first_pulse", "distributed")

#: ``first_pulse`` stays the default.  ``distributed`` measured better
#: open-loop on a sister rig (+24.2 ± 3.4 % overshoot vs +34.8 ± 2.8 % at
#: 50 kHz/100 mA, n=8 each) and was briefly made the default on that evidence.
#: A production sweep showed why that was wrong: the evidence came from
#: 24-pulse trains, but at the default 8 pulses the ramp gets only 3 coarse
#: steps, biased points saturated, and the run collapsed from ~23 accepted
#: points to 9 with demagnetisation left failing at ~380 mA residual.  So the
#: default follows the evidence *at the default train length*; recipes that set
#: ``n_pulses >= 16`` are the ones that should opt into ``distributed``.
DEFAULT_BIAS_METHOD = "first_pulse"
DEFAULT_BIAS_CYCLES = 4
#: Fraction of the bias volt-seconds placed in the opening pulse.  1.0 is
#: ``first_pulse``, 0.0 the pure staircase; see :func:`pulse_train`.
DEFAULT_BIAS_LEAD = 0.0


def bias_volt_seconds(dc_bias_A, L_H, voltage):
    """Volt-seconds needed to ramp the magnetising current to ``dc_bias_A``."""
    if not dc_bias_A or not L_H or not voltage:
        return 0.0
    return abs(float(dc_bias_A)) * float(L_H) / float(voltage)


def ramp_cycles(bias_method, bias_cycles, n_pulses):
    """How many cycles of the train carry the bias ramp.

    The ramp must leave at least one full symmetric cycle behind it, because
    the last full cycle is the one analysed — a ramp running to the end of the
    train would be measured mid-climb.  With the default 8 pulses (4 cycles)
    that caps the ramp at 3; a recipe wanting a gentler ramp raises
    ``n_pulses``, which the firmware supports up to 256.
    """
    if bias_method == "first_pulse":
        return 1
    n_cycles = max(1, int(n_pulses) // 2)
    return int(max(1, min(int(bias_cycles), n_cycles - 1)))


def flux_to_voltage(B_peak_T, N1, Ae, frequency, deadtime_s=DEFAULT_DEADTIME_S):
    """Rail voltage needed for a target peak flux density.

    Volt-second balance over one half-period of a symmetric square drive:

        V · T_eff = 2 · B̂ · N1 · Ae ,  T_eff = 1/(2f) − deadtime

    The firmware's deadtime shortens the drive window, so ignoring it
    under-drives the core — at 200 kHz the 500 ns deadtime is 20 % of the
    half-period, which is why that corner is marginal on this rig.  Residual
    resistive drops cause a further shortfall; B is always taken from the
    *measured* sense-winding integral, never from this estimate.
    """
    T_half = 1.0 / (2.0 * frequency)
    T_eff = T_half - deadtime_s
    if T_eff <= 0:
        raise ValueError(
            f"half-period {T_half*1e9:.0f} ns is not longer than the "
            f"{deadtime_s*1e9:.0f} ns deadtime")
    return 2.0 * B_peak_T * N1 * Ae / T_eff


def voltage_to_flux(V, N1, Ae, frequency, deadtime_s=DEFAULT_DEADTIME_S):
    """Inverse of :func:`flux_to_voltage` — the B̂ a given rail can reach."""
    T_eff = 1.0 / (2.0 * frequency) - deadtime_s
    return V * T_eff / (2.0 * N1 * Ae)


def pulse_train(frequency, *, n_pulses=DEFAULT_PULSES, dc_bias_A=0.0,
                L_H=None, voltage=None, duty=0.5,
                bias_method=DEFAULT_BIAS_METHOD, bias_cycles=DEFAULT_BIAS_CYCLES,
                bias_lead=DEFAULT_BIAS_LEAD, bias_dt_total=None):
    """The half-period list for one TPT burst.

    The firmware alternates polarity by list *index*: even entries drive the
    high side, odd entries the low side.  So the list is always
    ``[+, −, +, −, …]`` and everything below is expressed as which entries get
    lengthened.

    **Centring.**  The burst starts from B = 0, so the first pulse is HALF a
    half-period.  A full-length first pulse drives the flux 0 → +2·B̂, and the
    symmetric pulses that follow then swing it between 0 and +2·B̂: the right
    amplitude, but the whole minor loop offset by one B̂.  That shows up as a
    spurious ``H_dc`` on every nominally unbiased point — it is not volt-second
    imbalance and not a channel offset.  The prediction is the whole of it::

        dI = V·T_pos/(2L) = 11.58 V · 10 µs / (2 · 404 µH) = 0.143 A
        H  = N1·dI/le     = 10 · 0.143 / 0.0644            = 22.2 A/m

    **DC bias**, on top of that centring, by one of two methods:

    ``first_pulse``
        The classical TPT (Wang/Yuan/Rasekh, IECON 2020, Fig. 10): put every
        bias volt-second into the first pulse, ``T₁ += I_dc·L/V``.  One shot,
        minimum burst length, operating point established before any
        measurement cycle.  Its weakness is that the whole ramp happens in one
        stretch, during which the incremental inductance is falling — so the
        achieved bias is set by an L the planner does not know.

    ``distributed``
        Spend the same volt-seconds over ``bias_cycles`` cycles, lengthening
        one half-period of each by ``δt = I_dc·L/(V·bias_cycles)``.  Identical
        net volt-seconds and the same end state, but the flux climbs in small
        steps with the AC swing superimposed throughout.

    *Peak* flux is identical either way — same volt-seconds — so neither is
    gentler on saturation headroom.  What ``distributed`` buys is a conduction
    interval pinned near T_half however large the bias gets, where
    ``first_pulse``'s opening pulse grows without bound (at 200 mA it is
    already twice a half-period: a low-frequency excursion in the middle of a
    high-frequency measurement).  What it costs is swept flux — the loop walks
    up from centre rather than starting inside the final loop.

    ``bias_lead`` interpolates: the fraction of the bias volt-seconds placed in
    the opening pulse.  ``1.0`` reproduces ``first_pulse``, ``0.0`` is the pure
    staircase.  Both leave every cycle after the ramp strictly symmetric, so
    the last full cycle — the one analysed — is a clean minor loop either way.

    A NEGATIVE ``dc_bias_A`` lengthens the odd (negative) entries instead, so
    the bias lands where it was asked for.  Taking ``abs()`` and always
    lengthening the first pulse silently turns a request for −100 mA into
    +100 mA.

    ``duty`` ≠ 0.5 makes the half-periods unequal while keeping the *period*
    fixed.  It is only a valid excitation when the rails are asymmetric to
    match — the negative rail must be trimmed by ``duty/(1-duty)``.
    """
    if bias_method not in BIAS_METHODS:
        raise ValueError(f"unknown bias_method {bias_method!r}; "
                         f"expected one of {BIAS_METHODS}")

    T = 1.0 / frequency
    T_pos = T * duty
    T_neg = T * (1.0 - duty)

    if bias_dt_total is None:
        bias_dt_total = bias_volt_seconds(dc_bias_A, L_H, voltage)
    negative_bias = float(dc_bias_A or 0.0) < 0.0

    # Which entries carry the bias, and how many of them.
    n_ramp = (1 if bias_dt_total <= 0.0
              else ramp_cycles(bias_method, bias_cycles, n_pulses))
    lead = 1.0 if bias_method == "first_pulse" else min(max(bias_lead, 0.0), 1.0)
    lead_dt = bias_dt_total * lead
    dt_each = (bias_dt_total - lead_dt) / n_ramp if bias_dt_total else 0.0
    # The lead goes on the first half-period of the sign the bias asks for:
    # entry 0 drives the flux up, entry 1 drives it down.
    lead_index = 1 if negative_bias else 0

    pulses = []
    for k in range(n_pulses):
        base = T_neg if k % 2 else T_pos
        if k == 0:
            base = T_pos / 2.0          # centring, before any bias
        if k == lead_index:
            base += lead_dt
        cycle = k // 2
        carries = ((k % 2 == 1) if negative_bias else (k % 2 == 0))
        if carries and cycle < n_ramp:
            base += dt_each
        pulses.append(base)
    return pulses


@dataclass
class DrivePlan:
    """Everything needed to execute one point, plus why it may be refused."""

    frequency_Hz: float
    B_target_T: Optional[float]
    dc_bias_A: float
    duty: float
    voltage_V: float
    pulses: List[float]
    i_expected_A: float
    expected_vpri_pp: float
    feasible: bool = True
    reason: str = ""

    @property
    def tag(self):
        # ASCII only: this string goes to a Windows console (cp1252) and into
        # waveform filenames, and both mangle anything else.
        parts = [f"{self.frequency_Hz/1e3:.0f}k"]
        if self.B_target_T is not None:
            parts.append(f"{self.B_target_T*1e3:.0f}mT")
        if self.dc_bias_A:
            parts.append(f"{self.dc_bias_A*1e3:.0f}mA")
        if abs(self.duty - 0.5) > 1e-9:
            parts.append(f"d{self.duty:.2f}")
        return "-".join(parts)

    def to_dict(self):
        return {"frequency_Hz": self.frequency_Hz, "B_target_T": self.B_target_T,
                "dc_bias_A": self.dc_bias_A, "duty": self.duty,
                "voltage_V": self.voltage_V, "n_pulses": len(self.pulses),
                "T_stage1_s": self.pulses[0] if self.pulses else None,
                "feasible": self.feasible, "reason": self.reason}


def plan_point(dut, limits, *, frequency, B_target_T=None, voltage_V=None,
               dc_bias_A=0.0, duty=0.5, n_pulses=DEFAULT_PULSES,
               bias_method=DEFAULT_BIAS_METHOD,
               bias_cycles=DEFAULT_BIAS_CYCLES,
               bias_lead=DEFAULT_BIAS_LEAD) -> DrivePlan:
    """Build a :class:`DrivePlan`, marking it infeasible rather than clamping it.

    Refusing is the whole point: a point silently run at a lower voltage than
    it asked for produces a real measurement of the *wrong* operating point,
    which is worse than a labelled gap.
    """
    reason = ""
    feasible = True

    if not (limits.f_min_Hz <= frequency <= limits.f_max_Hz):
        feasible = False
        reason = (f"{frequency/1e3:.0f} kHz outside the validated "
                  f"{limits.f_min_Hz/1e3:.0f}-{limits.f_max_Hz/1e3:.0f} kHz band")

    if voltage_V is None:
        if B_target_T is None:
            raise ValueError("plan_point needs either B_target_T or voltage_V")
        try:
            voltage_V = flux_to_voltage(B_target_T, dut.N1, dut.Ae, frequency,
                                        limits.deadtime_s)
        except ValueError as exc:
            return DrivePlan(frequency, B_target_T, dc_bias_A, duty, 0.0, [],
                             0.0, 0.0, False, str(exc))

    if feasible and voltage_V > limits.psu_max_V:
        feasible = False
        reason = (f"needs {voltage_V:.1f} V, PSU ceiling is "
                  f"{limits.psu_max_V:.0f} V")

    # Asymmetric excitation needs the negative rail trimmed to duty/(1-duty)
    # so V·T balances in both directions; without that the flux walks and the
    # core saturates. That trimmed rail has to fit under the ceiling too, and
    # it is the binding one for duty > 0.5 — at duty 0.7 it is 2.33x the
    # positive rail. Checked here so `validate` refuses exactly what `run`
    # would, rather than the run discovering it at the supply.
    if feasible and abs(duty - 0.5) > 1e-9:
        if not 0.0 < duty < 1.0:
            feasible = False
            reason = f"duty {duty} is not in (0, 1)"
        else:
            v_neg = voltage_V * duty / (1.0 - duty)
            if v_neg > limits.psu_max_V:
                feasible = False
                reason = (f"duty {duty:.2f} needs a {v_neg:.1f} V negative rail "
                          f"to balance volt-seconds, PSU ceiling is "
                          f"{limits.psu_max_V:.0f} V")

    if feasible and dut.B_sat and B_target_T and B_target_T >= dut.B_sat:
        feasible = False
        reason = (f"B target {B_target_T*1e3:.0f} mT is at or above B_sat "
                  f"{dut.B_sat*1e3:.0f} mT for {dut.material}")

    L = dut.L_estimate_H
    if dc_bias_A and not L:
        feasible = False
        reason = "dc bias requested but the DUT has no L_estimate_H"

    if feasible and bias_method not in BIAS_METHODS:
        feasible = False
        reason = (f"unknown bias_method {bias_method!r}; expected one of "
                  f"{list(BIAS_METHODS)}")

    pulses = pulse_train(frequency, n_pulses=n_pulses, dc_bias_A=dc_bias_A,
                         L_H=L, voltage=voltage_V, duty=duty,
                         bias_method=bias_method, bias_cycles=bias_cycles,
                         bias_lead=bias_lead) if feasible else []

    T_half = 1.0 / (2.0 * frequency)
    i_ac = voltage_V * T_half / L if L else 0.5
    i_expected = abs(dc_bias_A) + i_ac

    return DrivePlan(frequency, B_target_T, dc_bias_A, duty, voltage_V, pulses,
                     i_expected, 2.0 * voltage_V, feasible, reason)
