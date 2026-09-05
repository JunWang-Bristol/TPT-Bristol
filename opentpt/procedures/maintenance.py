"""``demagnetize`` — reset the core, and prove it was reset.

A demagnetisation that silently failed is worse than none: every µ_i and every
low-amplitude point taken afterwards is quietly wrong, and nothing downstream
can tell.  So this procedure does not just fire the decaying train — it takes a
small probe burst afterwards and checks the residual.

``verify_reset`` is on by default.  If the residual is still above threshold the
run continues but the failure is emitted as a warning and recorded in the
derived block, so any point that depends on a demagnetised core can be
identified after the fact.
"""

from __future__ import annotations

from .. import analysis as an
from .. import events as ev
from ..drive import plan_point
from ..results import ProcedureResult
from . import Context, procedure_started, register


@register("demagnetize")
def demagnetize(ctx: Context) -> ProcedureResult:
    p = ctx.params
    voltage = float(p.get("voltage_V", 5.0))
    frequency = float(p.get("frequency_Hz", 5e3))
    steps = int(p.get("steps", 8))
    verify = bool(p.get("verify_reset", True))
    # Residual remanence accepted as "reset", as a fraction of the probe burst's
    # own flux amplitude.  Absolute thresholds do not transfer between cores.
    max_residual_frac = float(p.get("max_residual_fraction", 0.10))
    probe_B_T = float(p.get("probe_B_T", 0.010))
    probe_f = float(p.get("probe_frequency_Hz", 25e3))

    result = ProcedureResult("demagnetize", p.get("label"))
    procedure_started(ctx, "demagnetize", n_points=1 if verify else 0,
                      total=1)

    ctx.bench.demagnetize(voltage=voltage, frequency=frequency, steps=steps,
                          i_limit=ctx.limits.i_limit_A)
    result.derived["train"] = {"voltage_V": voltage, "frequency_Hz": frequency,
                               "steps": steps}

    if not verify:
        result.derived["verified"] = None
        ctx.bus.emit(ev.PROCEDURE_FINISHED, type="demagnetize", accepted=0,
                     rejected=0, skipped=0)
        return result

    plan = plan_point(ctx.dut, ctx.limits, frequency=probe_f,
                      B_target_T=probe_B_T, n_pulses=8)
    verified, detail = None, "probe burst not feasible"
    if plan.feasible:
        ctx.bench.set_rails(plan.voltage_V, ctx.limits.i_limit_A)
        cap = ctx.bench.acquire(plan.pulses, frequency=probe_f,
                                i_expected_A=plan.i_expected_A,
                                expected_vpri_pp=plan.expected_vpri_pp)
        if cap is None:
            detail = "probe capture did not complete — reset unverified"
        else:
            a = an.analyse_cycle(cap.t, cap.v_pri, cap.v_sec, cap.current,
                                 frequency=probe_f, N1=ctx.dut.N1, N2=ctx.dut.N2,
                                 Ae=ctx.dut.Ae, le=ctx.dut.le, Ve=ctx.dut.Ve)
            if a is None:
                detail = "no closed cycle in the probe burst — reset unverified"
            else:
                frac = (a.remanence_T / a.B_peak_T) if a.B_peak_T else float("nan")
                verified = bool(frac <= max_residual_frac)
                detail = (f"residual B_r {a.remanence_T*1e3:.2f} mT = "
                          f"{frac*100:.0f}% of the {a.B_peak_T*1e3:.1f} mT probe "
                          f"amplitude (limit {max_residual_frac*100:.0f}%)")
                result.derived["probe"] = {
                    "B_peak_T": a.B_peak_T, "remanence_T": a.remanence_T,
                    "residual_fraction": frac, "I_dc_A": a.I_dc_A,
                }

    result.derived["verified"] = verified
    result.derived["verification_detail"] = detail
    ctx.bus.emit(ev.BENCH_CHECK, check="demagnetize", ok=bool(verified),
                 detail=detail)
    if verified is not True:
        ctx.bus.warn(f"demagnetisation not verified: {detail} — any mu_i or "
                     f"low-amplitude point after this is suspect")

    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="demagnetize",
                 accepted=1 if verified else 0, rejected=0, skipped=0)
    return result
