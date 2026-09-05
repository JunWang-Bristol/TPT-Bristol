"""AC loss from the primary alone — the MADMIX-style measurement.

MADMIX (MinDCet) measures a power inductor as a component rather than as a
core: it is an instrumented buck converter, and the loss comes from
integrating the terminal voltage and current of the inductor under test over
time.  What that integral returns is the **total AC loss** — core *and*
winding — which is then split analytically by Fourier decomposition.  The
distinction from the two-winding TPT method is worth stating plainly because
it decides when each is the right tool:

===================  ==========================  =========================
                     two-winding (this rig)      single-winding (MADMIX)
===================  ==========================  =========================
loss integral        ``∮ i·v_sec dt``            ``∮ i·v_pri dt``
what it contains     core loss only              core + winding + dielectric
why                  the sense winding carries   the drive winding carries
                     no current, so its EMF is   the current, so its
                     pure ``N₂·dΦ/dt``           terminal voltage includes
                                                 ``i·R`` and ``L_leak·di/dt``
needs                a second winding            nothing extra
separation           none needed                 a model, or a second
                                                 measurement
===================  ==========================  =========================

So the single-winding mode is not a better core-loss measurement — it is a
*different* measurement that happens to need less hardware, and one that
measures the quantity a converter designer actually cares about.  On this rig,
where both windings exist and are both instrumented, running the two together
is worth more than either alone: their **difference is the winding loss**,
measured rather than modelled, and that is an independent check on the current
probe and the shunt calibration that nothing else here provides.

Flux without a sense winding
----------------------------
B still follows from the primary, via flux linkage rather than a sense EMF::

    λ(t) = ∫ (v_pri − i·R_dc) dt
    B(t) = λ(t) / (N₁·Ae)          H(t) = N₁·i(t) / le

The ``i·R_dc`` subtraction is not cosmetic.  Left in, the resistive drop
integrates into an apparent flux that lags the current, which tilts the loop
and adds a spurious area equal to the copper loss — i.e. exactly the term that
makes the single-winding integral differ from the two-winding one in the first
place.  Subtracting it recovers a B–H loop whose area is core loss again, to
the extent ``R_dc`` is right and the AC resistance rise is small.

Winding/core separation
-----------------------
:func:`winding_loss` does the Fourier split.  A square-wave-driven inductor's
current is a triangle, whose harmonics fall as 1/n², while ``R_ac`` rises
roughly as √n once past the skin-effect corner — so the high harmonics
contribute little and a crude ``R_ac(f)`` model is good enough to be useful.
The default model is the standard one-dimensional skin-effect rise,

    R_ac(f) = R_dc · √(1 + (f/f_skin)²)   [approximated; see the function]

with ``f_skin`` supplied per DUT.  With no model given the function returns the
DC copper loss alone, which is a strict lower bound, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np


@dataclass
class PrimaryLoss:
    """What one cycle of the primary terminals says."""

    P_ac_W: float               # total AC loss: core + winding
    Pv_ac_kW_m3: float          # ...as a density, for comparison with Pv
    Q_cycle_J: float
    i_rms_A: float
    P_winding_dc_W: float       # i_rms**2 * R_dc — the strict lower bound
    P_winding_W: float          # harmonic estimate, or the DC bound
    P_core_estimate_W: float    # P_ac - P_winding
    B_peak_T: float             # from flux linkage, R_dc removed
    R_dc_ohm: float
    separation: str             # how P_winding was obtained, in words

    def to_dict(self):
        return asdict(self)


def flux_linkage(t, v_pri, current, *, N1, Ae, R_dc=0.0, remove_offset=True):
    """B(t) from the drive winding alone.

    ``R_dc`` is subtracted from the terminal voltage before integrating. With
    a 10-turn primary on this DUT the winding is a few tens of milliohms and
    the drive current is under an amp, so the correction is millivolts against
    a ten-volt drive — small, but it is the whole of the difference between a
    loop whose area is core loss and one whose area is core plus copper.
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v_pri, dtype=float)
    i = np.asarray(current, dtype=float)
    emf = v - i * float(R_dc)
    lam = np.concatenate(([0.0], np.cumsum(0.5 * (emf[1:] + emf[:-1])
                                           * np.diff(t))))
    B = lam / (float(N1) * float(Ae))
    if remove_offset:
        B = B - B.mean()
    return B


