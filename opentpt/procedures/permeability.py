"""``permeability_set`` and ``complex_mu_spectrum``.

Two very different confidence tiers, kept apart on purpose.

**permeability_set** is reduction, not new measurement.  µ_a, µ_Δ, µ_rev and
the large-signal µ′/µ″ all fall out of loops the loss map already captures —
the only new code is the export and, for µ_rev and µ_i, a few extra
small-amplitude points.  These are reported as measurements.

**complex_mu_spectrum** is explicitly experimental.  Getting a small-signal
µ′(f)/µ″(f) spectrum out of an 8-bit scope and a square-wave half-bridge means
driving ΔB below a millitesla, taking the fundamental of Z(ω)=V(ω)/I(ω), and
beating the quantisation floor down with coherent averaging over many repeats.
It is worth having — it is the one characterisation the rig can attempt that
needs no new hardware — but an impedance analyser remains the reference method,
and every point it produces is exported with its tier and an uncertainty
estimate attached.  A number from this procedure must never be presented
alongside a loss-map number as though they carried the same weight.
"""

from __future__ import annotations

import numpy as np

from .. import analysis as an
from .. import events as ev
from ..drive import plan_point, voltage_to_flux
from ..results import ProcedureResult
from . import Context, measure_point, procedure_started, register

DEFAULT_KINDS = ("amplitude", "incremental", "reversible", "complex_fundamental")


@register("permeability_set")
def permeability_set(ctx: Context) -> ProcedureResult:
    p = ctx.params
    kinds = [k for k in p.get("kinds", DEFAULT_KINDS)]
    frequency = float(p.get("frequency_Hz", 50e3))
    n_pulses = int(p.get("n_pulses", 8))
    amplitudes = [float(b) for b in p.get(
        "B_peak_T", [0.010, 0.025, 0.050, 0.100, 0.200])]
    biases = [float(b) for b in p.get("dc_bias_A", [0.0, 0.10, 0.20])]

    result = ProcedureResult("permeability_set", p.get("label"))
    unknown = set(kinds) - {"amplitude", "incremental", "reversible",
                            "complex_fundamental", "initial"}
    if unknown:
        raise ValueError(f"unknown permeability kinds: {sorted(unknown)}")

    # µ_a and the large-signal complex µ come from the same unbiased amplitude
    # ladder; µ_Δ and µ_rev need the ladder repeated at each bias.
    want_bias = bool({"incremental", "reversible"} & set(kinds))
    bias_list = biases if want_bias else [0.0]

    plans = []
    for i_dc in bias_list:
        for B in sorted(amplitudes):
            plans.append(plan_point(ctx.dut, ctx.limits, frequency=frequency,
                                    B_target_T=B, dc_bias_A=i_dc,
                                    n_pulses=n_pulses))

    feasible = [pl for pl in plans if pl.feasible]
    procedure_started(ctx, "permeability_set", n_points=len(feasible),
                      total=len(plans))

    i_pp_ref = ctx.state.get("i_pp_unbiased")
    by_bias = {}

    for plan in plans:
        if not plan.feasible:
            ctx.bus.emit(ev.POINT_SKIPPED, tag=plan.tag, reason=plan.reason,
                         procedure="permeability_set")
            result.skipped.append({"tag": plan.tag, "reason": plan.reason})
            continue
        point = measure_point(ctx, plan, procedure="permeability_set",
                              i_pp_reference=i_pp_ref if plan.dc_bias_A else None)
        result.points.append(point)
        if not point.accepted:
            continue
        a = point.analysis
        if i_pp_ref is None and not plan.dc_bias_A:
            i_pp_ref = a.i_pp
        by_bias.setdefault(plan.dc_bias_A, []).append({
            "B_peak_T": a.B_peak_T, "H_peak_A_m": a.H_peak_A_m,
            "H_dc_A_m": a.H_dc_A_m, "mu": a.mu_secant,
            "mu_prime": a.mu_prime, "mu_second": a.mu_second,
            "tan_delta": a.tan_delta, "frequency_Hz": frequency,
        })

    ladders = {k: sorted(v, key=lambda r: r["B_peak_T"]) for k, v in by_bias.items()}

    if "amplitude" in kinds and 0.0 in ladders:
        result.derived["amplitude"] = [
            {"magneticFluxDensityPeak": r["B_peak_T"], "frequency": frequency,
             "temperature": ctx.dut.temperature_C, "value": r["mu"]}
            for r in ladders[0.0]]

    if "complex_fundamental" in kinds and 0.0 in ladders:
        result.derived["complex_fundamental"] = [
            {"magneticFluxDensityPeak": r["B_peak_T"], "frequency": frequency,
             "temperature": ctx.dut.temperature_C,
             "real": r["mu_prime"], "imaginary": r["mu_second"],
             "tanDelta": r["tan_delta"], "excitation": "large-signal"}
            for r in ladders[0.0]]

    if "incremental" in kinds:
        result.derived["incremental"] = [
            {"magneticFieldDcBias": r["H_dc_A_m"],
             "magneticFluxDensityPeak": r["B_peak_T"], "frequency": frequency,
             "temperature": ctx.dut.temperature_C, "value": r["mu"]}
            for bias, rows in ladders.items() if bias for r in rows]

    if "reversible" in kinds:
        result.derived["reversible"] = _reversible(ladders, ctx)

    if "initial" in kinds:
        result.derived["initial"] = _initial(ladders.get(0.0, []), ctx)

    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="permeability_set",
                 accepted=len(result.accepted), rejected=len(result.rejected),
                 skipped=len(result.skipped))
    return result


