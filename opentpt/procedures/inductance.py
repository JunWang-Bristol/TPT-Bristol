"""``inductance_vs_bias`` and ``saturation_curve`` — the deep-drive procedures.

Both walk *up* into saturation, so both are guarded: the ramp stops at the
first sign of the knee rather than pushing on to a number that is no longer a
valid measurement.

The knee has three independent tells on this rig, and the guard uses all of
them because any one alone gives false negatives:

* inductance (or secant permeability) has fallen by the configured percentage;
* ripple current has ballooned past ``max_ipp_ratio`` — 4-8× is typical once
  the core goes over, e.g. 332 mA unbiased to 2535 mA;
* measured loss goes **non-monotonic** — it rises to ~131 kW/m³ and then falls
  to 108 while the drive is still increasing.  A point past the knee can look
  perfectly clean on its own gates; only the sequence gives it away.

On the TX26/3C90 at ~110 mT AC the knee sits at |I₀| ≈ 0.35–0.4 A.  Note that
measured I₀ comes out negative for a positively requested bias (a pulse
polarity convention after the current-clamp flip) — magnitude is what matters,
so every comparison here is on |I₀|.
"""

from __future__ import annotations

import numpy as np

from .. import analysis as an
from .. import events as ev
from ..drive import plan_point, voltage_to_flux
from ..results import ProcedureResult
from . import Context, measure_point, procedure_started, register


@register("inductance_vs_bias")
def inductance_vs_bias(ctx: Context) -> ProcedureResult:
    p = ctx.params
    i_max = float(p.get("i_max_A", 1.0))
    n_steps = int(p.get("steps", 8))
    knee_stop_pct = float(p.get("knee_stop_pct", 30.0))
    frequency = float(p.get("frequency_Hz", 50e3))
    B_ac = float(p.get("B_ac_T", 0.05))
    n_pulses = int(p.get("n_pulses", 8))

    result = ProcedureResult("inductance_vs_bias", p.get("label"))

    biases = list(np.linspace(0.0, i_max, n_steps + 1))
    procedure_started(ctx, "inductance_vs_bias", n_points=len(biases),
                      total=len(biases))

    L0 = None
    i_pp_ref = None
    pv_prev = None
    curve = []

    for i_dc in biases:
        plan = plan_point(ctx.dut, ctx.limits, frequency=frequency,
                          B_target_T=B_ac, dc_bias_A=i_dc, n_pulses=n_pulses)
        if not plan.feasible:
            ctx.bus.emit(ev.POINT_SKIPPED, tag=plan.tag, reason=plan.reason,
                         procedure="inductance_vs_bias")
            result.skipped.append({"tag": plan.tag, "reason": plan.reason})
            continue

        point = measure_point(ctx, plan, procedure="inductance_vs_bias",
                              i_pp_reference=i_pp_ref)
        result.points.append(point)
        if not point.accepted:
            continue

        a = point.analysis
        # L = N1·dΦ/di over the cycle.  With B and H both available the secant
        # permeability already carries the slope, so L follows from geometry
        # without a second regression: L = µ0·µ·N1²·Ae/le.
        L = an.MU0 * a.mu_secant * ctx.dut.N1 ** 2 * ctx.dut.Ae / ctx.dut.le
        point.extra["L_H"] = L
        point.extra["I_dc_measured_A"] = a.I_dc_A

        if L0 is None:
            L0, i_pp_ref = L, a.i_pp
            ctx.state.setdefault("i_pp_unbiased", i_pp_ref)
        drop_pct = (1.0 - L / L0) * 100.0 if L0 else 0.0
        point.extra["L_drop_pct"] = drop_pct
        curve.append({"I_dc_requested_A": i_dc,
                      "I_dc_measured_A": abs(a.I_dc_A),
                      "H_dc_A_m": abs(a.H_dc_A_m),
                      "L_H": L, "L_drop_pct": drop_pct,
                      "Pv_kW_m3": a.Pv_kW_m3, "i_pp_A": a.i_pp})

        ctx.bus.log(f"  L({abs(a.I_dc_A)*1e3:.0f} mA) = {L*1e6:.1f} uH "
                    f"({drop_pct:+.1f}% vs unbiased)")

        if pv_prev is not None and a.Pv_kW_m3 < pv_prev:
            ctx.bus.warn(f"loss went non-monotonic at {abs(a.I_dc_A)*1e3:.0f} mA "
                         f"({pv_prev:.0f} -> {a.Pv_kW_m3:.0f} kW/m3) — past the "
                         f"saturation knee, stopping the ramp")
            point.extra["past_knee"] = True
            break
        pv_prev = a.Pv_kW_m3

        if drop_pct >= knee_stop_pct:
            ctx.bus.log(f"  knee reached: L down {drop_pct:.0f}% "
                        f"(stop at {knee_stop_pct:.0f}%)")
            break

    result.derived["L_vs_bias"] = curve
    result.derived.update(_saturation_currents(curve))
    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="inductance_vs_bias",
                 accepted=len(result.accepted), rejected=len(result.rejected),
                 skipped=len(result.skipped))
    return result


