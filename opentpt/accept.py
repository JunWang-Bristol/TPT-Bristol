"""Bench acceptance: run the same recipe twice and compare the two datasets.

This is the check the build plan asks for — *the full map, hands-off, twice,
with matching output* — and it is the only test that can tell you whether the
rig is actually repeatable, as opposed to whether the software is
deterministic. The software half is already proven by the replay tests; this
exercises the part that has drift, thermal creep, trigger jitter and a
current probe in it.

What it compares, and why those things:

* **Which points were accepted.** A point that passes in one run and fails in
  the other is the most important signal here — it means the rig is sitting on
  a QC threshold, and any single run is a coin toss. That shows up as
  ``only_in_a``/``only_in_b`` and fails acceptance even if every shared point
  agrees perfectly.
* **The measured values, per point.** B, Pv and µ, each against its own
  tolerance. Pv is allowed more spread than B because it carries the current
  channel and B does not.

Deliberately *not* compared: anything derived from the recipe rather than the
bench (planned voltage, tags, ordering). Those are identical by construction
and would only pad the pass rate.

A run pair that agrees does not make the numbers correct — the current probe
is still uncalibrated, and a systematic scale error reproduces perfectly.
Acceptance measures repeatability, not accuracy, and the report says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# Field, human label, and the fractional deviation tolerated between runs.
COMPARED = [
    ("B_peak_T", "B", 0.02),
    ("Pv_kW_m3", "Pv", 0.05),
    ("mu_secant", "mu_a", 0.05),
]


@dataclass
class PointComparison:
    tag: str
    procedure: str
    status: str                       # match | deviates | only_in_a | only_in_b
    values_a: Dict[str, float] = field(default_factory=dict)
    values_b: Dict[str, float] = field(default_factory=dict)
    deviations: Dict[str, float] = field(default_factory=dict)
    exceeded: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class AcceptanceReport:
    ok: bool
    points: List[PointComparison] = field(default_factory=list)
    tolerances: Dict[str, float] = field(default_factory=dict)
    run_dirs: List[str] = field(default_factory=list)
    note: str = ""

    def counts(self):
        c = {"match": 0, "deviates": 0, "only_in_a": 0, "only_in_b": 0}
        for p in self.points:
            c[p.status] = c.get(p.status, 0) + 1
        return c

    def worst(self, key):
        vals = [p.deviations.get(key) for p in self.points
                if p.deviations.get(key) is not None]
        return max(vals) if vals else None

    def summary(self) -> str:
        c = self.counts()
        head = "ACCEPTED" if self.ok else "NOT ACCEPTED"
        bits = [f"{c['match']} matching"]
        if c["deviates"]:
            bits.append(f"{c['deviates']} deviating")
        if c["only_in_a"] or c["only_in_b"]:
            bits.append(f"{c['only_in_a'] + c['only_in_b']} present in only one run")
        return f"{head}: " + ", ".join(bits)

    def to_dict(self):
        return {"ok": self.ok, "summary": self.summary(),
                "counts": self.counts(), "tolerances": self.tolerances,
                "runDirs": self.run_dirs, "note": self.note,
                "worstDeviation": {k: self.worst(k) for k, _, _ in COMPARED},
                "points": [p.to_dict() for p in self.points]}


def _accepted_index(result) -> Dict[str, Any]:
    return {f"{p.procedure}|{p.tag}": p for p in result.accepted_points}


def compare(result_a, result_b, *, tolerances=None) -> AcceptanceReport:
    """Compare two completed runs of the same recipe."""
    tol = dict(tolerances or {k: t for k, _, t in COMPARED})
    a_idx, b_idx = _accepted_index(result_a), _accepted_index(result_b)

    report = AcceptanceReport(ok=True, tolerances=tol)
    report.note = ("Repeatability only. A systematic scale error - notably the "
                   "uncalibrated current_probe_scale - reproduces perfectly "
                   "and will pass this check.")

    for key in sorted(set(a_idx) | set(b_idx)):
        procedure, _, tag = key.partition("|")
        pa, pb = a_idx.get(key), b_idx.get(key)

        if pa is None or pb is None:
            report.points.append(PointComparison(
                tag=tag, procedure=procedure,
                status="only_in_b" if pa is None else "only_in_a"))
            report.ok = False
            continue

        cmp = PointComparison(tag=tag, procedure=procedure, status="match")
        for fieldname, label, _default in COMPARED:
            va = getattr(pa.analysis, fieldname, None)
            vb = getattr(pb.analysis, fieldname, None)
            if va is None or vb is None:
                continue
            cmp.values_a[label] = float(va)
            cmp.values_b[label] = float(vb)
            scale = max(abs(va), abs(vb))
            dev = abs(va - vb) / scale if scale else 0.0
            cmp.deviations[label] = dev
            if dev > tol.get(fieldname, tol.get(label, 0.05)):
                cmp.exceeded.append(label)
        if cmp.exceeded:
            cmp.status = "deviates"
            report.ok = False
        report.points.append(cmp)

    return report


def run_acceptance(recipe_path, out_dir, *, bench_factory=None, verbose=True,
                   tolerances=None):
    """Run a recipe twice into ``<out>/run1`` and ``<out>/run2`` and compare.

    ``bench_factory`` builds a fresh bench per run; the default is real
    hardware. Each run gets its own output directory so neither can resume
    from — or otherwise observe — the other.
    """
    from .engine import run_recipe

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    results = []
    for k in (1, 2):
        if verbose:
            print(f"\n{'=' * 60}\n  ACCEPTANCE RUN {k} of 2\n{'=' * 60}")
        bench = bench_factory() if bench_factory else None
        results.append(run_recipe(recipe_path, bench=bench,
                                  output_dir=out / f"run{k}", verbose=verbose))

    report = compare(*results, tolerances=tolerances)
    report.run_dirs = [str(out / "run1"), str(out / "run2")]

    (out / "acceptance.json").write_text(
        json.dumps(report.to_dict(), indent=2, default=float), encoding="utf-8")
    return report, results


def print_report(report: AcceptanceReport):
    print(f"\n{'=' * 72}")
    print(f"  {report.summary()}")
    print(f"{'=' * 72}")
    print(f"  {'point':28s} {'B a/b (mT)':>20s} {'Pv a/b (kW/m3)':>22s}  dev")
    print("  " + "-" * 70)
    for p in report.points:
        if p.status in ("only_in_a", "only_in_b"):
            print(f"  {p.tag:28s} {'present in ' + p.status[-1].upper() + ' only':>44s}")
            continue
        ba, bb = p.values_a.get("B", 0) * 1e3, p.values_b.get("B", 0) * 1e3
        pa, pb = p.values_a.get("Pv", 0), p.values_b.get("Pv", 0)
        worst = max(p.deviations.values()) if p.deviations else 0.0
        flag = "  <-- " + ",".join(p.exceeded) if p.exceeded else ""
        print(f"  {p.tag:28s} {ba:9.1f}/{bb:9.1f} {pa:10.1f}/{pb:10.1f}  "
              f"{worst * 100:5.2f}%{flag}")
    for key, label, _ in COMPARED:
        w = report.worst(label)
        if w is not None:
            print(f"  worst {label:6s} deviation: {w * 100:.2f}% "
                  f"(tolerance {report.tolerances.get(key, 0) * 100:.0f}%)")
    print(f"\n  {report.note}")
