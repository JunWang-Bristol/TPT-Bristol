"""Quality gates — a point is published only if it survives all of them.

The rule this module enforces: **never a silent wrong number**.  Every reject
carries the name of the gate that failed and the numbers that failed it, so a
gap in a dataset is always explained rather than merely absent.

The gates are the ones this bench actually needed:

``capture``     the burst was caught at all — a scope that missed the train
                returns a flat V_pri and then produces a plausible-looking but
                entirely fictional loss figure.
``clipping``    the current channel saturated; on an 8-bit scope this is
                common and destroys ∮i·v dt.
``cycle``       a closed, edge-bounded cycle was found and is resolved by
                enough samples to integrate.
``imbalance``   net volt-seconds over that cycle are small, i.e. it really is
                a closed loop.
``saturation``  ripple current has not ballooned relative to the unbiased
                reference — past the knee, loss goes non-monotonic and the
                minor loop is no longer valid.
``resolution``  the current swing uses enough ADC codes to be worth
                integrating (the 2408B has 256 across the range).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

import numpy as np


@dataclass
class QCPolicy:
    """Thresholds for the gates.  Serialised into the recipe as ``qc``."""

    max_imbalance_pct: float = 5.0
    max_ipp_ratio: float = 3.0          # vs the unbiased reference point
    min_vpri_fraction: float = 0.5      # of the expected 2·V_rail swing
    min_cycle_samples: int = 20
    min_current_codes: int = 24         # of 256 on an 8-bit scope
    retries: int = 3

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown qc keys: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self):
        return asdict(self)


@dataclass
class Failure:
    gate: str
    message: str
    measured: Optional[float] = None
    limit: Optional[float] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class Verdict:
    """Outcome of the whole gate set for one capture."""

    passed: bool
    failures: List[Failure] = field(default_factory=list)

    @property
    def reason(self):
        return "; ".join(f.message for f in self.failures)

    def to_dict(self):
        return {"passed": self.passed,
                "failures": [f.to_dict() for f in self.failures]}


def is_clipped(signal, full_scale=None, *, min_flat=5, frac=0.98):
    """True if the channel saturated.

    Two independent detectors, because either alone gives false negatives:
    a run of ``min_flat`` identical samples at the extreme (the ADC railing),
    and — when the channel's full scale is known — the peak reaching
    ``frac`` of it.
    """
    a = np.asarray(signal, dtype=float)
    if a.size == 0:
        return False

    if full_scale is not None and full_scale > 0:
        if float(np.abs(a).max()) >= frac * full_scale:
            return True

    pk_pk = float(np.ptp(a))
    if pk_pk < 1e-12:
        return False
    eps = pk_pk * 0.005
    for mask in (a >= a.max() - eps, a <= a.min() + eps):
        padded = np.concatenate(([False], mask, [False]))
        starts = np.flatnonzero(~padded[:-1] & padded[1:])
        ends = np.flatnonzero(padded[:-1] & ~padded[1:])
        if starts.size and int((ends - starts).max()) >= min_flat:
            return True
    return False


def evaluate(analysis, policy, *, expected_vpri_pp=None, clipped=False,
             i_pp_reference=None, current_lsb=None) -> Verdict:
    """Run every gate against one analysed capture.

    Parameters
    ----------
    analysis          : :class:`~opentpt.analysis.CycleAnalysis` or ``None``
                        (``None`` means no closed cycle was found)
    policy            : :class:`QCPolicy`
    expected_vpri_pp  : float — the rail-to-rail swing the drive should produce
    clipped           : bool  — result of :func:`is_clipped` on the raw current
    i_pp_reference    : float — unbiased ripple current for the saturation gate
    current_lsb       : float — amps per ADC code, for the resolution gate
    """
    fails: List[Failure] = []

    if clipped:
        fails.append(Failure("clipping",
                             "current channel is clipping — widen the range"))

    if analysis is None:
        fails.append(Failure("cycle", "no closed +/- cycle found in the capture"))
        return Verdict(False, fails)

    if expected_vpri_pp:
        need = policy.min_vpri_fraction * expected_vpri_pp
        if analysis.v_pri_pp < need:
            fails.append(Failure(
                "capture",
                f"capture missed the burst: V_pri {analysis.v_pri_pp:.1f} Vpp "
                f"< {need:.1f} Vpp expected",
                analysis.v_pri_pp, need))

    if analysis.n_samples < policy.min_cycle_samples:
        fails.append(Failure(
            "cycle",
            f"cycle resolved by only {analysis.n_samples} samples",
            analysis.n_samples, policy.min_cycle_samples))

    imb = analysis.imbalance_pct
    if not np.isfinite(imb) or imb > policy.max_imbalance_pct:
        fails.append(Failure(
            "imbalance",
            f"volt-second imbalance {imb:.2f}% — not a closed loop",
            imb, policy.max_imbalance_pct))

    if i_pp_reference:
        ratio = analysis.i_pp / i_pp_reference
        if ratio > policy.max_ipp_ratio:
            fails.append(Failure(
                "saturation",
                f"ripple current {ratio:.1f}x the unbiased value — core is "
                f"saturating, the minor loop is not valid",
                ratio, policy.max_ipp_ratio))

    if current_lsb and current_lsb > 0:
        codes = analysis.i_pp / current_lsb
        if codes < policy.min_current_codes:
            fails.append(Failure(
                "resolution",
                f"current swing uses only {codes:.0f} ADC codes — the loop "
                f"integral would be quantisation noise",
                codes, policy.min_current_codes))

    return Verdict(not fails, fails)


def check_monotonic_loss(rows, key="Pv_kW_m3"):
    """Indices of bias points where loss stops rising — the saturation tell.

    Past the knee the measured loss goes *down* while ripple current balloons.
    A single point can look perfectly clean on its own gates and still be
    invalid; only the sequence reveals it, so this runs after a bias sweep.
    """
    bad = []
    for k in range(1, len(rows)):
        if rows[k][key] < rows[k - 1][key]:
            bad.append(k)
    return bad