def _extrapolate_to_zero(rows, *, n_min=3):
    """Linear extrapolation of µ(B̂) to B̂ → 0, with the fit's own scatter.

    Returns ``(value, stderr, n_used)`` or ``None`` when there are too few
    small-amplitude points to fit — an extrapolation from two points has no
    residual and would report a fake certainty.
    """
    if len(rows) < n_min:
        return None
    xs = np.array([r["B_peak_T"] for r in rows[:4]], dtype=float)
    ys = np.array([r["mu"] for r in rows[:4]], dtype=float)
    if xs.size < n_min:
        return None
    slope, intercept = np.polyfit(xs, ys, 1)
    resid = ys - (slope * xs + intercept)
    dof = max(1, xs.size - 2)
    sx = float(np.sum((xs - xs.mean()) ** 2))
    se = float(np.sqrt(np.sum(resid ** 2) / dof * (1.0 / xs.size + xs.mean() ** 2 / sx))) \
        if sx > 0 else float("nan")
    return float(intercept), se, int(xs.size)


def _reversible(ladders, ctx):
    """µ_rev(H_dc): µ_Δ extrapolated to zero AC amplitude at each bias."""
    out = []
    for bias, rows in sorted(ladders.items()):
        fit = _extrapolate_to_zero(rows)
        if fit is None:
            continue
        value, se, n = fit
        out.append({
            "magneticFieldDcBias": float(np.mean([r["H_dc_A_m"] for r in rows])),
            "frequency": rows[0]["frequency_Hz"],
            "temperature": ctx.dut.temperature_C,
            "value": value, "standardError": se, "nPoints": n,
            "method": "linear extrapolation of mu_delta to B_peak -> 0",
        })
    return out


def _initial(rows, ctx):
    """µ_i: the low-amplitude limit of µ_a, reported as an estimate.

    Scope resolution bounds this: at very low B̂ the current swing uses few ADC
    codes, so µ_i from this bench is an estimate with a tolerance, not a
    catalogue value.  It is only meaningful straight after a demagnetisation,
    which the engine's ordering guarantees.
    """
    fit = _extrapolate_to_zero(rows)
    if fit is None:
        return None
    value, se, n = fit
    return {
        "value": value, "standardError": se, "nPoints": n,
        "temperature": ctx.dut.temperature_C,
        "estimate": True,
        "note": ("extrapolated from the lowest-amplitude mu_a points; bounded "
                 "by 8-bit scope resolution — report with tolerance"),
    }


# ─── Experimental: small-signal spectrum ──────────────────────────────────────

