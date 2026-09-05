"""What a run accumulates: measured points, rejects, skips, and the dataset.

A point carries its excitation conditions, the full cycle reduction, and the
QC verdict together.  Keeping the three in one record is what makes the export
honest — a number can never appear in the dataset without the conditions it was
taken at and the gates it passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MeasuredPoint:
    """One accepted (or rejected) measurement."""

    procedure: str
    tag: str
    plan: Dict[str, Any]
    analysis: Optional[Any] = None           # CycleAnalysis
    verdict: Optional[Any] = None            # qc.Verdict
    attempts: int = 1
    accepted: bool = False
    waveform_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, with_loop=False):
        d = {
            "procedure": self.procedure,
            "tag": self.tag,
            "accepted": self.accepted,
            "attempts": self.attempts,
            **{f"plan_{k}": v for k, v in self.plan.items()},
        }
        if self.analysis is not None:
            d.update(self.analysis.to_dict(with_loop=with_loop))
        if self.verdict is not None:
            d["qc"] = self.verdict.to_dict()
        if self.waveform_path:
            d["waveform"] = self.waveform_path
        d.update(self.extra)
        return d

    def summary(self):
        """One line for the console and the event stream."""
        a = self.analysis
        if a is None:
            return "no analysis"
        s = (f"B={a.B_peak_T*1e3:.1f} mT  H={a.H_peak_A_m:.0f} A/m  "
             f"Pv={a.Pv_kW_m3:.1f} kW/m3  mu_a={a.mu_secant:.0f}  "
             f"mu'/mu\"={a.mu_prime:.0f}/{a.mu_second:.0f}  "
             f"imbal={a.imbalance_pct:.2f}%")
        if a.H_dc_A_m:
            s += f"  H_dc={a.H_dc_A_m:.1f} A/m"
        return s


@dataclass
class ProcedureResult:
    type: str
    label: Optional[str]
    points: List[MeasuredPoint] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    derived: Dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self):
        return [p for p in self.points if p.accepted]

    @property
    def rejected(self):
        return [p for p in self.points if not p.accepted]

    def to_dict(self, *, with_loop=False):
        return {
            "type": self.type,
            "label": self.label,
            "n_accepted": len(self.accepted),
            "n_rejected": len(self.rejected),
            "n_skipped": len(self.skipped),
            "points": [p.to_dict(with_loop=with_loop) for p in self.points],
            "skipped": self.skipped,
            "derived": self.derived,
        }


@dataclass
class RunResult:
    recipe: Dict[str, Any]
    procedures: List[ProcedureResult] = field(default_factory=list)
    bench_checks: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    aborted: Optional[str] = None

    @property
    def all_points(self):
        return [p for pr in self.procedures for p in pr.points]

    @property
    def accepted_points(self):
        return [p for p in self.all_points if p.accepted]

    def to_dict(self, *, with_loop=False):
        return {
            "recipe": self.recipe,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.finished_at - self.started_at,
            "aborted": self.aborted,
            "bench_checks": self.bench_checks,
            "n_accepted": len(self.accepted_points),
            "procedures": [p.to_dict(with_loop=with_loop) for p in self.procedures],
        }