def r_ac(frequency, R_dc, f_skin=None):
    """AC resistance at one frequency.

    One-dimensional skin-effect rise, written so it degrades gracefully: with
    no ``f_skin`` it returns ``R_dc`` and the caller reports the result as a
    lower bound rather than pretending to a separation it cannot do.

    The form is ``R_dc·√(1 + (f/f_skin)²)`` rather than the exact Bessel
    solution. Over the band this rig covers, and against the 1/n² fall of a
    triangle's harmonics, the difference is far below the uncertainty in
    ``f_skin`` itself.
    """
    if not f_skin or f_skin <= 0:
        return float(R_dc)
    return float(R_dc) * float(np.sqrt(1.0 + (float(frequency) / f_skin) ** 2))


def winding_loss(t, current, *, frequency, R_dc, f_skin=None, n_harmonics=25):
    """Copper loss of the drive winding, by harmonic superposition.

    Returns ``(watts, how)``.  ``how`` names the method in words so a reader
    of the dataset can tell a modelled number from a measured one — this is a
    place where it would be easy to publish a confident figure resting on a
    guessed ``f_skin``.
    """
    i = np.asarray(current, dtype=float)
    t = np.asarray(t, dtype=float)
    if i.size < 4:
        return 0.0, "not enough samples"

    i_rms = float(np.sqrt(np.mean(i ** 2)))
    if not f_skin:
        return i_rms ** 2 * float(R_dc), (
            "DC resistance only (no f_skin for this DUT) - a LOWER BOUND on "
            "the winding loss, so the core loss inferred from it is an UPPER "
            "bound")

    # One cycle, resampled to a power of two, so the harmonic amplitudes are
    # not smeared by a non-integer number of periods.
    n = 1 << int(np.floor(np.log2(max(8, i.size))))
    grid = np.linspace(t[0], t[-1], n, endpoint=False)
    iq = np.interp(grid, t, i)
    spec = np.fft.rfft(iq - iq.mean()) * (2.0 / n)

    total = 0.0
    for k in range(1, min(n_harmonics + 1, spec.size)):
        amp = float(np.abs(spec[k]))
        if amp <= 0:
            continue
        total += 0.5 * amp ** 2 * r_ac(k * frequency, R_dc, f_skin)
    # The DC component sees R_dc, not R_ac.
    total += float(iq.mean()) ** 2 * float(R_dc)
    return total, (f"harmonic superposition to {min(n_harmonics, spec.size-1)} "
                   f"harmonics, R_ac = R_dc*sqrt(1+(f/{f_skin/1e3:.0f} kHz)^2)")


def analyse_primary(t, v_pri, current, *, frequency, N1, Ae, Ve,
                    R_dc=0.0, f_skin=None) -> Optional[PrimaryLoss]:
    """Everything the primary terminals alone can say about one cycle.

    ``t`` must already be trimmed to a whole number of cycles — the caller
    knows where the cycle boundaries are, and an integral over a fractional
    cycle is the classic way to get a confident wrong answer here.
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v_pri, dtype=float)
    i = np.asarray(current, dtype=float)
    if t.size < 4:
        return None

    period = float(t[-1] - t[0])
    if period <= 0:
        return None
    f_meas = 1.0 / period

    Q = float(np.trapezoid(i * v, t))
    P_ac = abs(Q) * f_meas
    i_rms = float(np.sqrt(np.mean(i ** 2)))
    p_dc = i_rms ** 2 * float(R_dc)
    p_wind, how = winding_loss(t, i, frequency=frequency, R_dc=R_dc,
                               f_skin=f_skin)
    B = flux_linkage(t, v, i, N1=N1, Ae=Ae, R_dc=R_dc)

    return PrimaryLoss(
        P_ac_W=P_ac,
        Pv_ac_kW_m3=P_ac / float(Ve) / 1e3 if Ve else float("nan"),
        Q_cycle_J=abs(Q),
        i_rms_A=i_rms,
        P_winding_dc_W=p_dc,
        P_winding_W=p_wind,
        P_core_estimate_W=P_ac - p_wind,
        B_peak_T=float(np.ptp(B)) / 2.0,
        R_dc_ohm=float(R_dc),
        separation=how,
    )