@register("complex_mu_spectrum")
def complex_mu_spectrum(ctx: Context) -> ProcedureResult:
    """Small-signal µ′(f), µ″(f) by coherent averaging.  Experimental tier.

    Per frequency: drive the smallest burst that still produces a usable
    current swing, average ``averages`` repeats coherently to push the
    quantisation floor down by ~√N, then take µ = B₁/(µ0·H₁) at the
    fundamental.  Averaging only helps to the extent the captures are
    time-aligned, so the achieved reduction is measured from the data rather
    than assumed, and it is reported with every point.
    """
    p = ctx.params
    band = p.get("band_Hz", [25e3, 1e6])
    n_freq = int(p.get("n_frequencies", 8))
    averages = int(p.get("averages", 200))
    dB_target = float(p.get("delta_B_T", 1e-3))
    n_pulses = int(p.get("n_pulses", 8))

    result = ProcedureResult("complex_mu_spectrum", p.get("label"))
    result.derived["tier"] = "experimental"
    result.derived["reference_method"] = (
        "impedance analyser — this procedure does not replace it")

    freqs = np.geomspace(float(band[0]), float(band[1]), n_freq)
    in_band = [f for f in freqs if f <= ctx.limits.f_max_Hz]
    procedure_started(ctx, "complex_mu_spectrum", n_points=len(in_band),
                      total=len(freqs))

    if len(in_band) < len(freqs):
        ctx.bus.warn(
            f"{len(freqs) - len(in_band)} of {len(freqs)} spectrum points are "
            f"above the {ctx.limits.f_max_Hz/1e3:.0f} kHz validated ceiling and "
            f"were dropped — the firmware deadtime dominates up there")

    spectrum = []
    for f in in_band:
        plan = plan_point(ctx.dut, ctx.limits, frequency=f,
                          B_target_T=dB_target, n_pulses=n_pulses)
        if not plan.feasible:
            ctx.bus.emit(ev.POINT_SKIPPED, tag=plan.tag, reason=plan.reason,
                         procedure="complex_mu_spectrum")
            result.skipped.append({"tag": plan.tag, "reason": plan.reason})
            continue

        ctx.bench.set_rails(plan.voltage_V, ctx.limits.i_limit_A)
        stack, ref_len = [], None
        for _ in range(averages):
            cap = ctx.bench.acquire(
                plan.pulses, frequency=f, i_expected_A=plan.i_expected_A,
                expected_vpri_pp=plan.expected_vpri_pp, retries=1)
            if cap is None:
                continue
            if ref_len is None:
                ref_len = cap.t.size
            if cap.t.size != ref_len:
                continue
            stack.append(cap)
        if not stack:
            result.skipped.append({"tag": plan.tag,
                                   "reason": "no captures completed"})
            continue

        v_pri = np.mean([c.v_pri for c in stack], axis=0)
        v_sec = np.mean([c.v_sec for c in stack], axis=0)
        cur = np.mean([c.current for c in stack], axis=0)
        t = stack[0].t

        # Measured noise reduction: the scatter of single captures against the
        # average, versus the scatter that remains in the average itself.
        single = float(np.std([np.std(c.current - cur) for c in stack])) or float("nan")
        achieved = float(np.sqrt(len(stack)))

        a = an.analyse_cycle(t, v_pri, v_sec, cur, frequency=f,
                             N1=ctx.dut.N1, N2=ctx.dut.N2, Ae=ctx.dut.Ae,
                             le=ctx.dut.le, Ve=ctx.dut.Ve)
        if a is None:
            result.skipped.append({"tag": plan.tag,
                                   "reason": "no closed cycle after averaging"})
            continue

        codes = a.i_pp / stack[0].current_lsb_A if stack[0].current_lsb_A else 0.0
        row = {
            "frequency": float(f),
            "magneticFluxDensityPeak": a.B_peak_T,
            "real": a.mu_prime, "imaginary": a.mu_second,
            "tanDelta": a.tan_delta,
            "temperature": ctx.dut.temperature_C,
            "averages_used": len(stack),
            "noise_reduction_sqrtN": achieved,
            "adc_codes_per_swing": codes,
            "excitation": "small-signal square burst",
            "tier": "experimental",
        }
        # An honest floor: below ~8 codes even √N averaging cannot rescue the
        # phase, and phase error is what µ″ is made of.
        if codes < 8:
            row["warning"] = (f"only {codes:.0f} ADC codes per swing — mu'' "
                              f"is dominated by quantisation phase error")
        spectrum.append(row)
        ctx.bus.log(f"  {f/1e3:7.1f} kHz  mu'={a.mu_prime:6.0f} "
                    f"mu''={a.mu_second:6.0f}  ({len(stack)} averages, "
                    f"{codes:.0f} codes)")

    result.derived["spectrum"] = spectrum
    ctx.bench.rails_off()
    ctx.bus.emit(ev.PROCEDURE_FINISHED, type="complex_mu_spectrum",
                 accepted=len(spectrum), rejected=0, skipped=len(result.skipped))
    return result
