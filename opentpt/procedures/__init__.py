"""Procedures — one reusable block of a run each.

A procedure receives a :class:`Context` and returns a
:class:`~opentpt.results.ProcedureResult`.  It never prints and never writes
files; everything it wants to say goes through the event bus, and everything it
measures goes into the result.  That is what lets the same code run under the
CLI, under CI, and under the GUI without a second implementation.

All of them share :func:`measure_point`, which is where the retry policy lives:
capture, analyse, gate — and on failure retry, **keeping the best attempt**
rather than the last.  Imbalance rejection on this rig is often a property of a
marginal capture, not of the operating point, so keeping the best of N recovers
points that dropping outright would lose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from .. import analysis as an
from .. import events as ev
from .. import qc as qcmod
from ..results import MeasuredPoint


@dataclass
class Context:
    """Everything a procedure is allowed to touch."""

    dut: Any
    limits: Any
    qc: Any
    bench: Any
    bus: Any
    params: Dict[str, Any] = field(default_factory=dict)
    waveform_dir: Optional[Path] = None
    save_waveforms: bool = True
    index: Optional[int] = None      # position in the run, for progress
    total: Optional[int] = None
    prior: Any = None                # PriorRun, when resuming
    state: Dict[str, Any] = field(default_factory=dict)   # shared across procedures

    def param(self, name, default=None, cast=None):
        v = self.params.get(name, default)
        return cast(v) if (cast and v is not None) else v


def procedure_started(ctx: Context, ptype, *, n_points, n_skipped=0, total=None):
    """Announce a procedure, carrying its position in the run.

    Procedures do not know where they sit in the recipe; the engine puts that
    on the Context so progress can be rendered without the UI re-deriving it.
    """
    ctx.bus.emit(ev.PROCEDURE_STARTED, type=ptype, label=ctx.params.get("label"),
                 n_points=n_points, n_skipped=n_skipped,
                 total=total if total is not None else n_points + n_skipped,
                 index=ctx.index, of=ctx.total)


def _save_waveform(ctx: Context, capture, tag) -> Optional[str]:
    if not (ctx.save_waveforms and ctx.waveform_dir):
        return None
    ctx.waveform_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in tag)
    path = ctx.waveform_dir / f"{safe}.csv"
    capture.to_frame().to_csv(path, index=False)
    return str(path)


def measure_point(ctx: Context, plan, *, procedure, i_pp_reference=None,
                  window=None, extra=None) -> MeasuredPoint:
    """Run one operating point through capture → analyse → gate, with retries.

    Returns a :class:`MeasuredPoint` that is accepted only if some attempt
    passed every gate; otherwise it carries the *best* attempt and the verdict
    that explains the reject, so the gap in the dataset is always attributable.
    """
    point = MeasuredPoint(procedure=procedure, tag=plan.tag, plan=plan.to_dict(),
                          extra=dict(extra or {}))

    if not plan.feasible:
        point.verdict = qcmod.Verdict(
            False, [qcmod.Failure("feasibility", plan.reason)])
        return point

    # Resuming: a point this recipe already measured and accepted is kept as
    # it stands.  Rejects and skips are NOT restored - a reject is often a
    # marginal capture, and re-attempting it costs one point of bench time
    # against the risk of banking a bad one.
    if ctx.prior is not None:
        kept = ctx.prior.get(procedure, plan.tag)
        if kept is not None:
            ctx.bus.emit(ev.POINT_RESTORED, tag=plan.tag, procedure=procedure,
                         summary=kept.summary())
            return kept

    # Asymmetric excitation needs asymmetric rails. With duty ≠ 0.5 the
    # half-periods differ in length, so equal rails leave a net volt-second per
    # cycle — the flux walks and the core saturates in a few cycles, which the
    # QC gates then (correctly) reject. Trimming the negative rail by
    # duty/(1-duty) makes V·T match in both directions and keeps the loop closed.
    duty = getattr(plan, "duty", 0.5) or 0.5
    v_neg = (plan.voltage_V * duty / (1.0 - duty)
             if abs(duty - 0.5) > 1e-9 and 0.0 < duty < 1.0 else None)

    ctx.bus.emit(ev.POINT_STARTED, tag=plan.tag, procedure=procedure,
                 voltage_V=plan.voltage_V, frequency_Hz=plan.frequency_Hz,
                 B_target_T=plan.B_target_T, dc_bias_A=plan.dc_bias_A,
                 duty=duty, v_neg_V=v_neg)

    ctx.bench.set_rails(plan.voltage_V, ctx.limits.i_limit_A, v_neg=v_neg)

    best = None          # (score, analysis, verdict, capture)
    attempts = 0
    for attempt in range(1, ctx.qc.retries + 1):
        attempts = attempt
        capture = ctx.bench.acquire(
            plan.pulses, frequency=plan.frequency_Hz,
            i_expected_A=plan.i_expected_A,
            expected_vpri_pp=plan.expected_vpri_pp,
        )
        if capture is None:
            ctx.bus.emit(ev.CAPTURE, tag=plan.tag, attempt=attempt,
                         summary="scope did not complete")
            continue

        a = an.analyse_cycle(
            capture.t, capture.v_pri, capture.v_sec, capture.current,
            frequency=plan.frequency_Hz, N1=ctx.dut.N1, N2=ctx.dut.N2,
            Ae=ctx.dut.Ae, le=ctx.dut.le, Ve=ctx.dut.Ve, window=window,
        )
        verdict = qcmod.evaluate(
            a, ctx.qc, expected_vpri_pp=plan.expected_vpri_pp,
            clipped=capture.clipped, i_pp_reference=i_pp_reference,
            current_lsb=capture.current_lsb_A,
        )
        ctx.bus.emit(ev.CAPTURE, tag=plan.tag, attempt=attempt,
                     summary=(f"imbal={a.imbalance_pct:.2f}% "
                              f"B={a.B_peak_T*1e3:.1f} mT" if a else "no cycle"),
                     clipped=capture.clipped)
        ctx.bus.emit(ev.VERDICT, tag=plan.tag, attempt=attempt,
                     **verdict.to_dict())

        # Rank attempts by imbalance — the one quality measure that is
        # continuous rather than pass/fail, so "best" is well defined.
        score = a.imbalance_pct if a is not None else float("inf")
        if best is None or score < best[0]:
            best = (score, a, verdict, capture)
        if verdict.passed:
            break

    if best is None:
        point.attempts = attempts
        point.verdict = qcmod.Verdict(
            False, [qcmod.Failure("capture", "no capture completed")])
        ctx.bus.emit(ev.POINT_REJECTED, tag=plan.tag, procedure=procedure,
                     reason=point.verdict.reason)
        return point

    _, a, verdict, capture = best

    # Wiring facts the reduction resolved from the data.  Reported once per
    # run rather than per point: they are properties of the bench, and a
    # warning on every point would be noise nobody reads.
    if a is not None:
        if a.sense_polarity_inverted and not ctx.state.get("_warned_polarity"):
            ctx.state["_warned_polarity"] = True
            ctx.bus.warn(
                "sense winding is wired anti-phase with the primary — the "
                "reduction corrects for it from the sign of the energy "
                "integral, but swapping the V_sec leads would remove the "
                "correction and one more thing to explain")
        expected_ratio = ctx.dut.N2 / ctx.dut.N1
        if (a.turns_ratio_measured and
                abs(a.turns_ratio_measured - expected_ratio) > 0.15 * expected_ratio
                and not ctx.state.get("_warned_ratio")):
            ctx.state["_warned_ratio"] = True
            ctx.bus.warn(
                f"measured |V_sec/V_pri| = {a.turns_ratio_measured:.3f} does "
                f"not match N2/N1 = {expected_ratio:.3f} — check the turns "
                f"count in the recipe before trusting B")

    point.attempts = attempts
    point.analysis = a
    point.verdict = verdict
    point.accepted = verdict.passed
    point.waveform_path = _save_waveform(ctx, capture, f"{procedure}_{plan.tag}")

    if point.accepted:
        ctx.bus.emit(ev.POINT_RESULT, tag=plan.tag, procedure=procedure,
                     summary=point.summary(), **_flat(a))
        _emit_trace(ctx, plan, procedure, capture, a)
    else:
        ctx.bus.emit(ev.POINT_REJECTED, tag=plan.tag, procedure=procedure,
                     reason=verdict.reason)
    return point


# Enough to draw a waveform and a loop, small enough that a 40-point run's
# journal stays a few hundred kB.  The UI never needs full resolution; anything
# that does re-reads the stored CSV.
TRACE_POINTS = 400


def _decimate(arr, n=TRACE_POINTS, digits=6):
    a = np.asarray(arr, dtype=float)
    if a.size > n:
        a = a[np.linspace(0, a.size - 1, n).astype(int)]
    return [round(float(x), digits) for x in a]


def _emit_trace(ctx, plan, procedure, capture, a):
    """Publish the capture and the extracted loop for any UI watching."""
    ctx.bus.emit(
        ev.CAPTURE_TRACE, tag=plan.tag, procedure=procedure,
        t_us=_decimate(capture.t * 1e6, digits=4),
        v_pri=_decimate(capture.v_pri, digits=4),
        v_sec=_decimate(capture.v_sec, digits=4),
        i_a=_decimate(capture.current, digits=5),
        # the shaded window in the UI: which samples were actually integrated
        window_us=[round(float(capture.t[a.i0] * 1e6), 4),
                   round(float(capture.t[min(a.i1, capture.t.size - 1)] * 1e6), 4)],
        loop_h=_decimate(a.H, digits=4),
        loop_b_mt=_decimate(a.B * 1e3, digits=4),
    )


def _flat(a):
    """The numeric fields of an analysis, for the event payload."""
    return {k: v for k, v in a.to_dict().items()
            if isinstance(v, (int, float)) and np.isfinite(v)}


# ─── Registry ─────────────────────────────────────────────────────────────────

_REGISTRY: Dict[str, Callable[[Context], Any]] = {}


def register(name):
    def deco(fn):
        _REGISTRY[name] = fn
        return fn
    return deco


def get(name):
    if name not in _REGISTRY:
        raise KeyError(f"no executor for procedure type {name!r}")
    return _REGISTRY[name]


def known():
    return set(_REGISTRY)


# Importing these registers them.  Keep at the bottom: they import Context.
from . import core_loss      # noqa: E402,F401
from . import inductance     # noqa: E402,F401
from . import permeability   # noqa: E402,F401
from . import maintenance    # noqa: E402,F401
