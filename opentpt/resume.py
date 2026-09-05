"""Resuming an interrupted run from its own journal.

A 40-point sweep that dies at point 35 should not cost 35 points of bench
time. It does not have to: the journal already records every accepted point in
full, so resuming is a matter of reading it back rather than of keeping any
extra state. That is the same property that lets the Studio reopen a finished
run — the journal *is* the run.

Two rules make this safe rather than merely convenient:

* **The recipe must be the same.** A resumed run that silently mixes points
  taken under different thresholds, limits or geometry is not a dataset, it is
  two half-datasets in a trenchcoat. The fingerprint covers everything that
  changes what a point means — DUT (including resolved geometry), procedures,
  QC policy and limits — and a mismatch refuses the resume, naming the section
  that changed. Cosmetics like the export directory are excluded.
* **Only accepted points are restored.** A point that was rejected or skipped
  is attempted again: the reject may well have been a marginal capture, and
  re-running it is cheap compared with getting it wrong.

Restored points are marked as such in the dataset, so a resumed run is never
mistaken for one continuous session.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .analysis import CycleAnalysis
from .qc import Verdict
from .results import MeasuredPoint

JOURNAL = "journal.jsonl"


def recipe_fingerprint(recipe) -> str:
    """Hash of everything that changes what a measured point *means*."""
    d = recipe.to_dict()
    material = {
        "dut": d.get("dut"),
        "procedures": d.get("procedures"),
        "qc": d.get("qc"),
        "limits": d.get("limits"),
    }
    blob = json.dumps(material, sort_keys=True, default=float)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _fingerprint_diff(recipe, other: dict):
    """Which section of the recipe changed, for a useful refusal message."""
    d = recipe.to_dict()
    changed = []
    for key in ("dut", "procedures", "qc", "limits"):
        a = json.dumps(d.get(key), sort_keys=True, default=float)
        b = json.dumps(other.get(key), sort_keys=True, default=float)
        if a != b:
            changed.append(key)
    return changed


class ResumeRefused(RuntimeError):
    """The prior run cannot be continued as this recipe."""


@dataclass
class PriorRun:
    """Accepted points recovered from a previous run's journal."""

    points: Dict[Tuple[str, str], MeasuredPoint] = field(default_factory=dict)
    fingerprint: Optional[str] = None
    recipe: Optional[dict] = None
    path: Optional[str] = None

    def __len__(self):
        return len(self.points)

    def get(self, procedure, tag):
        return self.points.get((procedure, tag))


# Scalar fields of CycleAnalysis, in the order the dataclass declares them.
# Reconstruction is by name, so an added field does not silently shift values.
_SCALARS = [f for f in CycleAnalysis.__dataclass_fields__ if f not in ("t", "B", "H")]


def _analysis_from_event(ev: dict, trace: Optional[dict]) -> Optional[CycleAnalysis]:
    """Rebuild a CycleAnalysis from its point_result event (+ its trace)."""
    missing = [k for k in _SCALARS if k not in ev]
    if missing:
        return None
    kw = {k: ev[k] for k in _SCALARS}
    kw["i0"] = int(kw["i0"])
    kw["i1"] = int(kw["i1"])
    kw["n_samples"] = int(kw["n_samples"])
    kw["sense_polarity_inverted"] = bool(kw["sense_polarity_inverted"])

    t = B = H = None
    if trace:
        # The trace carries the loop in display units; convert back so a
        # restored point exports byte-identically to a freshly measured one.
        H = np.asarray(trace.get("loop_h", []), dtype=float)
        B = np.asarray(trace.get("loop_b_mt", []), dtype=float) / 1e3
        window = trace.get("window_us") or []
        if len(window) == 2 and H.size:
            t = np.linspace(window[0] * 1e-6, window[1] * 1e-6, H.size)
    return CycleAnalysis(t=t, B=B, H=H, **kw)


def load_prior(out_dir, recipe=None, *, strict=True) -> PriorRun:
    """Read accepted points out of a previous run's journal.

    ``strict`` refuses the resume when the recipe fingerprint differs; pass
    ``False`` only to inspect a journal, never to continue a run.
    """
    path = Path(out_dir) / JOURNAL
    prior = PriorRun(path=str(path))
    if not path.exists():
        return prior

    traces: Dict[Tuple[str, str], dict] = {}
    results: Dict[Tuple[str, str], dict] = {}
    plans: Dict[Tuple[str, str], dict] = {}
    dropped: set = set()

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = e.get("kind")
            if kind == "run_started":
                # The FIRST run_started, not the last: the journal is appended
                # across runs, and a resumed run emits its own run_started
                # before it gets here. Taking the last one would compare the
                # recipe against itself, so a changed recipe would always
                # match and the refusal would never fire.
                if prior.recipe is None:
                    prior.recipe = e.get("recipe_document")
            elif kind == "point_started":
                plans[(e.get("procedure", ""), e.get("tag", ""))] = e
            elif kind == "point_result":
                key = (e.get("procedure", ""), e.get("tag", ""))
                results[key] = e
                dropped.discard(key)
            elif kind == "capture_trace":
                traces[(e.get("procedure", ""), e.get("tag", ""))] = e
            elif kind in ("point_rejected", "point_skipped"):
                # A later attempt may have superseded an earlier pass; the last
                # word in the journal wins.
                key = (e.get("procedure", ""), e.get("tag", ""))
                results.pop(key, None)
                dropped.add(key)

    for key, ev in results.items():
        analysis = _analysis_from_event(ev, traces.get(key))
        if analysis is None:
            continue
        procedure, tag = key
        plan_ev = plans.get(key, {})
        plan = {"frequency_Hz": plan_ev.get("frequency_Hz"),
                "B_target_T": plan_ev.get("B_target_T"),
                "dc_bias_A": plan_ev.get("dc_bias_A"),
                "voltage_V": plan_ev.get("voltage_V"),
                "duty": plan_ev.get("duty", 0.5)}
        point = MeasuredPoint(
            procedure=procedure, tag=tag, plan=plan, analysis=analysis,
            verdict=Verdict(True, []), accepted=True,
            extra={"restored_from_journal": True})
        prior.points[key] = point

    if recipe is not None and prior.recipe is not None:
        expect = recipe_fingerprint(recipe)
        got = _document_fingerprint(prior.recipe)
        prior.fingerprint = got
        if strict and expect != got:
            changed = _fingerprint_diff(recipe, prior.recipe) or ["unknown"]
            raise ResumeRefused(
                f"the journal in {out_dir} was written by a different recipe "
                f"({', '.join(changed)} changed). Resuming would mix points "
                f"taken under different conditions - start a fresh output "
                f"directory instead.")
    return prior


def _document_fingerprint(doc: dict) -> str:
    material = {k: doc.get(k) for k in ("dut", "procedures", "qc", "limits")}
    blob = json.dumps(material, sort_keys=True, default=float)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