def _saturation_currents(curve, drops=(10.0, 30.0)):
    """I_sat at each −x % inductance drop, by linear interpolation.

    Reported only when the ramp actually crossed the drop — extrapolating a
    saturation current from a curve that never got there is exactly the kind of
    confident wrong number the QC policy exists to prevent.
    """
    out = {}
    if len(curve) < 2:
        return out
    xs = [c["I_dc_measured_A"] for c in curve]
    ys = [c["L_drop_pct"] for c in curve]
    for d in drops:
        key = f"I_sat_minus{int(d)}pct_A"
        hit = None
        for k in range(1, len(ys)):
            if ys[k - 1] < d <= ys[k]:
                span = ys[k] - ys[k - 1]
                frac = (d - ys[k - 1]) / span if span else 0.0
                hit = xs[k - 1] + frac * (xs[k] - xs[k - 1])
                break
        out[key] = hit
        if hit is None:
            out[key + "_note"] = (f"not reached — ramp stopped at "
                                  f"{max(ys):.0f}% drop")
    return out


@register("saturation_curve")
def saturation_curve(ctx: Context) -> ProcedureResult:
    """Deep-drive B(H) envelope with a guarded amplitude ramp."""
    p = ctx.params
    frequency = float(p.get("frequency_Hz", 25e3))
    n_steps = int(p.get("steps", 10))
    n_pulses = int(p.get("n_pulses", 8))
    mu_drop_pct = float(p.get("mu_drop_pct", 50.0))

    result = ProcedureResult("saturation_curve", p.get("label"))

    # Ramp the rail from a small fraction of the ceiling up to it; every step
    # is a real, gated measurement, so the envelope is measured, not modelled.
    v_max = float(p.get("v_max_V", ctx.limits.psu_max_V))
    v_min = float(p.get("v_min_V", max(1.0, v_max * 0.1)))
    rails = list(np.linspace(v_min, v_max, n_steps))

    procedure_started(ctx, "saturation_curve", n_points=len(rails),
                      total=len(rails))

    mu0_ref = None
    i_pp_ref = None
    envelope = []

    for v in rails:
        plan = plan_point(ctx.dut, ctx.limits, frequency=frequency,
                          voltage_V=v, n_pulses=n_pulses)
        plan.B_target_T = voltage_to_flux(v, ctx.dut.N1, ctx.dut.Ae, frequency,
                                          ctx.limits.deadtime_s)
        if not plan.feasible:
            ctx.bus.emit(ev.POINT_SKIPPED, tag=plan.tag, reason=plan.reason,
                         procedure="saturation_curve")
            result.skipped.append({"tag": plan.tag, "reason": plan.reason})
            continue

        point = measure_point(ctx, plan, procedure="saturation_curve",
                              i_pp_reference=i_pp_ref)
        result.points.append(point)
        if not point.accepted:
            continue

        a = point.analysis
        if mu0_ref is None:
            mu0_ref, i_pp_ref = a.mu_secant, a.i_pp
        envelope.append({"V_rail": v, "B_peak_T": a.B_peak_T,
                         "H_peak_A_m": a.H_peak_A_m, "mu_secant": a.mu_secant,
                         "remanence_T": a.remanence_T,
                         "coercivity_A_m": a.coercivity_A_m})

        drop = (1.0 - a.mu_secant / mu0_ref) * 100.0 if mu0_ref else 0.0
        point.extra["mu_drop_pct"] = drop
        if drop >= mu_drop_pct:
            ctx.bus.log(f"  saturation knee: permeability down {drop:.0f}% at "
                        f"B={a.B_peak_T*1e3:.0f} mT, H={a.H_peak_A_m:.0f} A/m")
            break

    result.derived["bh_envelope"] = envelope
    if envelope:
        top = max(envelope, key=lambda e: e["B_peak_T"])
        result.derived["B_max_measured_T"] = top["B_peak_T"]
        result.derived["H_at_B_max_A_m"] = top["H_peak_A_m"]
        result.derived["note"] = (
            "B_max is the highest flux this bench reached before the guard "
            "stopped the ramp; it is a lower bound on B_sat, not B_sat itself, "
            "unless the permeability drop gate actually fired")

    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="saturation_curve",
                 accepted=len(result.accepted), rejected=len(result.rejected),
                 skipped=len(result.skipped))
    return result
