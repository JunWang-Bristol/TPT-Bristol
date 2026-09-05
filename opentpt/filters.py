"""Digital filtering for TPT waveforms — and where it must not be used.

Two different filters from two different Bristol codebases, and they are not
interchangeable:

**ATPT1.0** (`TPT-Bristol/tpt_calculations.py::filter`) uses a first-order
exponential moving average at ``w = 0.99``, applied to Vp, Vs and Cp together
immediately before the loss integral.  That is :func:`ema`, and it is the one
that belongs in the loss path — see its docstring for why equal application to
every channel is what makes it safe.

**OpenTPT** (`src/post_processor.py`) uses the rolling-window segment
reconstruction described below.  It is a much heavier transformation and must
never touch the loss integrand.

The rest of this module is the second one.  It is not a low-pass filter, and
that is the point.  A moving average rounds the corners of a square
wave and smears the apex of a triangle, both of which move the numbers this rig
exists to measure.  What the rolling window does instead is fit the waveform's
*known shape*:

* the drive voltage is piecewise CONSTANT — a square wave — so each segment is
  replaced by its mean;
* the magnetising current is piecewise LINEAR — a triangle — so each segment is
  replaced by the straight line between its endpoints.

Both preserve the switching instants exactly, because the segments are bounded
by the turning points rather than by a fixed window.

Finding those turning points is the actual algorithm.  A window of
``window`` samples is slid over the signal in steps of ``window/sensitivity``,
and a window's extremum counts as a real turning point only if it falls in the
*interior* of that window — not in its first or last 10 %.  A noise spike near
an edge sits at the boundary of most windows that contain it and is rejected;
a genuine apex sits in the middle of several windows and is accepted.  The
level recorded for the apex is a local mean about it, not the single extreme
sample, so the reconstruction is not anchored on the noisiest point available.

**DO NOT put a reconstructed current into the loss integral.**  This is not a
matter of degree, it is exact: core loss is

    Q = ∮ i · v dt

and if ``v`` is replaced by a perfect square ±V and ``i`` by straight lines
between the same apices, then over one cycle

    ∮ i·v dt = V·(T/2)·(i_min+i_max)/2 − V·(T/2)·(i_max+i_min)/2 = 0

identically, whatever the real loss was.  The hysteresis loop's area lives
entirely in the *departure* of the current from the ideal triangle, which is
precisely what this filter removes.  ``tests/test_filters.py`` demonstrates it
on a synthetic loop with a known answer.

So the reconstruction is offered for what it is genuinely good at — locating
switching edges, fitting a di/dt slope for inductance, and producing a clean
trace to plot — and :func:`assert_not_loss_bearing` exists so a future caller
cannot wire it into the loss path by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence, Tuple

import numpy as np


DEFAULT_WINDOW = 2000
DEFAULT_SENSITIVITY = 3
DEFAULT_EDGE_FRACTION = 0.1
#: Half-width, in samples, of the local mean taken about a turning point.
DEFAULT_APEX_HALFWIDTH = 20

FILTER_MODES = ("none", "ema", "ema_zero_phase", "reconstruct",
                "moving_average")

#: Weight used by ATPT1.0 (`tpt_calculations.calculate_VI_losses`).
DEFAULT_EMA_WEIGHT = 0.99


class FilterMisuse(RuntimeError):
    """A filter was about to be applied where it would change the answer."""


@dataclass
class FilterPolicy:
    """How captures are cleaned up.  Serialised into the recipe as ``filtering``.

    ``mode`` is deliberately ``none`` by default.  Every number this rig
    publishes today was measured unfiltered, so switching filtering on is a
    change to the dataset and has to be an explicit, recorded decision rather
    than a default that quietly moved.
    """

    mode: str = "none"
    #: EMA weight when ``mode == "ema"``. Larger = lighter filtering.
    ema_weight: float = DEFAULT_EMA_WEIGHT
    window: int = DEFAULT_WINDOW
    sensitivity: int = DEFAULT_SENSITIVITY
    edge_fraction: float = DEFAULT_EDGE_FRACTION
    apex_halfwidth: int = DEFAULT_APEX_HALFWIDTH
    # Applied to the di/dt fit only. That fit is a least-squares slope over a
    # window of a noisy channel, which is exactly what smoothing helps and
    # nothing it can bias — unlike the loss integral.
    smooth_inductance_fit: bool = True

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown filtering keys: {sorted(unknown)}")
        mode = d.get("mode", "none")
        if mode not in FILTER_MODES:
            raise ValueError(f"unknown filter mode {mode!r}; expected one of "
                             f"{list(FILTER_MODES)}")
        return cls(**d)

    def to_dict(self):
        return asdict(self)


# ─── The rolling window ───────────────────────────────────────────────────────

def turning_points(signal, *, window=DEFAULT_WINDOW,
                   sensitivity=DEFAULT_SENSITIVITY,
                   edge_fraction=DEFAULT_EDGE_FRACTION,
                   apex_halfwidth=DEFAULT_APEX_HALFWIDTH
                   ) -> Tuple[List[int], List[float]]:
    """Indices and levels of the waveform's apices.

    Returns ``(indices, levels)``, always starting at sample 0 and ending at
    the last sample, so the pair can be walked as segment boundaries directly.

    An extremum counts only if it lies in the interior of the window that
    found it — see the module docstring for why that rejects noise spikes that
    a simple ``argmax`` would not.
    """
    a = np.asarray(signal, dtype=float)
    n = a.size
    if n == 0:
        return [], []
    if n < 4 or window < 4:
        return [0, n - 1], [float(a[0]), float(a[-1])]

    window = int(min(window, n))
    step = max(1, window // max(1, int(sensitivity)))
    guard = max(1, int(window * edge_fraction))

    found = {}
    for start in range(0, n, step):
        stop = min(start + window, n)
        chunk = a[start:stop]
        if chunk.size < 4:
            continue
        lo = start + int(np.argmin(chunk))
        hi = start + int(np.argmax(chunk))
        for idx in (lo, hi):
            # Interior of THIS window, which is what makes the test
            # scale-free: a spike is interior to at most a couple of windows,
            # an apex to many.
            if start + guard <= idx < stop - guard:
                found[idx] = True

    idx = sorted(found)
    # Collapse near-duplicates: consecutive windows straddling the same apex
    # each report it, a sample or two apart.
    merged = []
    for i in idx:
        if merged and i - merged[-1] < guard:
            continue
        merged.append(i)

    points = [0] + [i for i in merged if 0 < i < n - 1] + [n - 1]
    half = max(0, int(apex_halfwidth))
    levels = [float(a[max(0, i - half):min(n, i + half + 1)].mean())
              for i in points]
    return points, levels


def reconstruct_square(signal, points=None, **kw):
    """Replace each segment with its mean — the drive-voltage model."""
    a = np.asarray(signal, dtype=float)
    if a.size == 0:
        return a.copy()
    if points is None:
        points, _ = turning_points(a, **kw)
    out = np.empty_like(a)
    for lo, hi in zip(points, points[1:]):
        hi = max(hi, lo + 1)
        out[lo:hi] = a[lo:hi].mean()
    out[points[-1]:] = a[points[-1]:].mean() if points[-1] < a.size else a[-1]
    return out


def reconstruct_triangular(signal, points=None, levels=None, **kw):
    """Straight lines between apices — the magnetising-current model."""
    a = np.asarray(signal, dtype=float)
    if a.size == 0:
        return a.copy()
    if points is None:
        points, levels = turning_points(a, **kw)
    if levels is None:
        levels = [float(a[i]) for i in points]
    return np.interp(np.arange(a.size), np.asarray(points, dtype=float),
                     np.asarray(levels, dtype=float))


def ema(a, w=DEFAULT_EMA_WEIGHT):
    """First-order exponential moving average — the ATPT1.0 filter.

    Ported from `TPT-Bristol/tpt_calculations.py::filter`, semantics kept
    exactly, including the sense of the weight:

        y[i] = (1 - w)*y[i-1] + w*x[i]

    so a LARGER ``w`` means LESS memory and a faster response. Their loss path
    uses ``w = 0.99``, which is a very gentle filter — it removes only the top
    of the noise band and leaves the waveform essentially intact.

    **Why this one is safe on the loss integrand and the segment
    reconstruction is not.** An EMA is a linear, causal filter with a group
    delay of about ``(1-w)/w`` samples — 0.0101 samples at w = 0.99, well
    under a nanosecond on this rig's timebase. Delay is the entire distortion,
    and ATPT1.0 applies the SAME filter to Vp, Vs and Cp together, so every
    channel is delayed identically and their RELATIVE phase — which is what
    the loss integral measures — is untouched.

    That is also the trap. Filtering one channel and not another is
    mathematically identical to injecting skew between them, and this rig's
    real skew is 8.7 ns. At w = 0.99 the induced offset is ~0.5 ns and
    harmless; at w = 0.5 it would be a full sample, tens of nanoseconds, and
    would bias every loss figure. Hence :func:`assert_balanced`.
    """
    if not 0.0 <= w <= 1.0:
        raise ValueError("w must be in the range [0, 1]")
    x = np.asarray(a, dtype=float)
    n = x.size
    if n == 0:
        return x.copy()
    if w == 0.0:
        out = np.empty_like(x)
        out[:] = x[0]
        return out
    if w == 1.0:
        return x.copy()
    r = 1.0 - w
    out = np.empty_like(x)
    y = x[0]
    out[0] = y
    for i in range(1, n):
        y = r * y + w * x[i]
        out[i] = y
    return out


def ema_zero_phase(a, w=DEFAULT_EMA_WEIGHT):
    """Forward-backward EMA — the filtfilt idea applied to ATPT's filter.

    Run the EMA forward, then run it again over the reversed result. The
    impulse response of the combination is symmetric, so the group delay is
    exactly ZERO at every frequency — not merely small, as the causal EMA's
    is at w = 0.99, but identically zero at any weight.

    Why this matters here: the causal EMA's one distortion is delay, and
    delay between channels is skew, which is why `assert_balanced` polices
    it. Zero-phase filtering removes that failure mode outright, so heavier
    weights become usable — the noise reduction of w = 0.9 without the
    5-sample lag that would otherwise inject ~250 ns of skew.

    The costs are the standard filtfilt ones: it is non-causal (fine — every
    capture here is post-processed), the effective attenuation is squared
    (the magnitude response is applied twice), and the ends of the record see
    a transient. The analysed cycle sits well inside the capture, so the end
    effects never reach it.
    """
    return ema(ema(a, w)[::-1], w)[::-1]


def ema_lag_samples(w=DEFAULT_EMA_WEIGHT):
    """Group delay of :func:`ema`, in samples. Zero at w = 1."""
    if w <= 0.0:
        return float("inf")
    return (1.0 - float(w)) / float(w)


def assert_balanced(where: str, weights):
    """Refuse to filter channels unequally.

    Unequal filtering IS skew: it shifts one channel relative to another and
    opens or closes the loop by that amount. The loss integral cannot tell
    that from a real phase difference in the DUT.
    """
    uniq = {round(float(x), 12) for x in weights}
    if len(uniq) > 1:
        raise FilterMisuse(
            f"{where}: channels would be filtered with different weights "
            f"{sorted(uniq)}. That is equivalent to injecting skew between "
            f"them - this bench's real skew is 8.7 ns, and a mismatched EMA "
            f"at w=0.5 would add tens of ns on top. Filter every channel the "
            f"same or not at all.")


def moving_average(signal, window=7):
    """Plain box filter, with the edges left alone.

    Carried over from ``src/tpt.py``'s ``smooth_signal`` because the di/dt fit
    was tuned against it. Edge samples keep their original values: a
    ``mode='same'`` convolution tapers the ends towards zero, and the ends of
    a capture window are exactly where a slope fit reaches.
    """
    a = np.asarray(signal, dtype=float)
    w = int(window)
    if w < 2 or a.size < w:
        return a.copy()
    out = np.convolve(a, np.ones(w) / w, mode="same")
    half = w // 2
    out[:half] = a[:half]
    if half:
        out[-half:] = a[-half:]
    return out


def apply(signal, policy: FilterPolicy, *, kind="current"):
    """Filter one channel according to ``policy``.

    ``kind`` picks the shape model: ``"voltage"`` is piecewise constant,
    anything else piecewise linear.
    """
    if policy is None or policy.mode == "none":
        return np.asarray(signal, dtype=float)
    if policy.mode == "ema":
        return ema(signal, policy.ema_weight)
    if policy.mode == "ema_zero_phase":
        return ema_zero_phase(signal, policy.ema_weight)
    if policy.mode == "moving_average":
        return moving_average(signal, policy.window if policy.window < 100 else 7)
    kw = dict(window=policy.window, sensitivity=policy.sensitivity,
              edge_fraction=policy.edge_fraction,
              apex_halfwidth=policy.apex_halfwidth)
    if kind == "voltage":
        return reconstruct_square(signal, **kw)
    return reconstruct_triangular(signal, **kw)


def assert_not_loss_bearing(where: str):
    """Guard for the one place this must never be called.

    Cheap insurance: the failure it prevents is silent. A reconstructed
    current gives a loop that looks entirely plausible — closed, balanced,
    correctly shaped — and reports a loss of zero, or of whatever residue the
    imperfect reconstruction happens to leave.
    """
    raise FilterMisuse(
        f"{where}: a reconstructed waveform must not enter the loss integral. "
        f"Piecewise-linear current against piecewise-constant voltage has "
        f"identically zero loop area, so the answer would be ~0 W regardless "
        f"of the real loss. Filter for edges, slopes and plots only.")
