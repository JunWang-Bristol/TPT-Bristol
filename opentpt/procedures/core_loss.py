"""``core_loss_map`` — Pv over the (f, B̂, bias, duty) grid.

The workhorse procedure: the B–H loop area of an edge-bounded target cycle,
validated on this rig at 1.25–1.27× the MagNet neural model over 25–100 kHz and
60–220 mT with ~10 % spread.  A flat ratio across 4× frequency and 3.6× flux is
one systematic scale factor — current-probe calibration or assumed geometry —
not a method error, so the map is trustworthy for *shape* today and for
absolutes once ``current_probe_scale`` is calibrated against a known resistor.

Points are ordered low-stress first and every infeasible combination is refused
at planning time with its reason, never clamped to a voltage the recipe did not
ask for.
"""

from __future__ import annotations

import itertools

from .. import events as ev
from ..drive import (DEFAULT_BIAS_CYCLES, DEFAULT_BIAS_LEAD,
                     DEFAULT_BIAS_METHOD, plan_point)
from ..results import ProcedureResult
from . import Context, measure_point, procedure_started, register


@register("core_loss_map")
def core_loss_map(ctx: Context) -> ProcedureResult:
    p = ctx.params
    freqs = [float(f) for f in p.get("frequency_Hz", [])]
    fluxes = [float(b) for b in p.get("B_peak_T", [])]
    biases = [float(b) for b in p.get("dc_bias_A", [0.0])]
    duties = [float(d) for d in p.get("duty", [0.5])]
    n_pulses = int(p.get("n_pulses", 8))
    # How the DC operating point is reached. Defaults to the classical
    # single-opening-pulse method; `distributed` is opt-in per procedure block
    # and is only advisable with n_pulses >= 16 (see drive.BIAS_METHODS).
    bias_opts = {
        "bias_method": p.get("bias_method", DEFAULT_BIAS_METHOD),
        "bias_cycles": int(p.get("bias_cycles", DEFAULT_BIAS_CYCLES)),
        "bias_lead": float(p.get("bias_lead", DEFAULT_BIAS_LEAD)),
    }

    if not freqs or not fluxes:
        raise ValueError("core_loss_map needs frequency_Hz and B_peak_T lists")

    result = ProcedureResult("core_loss_map", ctx.params.get("label"))

    # Low-stress first: lowest flux, then lowest bias, then lowest frequency.
    grid = sorted(itertools.product(freqs, fluxes, biases, duties),
                  key=lambda x: (x[1], abs(x[2]), x[0]))

    plans = [plan_point(ctx.dut, ctx.limits, frequency=f, B_target_T=b,
                        dc_bias_A=i, duty=d, n_pulses=n_pulses, **bias_opts)
             for f, b, i, d in grid]

    feasible = [pl for pl in plans if pl.feasible]
    procedure_started(ctx, "core_loss_map", n_points=len(feasible),
                      n_skipped=len(plans) - len(feasible), total=len(plans))

    # The unbiased ripple current is the reference the saturation gate needs;
    # it only exists once the first zero-bias point of this map has passed.
    i_pp_ref = None

    for plan in plans:
        if not plan.feasible:
            ctx.bus.emit(ev.POINT_SKIPPED, tag=plan.tag, reason=plan.reason,
                         procedure="core_loss_map")
            result.skipped.append({"tag": plan.tag, "reason": plan.reason,
                                   **plan.to_dict()})
            continue

        point = measure_point(ctx, plan, procedure="core_loss_map",
                              i_pp_reference=i_pp_ref if plan.dc_bias_A else None)
        result.points.append(point)

        if point.accepted and not plan.dc_bias_A and i_pp_ref is None:
            i_pp_ref = point.analysis.i_pp
            ctx.state["i_pp_unbiased"] = i_pp_ref

    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="core_loss_map",
                 accepted=len(result.accepted), rejected=len(result.rejected),
                 skipped=len(result.skipped))
    return result
