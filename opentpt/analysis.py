"""Pure waveform reductions for the Triple Pulse Test.

Everything here is numpy in, numbers out — no instruments, no printing, no
files.  That is deliberate: every number the engine reports must be
re-derivable from a stored raw waveform by calling these functions, which is
what makes a dataset reproducible from its own header.

Method (Wang, Yuan & Rasekh, IECON 2020)
----------------------------------------
    H(t) = N1 · i(t) / le                        [A/m]
    B(t) = 1/(N2·Ae) ∫ v_sec dt                  [T]
    Q    = |N1/N2 · ∮ i·v_sec dt|                [J/cycle]
    P    = Q · f      Pv = P / Ve

Two bugs this module exists to make un-repeatable, both found on this rig:

1.  **Bound the cycle with real edges.**  Using ``t[i0] + 1/f`` leaves a
    partial cycle whenever the true period differs from 1/f (deadtime, loop
    overhead).  The leftover fraction makes the net volt-second integral
    non-zero and fakes a 10-25 % imbalance on a bridge genuinely balanced to
    0.3 %.  :func:`find_cycles` uses consecutive detected rising edges.

2.  **Never assume the drive is the measurement.**  B comes from the *sense*
    winding integral and H from the *measured* current, so flux-targeting
    error cannot distort a result — compare at measured B, never target B.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

# numpy renamed trapz to trapezoid in 2.0. This bench runs 1.25, so bind the
# name once here rather than sprinkling version checks through the reductions.
if not hasattr(np, "trapezoid"):        # pragma: no cover - numpy < 2.0
    np.trapezoid = np.trapz

MU0 = 4.0e-7 * np.pi


# ─── Cycle location ───────────────────────────────────────────────────────────

def find_cycles(t, v_pri, frequency, *, threshold=0.3, period_tol=0.35,
                min_samples=20, consensus_tol=0.08, edge_filter=None):
    """Return ``[(i0, i1), ...]`` for every complete +/− cycle in the record.

    A cycle runs from one rising zero-crossing of ``v_pri`` to the next, so it
    is a genuinely closed loop bounded by *measured* edges.

    Two filters, and the second is the one that matters.  ``period_tol``
    rejects the obvious rubbish against 1/f — ringing spikes, half-cycles.  It
    cannot be tight, because the real period is legitimately *longer* than 1/f:
    the firmware inserts its deadtime between commanded pulses, which is ~1 %
    at 25 kHz but ~20 % at 200 kHz.  So a fixed fraction of 1/f can never
    separate "deadtime-lengthened" from "wrong".

    ``consensus_tol`` does that separation without needing to know the
    deadtime: within one burst every cycle has the same period, so the *median*
    of the candidates is the true period and an outlier is contamination.  This
    is what rejects the final cycle of a record whose closing edge has landed
    in the post-train LC ringdown — on this rig that window comes out ~11 %
    long and looks entirely plausible, and it silently shifted a 100 kHz point
    by 12 % before this filter existed.

    Parameters
    ----------
    t, v_pri      : 1-D arrays — time [s] and primary voltage [V]
    frequency     : float — nominal switching frequency [Hz]
    threshold     : float — trigger level as a fraction of |v_pri| peak
    period_tol    : float — coarse gate: accept periods within ±tol of 1/f
    min_samples   : int   — reject cycles resolved by fewer samples than this
    consensus_tol : float — fine gate: accept periods within ±tol of the median
                            of the candidates (needs 3+ candidates to apply)
    edge_filter   : :class:`~opentpt.filters.FilterPolicy` or ``None`` — reconstruct
                    ``v_pri`` as a clean square before locating the edges.
                    Locating edges is the one job the reconstruction is
                    unambiguously good at, and doing it here cannot touch the
                    loss: only the returned INDICES are used, and every
                    integral downstream runs on the raw samples.
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v_pri, dtype=float)
    if t.size < 4 or v.size != t.size:
        return []

    if edge_filter is not None and getattr(edge_filter, "mode", "none") != "none":
        from . import filters as _flt
        v = _flt.apply(v, edge_filter, kind="voltage")

    peak = float(np.abs(v).max())
    if peak <= 0.0:
        return []
    th = threshold * peak

    # Hysteretic sign: +1 above +th, −1 below −th, 0 in the dead band.  A
    # rising edge is a transition into +1 from anything else, which ignores
    # the noise chatter a bare ``v > 0`` test would count as many edges.
    s = np.where(v > th, 1, np.where(v < -th, -1, 0))
    rises = np.flatnonzero((s[1:] == 1) & (s[:-1] <= 0)) + 1
    if rises.size < 2:
        return []

    lo, hi = (1.0 - period_tol) / frequency, (1.0 + period_tol) / frequency
    cycles = []
    for i0, i1 in zip(rises[:-1], rises[1:]):
        if (i1 - i0) < min_samples:
            continue
        if lo <= (t[i1] - t[i0]) <= hi:
            cycles.append((int(i0), int(i1)))

    if len(cycles) >= 3:
        periods = np.array([t[b] - t[a] for a, b in cycles])
        med = float(np.median(periods))
        if med > 0:
            keep = np.abs(periods - med) / med <= consensus_tol
            if keep.any():
                cycles = [c for c, k in zip(cycles, keep) if k]
    return cycles


def last_full_cycle(t, v_pri, frequency, **kw):
    """Index range ``(i0, i1)`` of the final complete cycle, or ``(None, None)``.

    The last cycle is the steady-state one: the magnetising current has
    settled and the minor loop has closed around its operating point.
    """
    cycles = find_cycles(t, v_pri, frequency, **kw)
    if not cycles:
        return None, None
    return cycles[-1]


# ─── Field quantities ─────────────────────────────────────────────────────────

def _cumtrapz(y, x):
    """Cumulative trapezoidal integral of y over x, same length as y."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    out = np.zeros_like(y)
    out[1:] = np.cumsum(np.diff(x) * (y[:-1] + y[1:]) * 0.5)
    return out


def flux_density(t, v_sec, N2, Ae, *, remove_offset=True):
    """B(t) = 1/(N2·Ae) ∫ v_sec dt  [T].

    The integration constant is unknowable from an AC capture, so the mean is
    removed by default: over a closed cycle that centres the loop on B=0,
    which is the correct convention for an AC amplitude.  A DC *bias* lives in
    H (the measured current), not in B — do not confuse the two.
    """
    B = _cumtrapz(v_sec, t) / (N2 * Ae)
    if remove_offset:
        B = B - B.mean()
    return B


def magnetic_field(current, N1, le):
    """H(t) = N1·i(t)/le  [A/m]."""
    return N1 * np.asarray(current, dtype=float) / le


def volt_second_imbalance(t, v_pri):
    """Net volt-seconds as a percentage of the absolute volt-seconds.

    Zero means the positive and negative half-cycles cancel exactly, i.e. the
    window really is a closed loop.  This is the single most useful capture
    quality gate on this rig: a missed burst, a partial cycle or a genuinely
    lopsided bridge all show up here.
    """
    net = float(np.trapezoid(v_pri, t))
    tot = float(np.trapezoid(np.abs(v_pri), t))
    return abs(net) / tot * 100.0 if tot > 0 else float("nan")


# ─── Permeability ─────────────────────────────────────────────────────────────

def secant_permeability(B_peak, H_peak):
    """Amplitude (secant) permeability µ = B̂ / (µ0·Ĥ), dimensionless.

    With an unbiased loop this is µ_a; measured on a biased minor loop with
    peak-to-peak halves it is the incremental permeability µ_Δ.  Same
    arithmetic, different excitation — the distinction is a recipe condition,
    not a different formula.
    """
    if H_peak == 0:
        return float("nan")
    return float(B_peak / (MU0 * H_peak))


def _fundamental_phasor(t, y):
    """Complex amplitude of the fundamental of y over exactly one period.

    Single-bin DFT evaluated by trapezoid over the *measured* cycle length, so
    it needs neither uniform sampling nor a power-of-two record.  Returns the
    amplitude convention (peak, not RMS): a pure cos(ωt) of amplitude A gives A.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    T = t[-1] - t[0]
    if T <= 0:
        return complex("nan")
    phase = 2.0 * np.pi * (t - t[0]) / T
    return complex(2.0 / T * np.trapezoid(y * np.exp(-1j * phase), t))


def complex_permeability(t, B, H):
    """Large-signal complex permeability at the fundamental.

    Takes the fundamental phasors of the measured B(t) and H(t) over one closed
    cycle and forms µ = B₁/(µ0·H₁).  With the physics sign convention
    µ = µ′ − j·µ″ (B lags H), so µ″ > 0 for a lossy core.

    Returns ``(mu_prime, mu_second, tan_delta)``.

    This is amplitude-conditioned *by design*: the TPT drive is a large-signal
    square wave, so these are the large-signal µ′/µ″ at B̂, not the
    small-signal spectrum an impedance analyser reports.  Both are legitimate;
    they are different quantities and MAS records the excitation with the
    point.

    Cross-check: for the fundamental alone, Pv = π·f·µ0·µ″·Ĥ².  Comparing that
    against the loop-area Pv is a free consistency test — the two agree only
    to the extent the waveform is sinusoidal, so the *gap* measures harmonic
    content rather than an error.  :func:`analyse_cycle` reports both.
    """
    B1 = _fundamental_phasor(t, B)
    H1 = _fundamental_phasor(t, H)
    if not np.isfinite(abs(H1)) or abs(H1) == 0:
        return float("nan"), float("nan"), float("nan")
    mu = B1 / (MU0 * H1)
    mu_prime = float(mu.real)
    mu_second = float(-mu.imag)
    tan_delta = mu_second / mu_prime if mu_prime != 0 else float("nan")
    return mu_prime, mu_second, tan_delta


def remanence_coercivity(B, H):
    """Dynamic remanence B_r [T] and coercivity H_c [A/m] of a closed loop.

    B_r is |B| where the loop crosses the H axis; H_c is |H| where it crosses
    the B axis, averaged over the crossings of the closed loop.

    Both axes are taken at the loop's own **mid-swing**, not at absolute zero.
    That matters: the current channel on this rig carries a residual DC offset
    of tens of milliamps even at zero requested bias, so H never crosses true
    zero at the symmetric point of the loop.  Measuring against absolute zero
    made B_r come out at 82 % of B̂ on a 100 kHz capture whose loss angle
    implies about 23 % — the offset, not the ferrite.

    These are **dynamic** values: AC loop crossings at the capture's frequency,
    not the DC catalogue figures, and they are only meaningful for a loop that
    was not deliberately biased.  The exporter enforces the latter.
    """
    B = np.asarray(B, dtype=float)
    H = np.asarray(H, dtype=float)
    B = B - 0.5 * (B.max() + B.min())
    H = H - 0.5 * (H.max() + H.min())

    def _crossings(x, y):
        """|y| at every zero crossing of x, by linear interpolation."""
        idx = np.flatnonzero(np.sign(x[:-1]) * np.sign(x[1:]) < 0)
        out = []
        for i in idx:
            x0, x1 = x[i], x[i + 1]
            frac = x0 / (x0 - x1)
            out.append(abs(y[i] + frac * (y[i + 1] - y[i])))
        return out

    br = _crossings(H, B)
    hc = _crossings(B, H)
    return (float(np.mean(br)) if br else float("nan"),
            float(np.mean(hc)) if hc else float("nan"))


# ─── Inductance ───────────────────────────────────────────────────────────────

def inductance_from_ramp(t, v, i, *, edge_margin=0.25, smooth=0):
    """L = V_avg / (di/dt) over the flat middle of the first positive pulse.

    The outer ``edge_margin`` of the pulse is discarded at each end so
    switching ringing does not enter the regression.  Returns ``None`` when no
    usable pulse is present rather than a meaningless number.

    ``smooth`` applies a box filter of that width to the current first.  This
    is the ONE place in this module where filtering the current is safe: a
    least-squares slope is a linear functional, so a symmetric smoother of odd
    width leaves it unbiased — it only shrinks the variance a noisy channel
    contributes.  The loss integral has no such property, which is why nothing
    here filters it (see :mod:`opentpt.filters`).
    """
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    i = np.asarray(i, dtype=float)
    if smooth and smooth > 1:
        from .filters import moving_average
        i = moving_average(i, int(smooth))

    if float(np.ptp(v)) < 0.05:
        return None
    th = float(np.max(v)) * 0.2
    above = np.flatnonzero(v > th)
    if above.size == 0:
        return None
    rise = int(above[0])
    after = np.flatnonzero(v[rise:] < th)
    if after.size == 0:
        return None
    fall = rise + int(after[0])
    if fall - rise < 5:
        return None

    margin = int((fall - rise) * edge_margin)
    sl = slice(rise + margin, fall - margin)
    tw, iw, vw = t[sl], i[sl], v[sl]
    if tw.size < 3:
        return None

    tc = tw - tw.mean()
    denom = float(np.sum(tc ** 2))
    if denom < 1e-30:
        return None
    slope = float(np.sum(tc * (iw - iw.mean())) / denom)   # A/s
    if abs(slope) < 1.0:
        return None
    return abs(float(np.mean(vw)) / slope)


def harmonic_loss(t, v, i, *, n_harmonics=25):
    """Loss over one cycle from the harmonic domain: P = Σ Re{V_k · I_k*}.

    Only spectral content AT the harmonics of the excitation carries real
    power; a square drive against a triangular current concentrates
    essentially all of it in the first couple of dozen odd harmonics.
    Truncating the sum there therefore keeps the physics and discards the
    broadband noise floor between the harmonics — which the time-domain
    integral ∮i·v dt integrates in full. This is the classic
    frequency-domain wattmeter, and it is a noise FILTER that cannot
    introduce phase error: each harmonic's V and I come from the same
    record, so their relative phase is untouched.

    ``t`` must span exactly one cycle on a uniform grid (the capture is).
    With ``n_harmonics`` covering the whole spectrum this equals the
    time-domain result exactly (Parseval); the tests pin that.
    """
    v = np.asarray(v, dtype=float)
    i = np.asarray(i, dtype=float)
    n = v.size
    if n < 4:
        return float("nan")
    V = np.fft.rfft(v)
    I = np.fft.rfft(i)
    k_max = min(int(n_harmonics), V.size - 1)
    # Mean power: DC term plus twice the real cross-spectrum of each
    # retained harmonic, with numpy's unnormalised rfft convention.
    p = float(np.real(V[0] * np.conj(I[0])))
    p += 2.0 * float(np.sum(np.real(V[1:k_max + 1]
                                    * np.conj(I[1:k_max + 1]))))
    return p / (n * n)


# ─── Whole-cycle reduction ────────────────────────────────────────────────────

@dataclass
class CycleAnalysis:
    """Every quantity derivable from one closed cycle of a TPT capture.

    One capture, every reduction: loss, the loop itself, all permeabilities,
    and the QC inputs.  The engine stores this whole record with the point so
    nothing has to be re-measured to add a column later.
    """

    # window
    i0: int
    i1: int
    n_samples: int
    period_s: float
    frequency_Hz: float           # nominal, as commanded
    frequency_measured_Hz: float  # 1 / the measured cycle period

    # wiring, resolved from the data rather than assumed
    sense_polarity_inverted: bool
    turns_ratio_measured: float

    # excitation / QC inputs
    v_pri_pp: float
    i_pp: float
    imbalance_pct: float

    # magnetics
    B_peak_T: float
    H_peak_A_m: float
    I_dc_A: float
    H_dc_A_m: float

    # loss
    Q_cycle_J: float
    P_core_W: float
    Pv_kW_m3: float

    # permeability
    mu_secant: float
    mu_prime: float
    mu_second: float
    tan_delta: float
    Pv_from_mu_second_kW_m3: float

    # loop landmarks (dynamic, frequency-dependent)
    remanence_T: float
    coercivity_A_m: float

    # The same cycle read from the PRIMARY terminals alone — the MADMIX-style
    # measurement. Free to compute here because all three channels are
    # already captured, and their DIFFERENCE is a measured winding loss rather
    # than a modelled one. See :mod:`opentpt.single_winding`.
    P_ac_primary_W: float = float("nan")
    Pv_primary_kW_m3: float = float("nan")
    P_winding_measured_W: float = float("nan")   # primary - secondary
    B_peak_primary_T: float = float("nan")       # from flux linkage
    i_rms_A: float = float("nan")

    # loop trace, kept out of the flat record
    t: np.ndarray = field(repr=False, default=None)
    B: np.ndarray = field(repr=False, default=None)
    H: np.ndarray = field(repr=False, default=None)

    def to_dict(self, *, with_loop=False):
        """Flat JSON-safe dict; the B–H trace is included only on request."""
        d = {k: v for k, v in asdict(self).items() if k not in ("t", "B", "H")}
        if with_loop and self.B is not None:
            d["bh_cycle"] = [
                {"magneticField": float(h), "magneticFluxDensity": float(b)}
                for h, b in zip(self.H, self.B)
            ]
        return d


def analyse_cycle(t, v_pri, v_sec, current, *, frequency, N1, N2, Ae, le, Ve,
                  window=None, edge_filter=None, R_dc_ohm=0.0,
                  f_skin_Hz=None) -> Optional[CycleAnalysis]:
    """Reduce one capture to a :class:`CycleAnalysis`, or ``None`` if no cycle.

    Parameters
    ----------
    t, v_pri, v_sec, current : 1-D arrays — time [s], volts, volts, amps
    frequency : float — nominal switching frequency [Hz]
    N1, N2    : int   — primary (drive) and secondary (sense) turns
    Ae, le, Ve: float — effective area [m²], path length [m], volume [m³]
    window    : ``(i0, i1)`` to force a window instead of locating the last one
    edge_filter : filter policy used ONLY to locate the cycle boundaries. The
                integrals below always run on the raw samples — see
                :mod:`opentpt.filters` for why a filtered current cannot be
                allowed anywhere near the loss integral.
    """
    t = np.asarray(t, dtype=float)
    v_pri = np.asarray(v_pri, dtype=float)
    v_sec = np.asarray(v_sec, dtype=float)
    current = np.asarray(current, dtype=float)

    i0, i1 = (window if window is not None
              else last_full_cycle(t, v_pri, frequency, edge_filter=edge_filter))
    if i0 is None:
        return None

    tc = t[i0:i1]
    vpc = v_pri[i0:i1]
    vsc = v_sec[i0:i1]
    ic = current[i0:i1]

    # ── Resolve the sense-winding polarity from physics, not from assumption.
    # A passive core cannot generate energy, so the sign of ∮i·v_sec dt fixes
    # the relative orientation of the sense winding and the current probe
    # unambiguously.  On this bench the sense winding is wired inverted
    # (V_sec is anti-phase with V_pri), which the old abs() around Q silently
    # absorbed — and which would otherwise emit a *negative* µ′.
    #
    # Correcting it here rather than taking abs() later keeps B, H, µ′, µ″,
    # B_r and H_c all mutually consistent, and surfaces the wiring state as a
    # reportable flag instead of hiding it.
    Q_signed = float(N1) / float(N2) * float(np.trapezoid(ic * vsc, tc))
    inverted = Q_signed < 0.0
    if inverted:
        vsc = -vsc
        Q_signed = -Q_signed

    # B is integrated over the window only, so the drift of a long record
    # cannot leak into the loop; the mean removal then centres it.
    B = flux_density(tc, vsc, N2, Ae)
    H = magnetic_field(ic, N1, le)

    period = float(tc[-1] - tc[0])
    f_meas = 1.0 / period if period > 0 else float("nan")

    Q = abs(Q_signed)
    # Loss uses the MEASURED repetition rate, not the commanded one: the
    # firmware's deadtime sits between pulses, so the core's real period runs
    # a per cent or so longer than 1/f and Pv would be overstated by the same
    # amount.  Both frequencies are recorded so the difference is auditable.
    P = Q * f_meas if np.isfinite(f_meas) else Q * frequency
    Pv = P / Ve / 1e3

    v_pri_pp = float(np.ptp(vpc))
    v_sec_pp = float(np.ptp(vsc))
    # The turns ratio is taken from VOLT-SECONDS, not peak-to-peak.
    #
    # Raw pp is not a turns-ratio estimator on this bench. Both voltage
    # channels are dominated by switching ringing, and they reach the scope
    # through two separate on-board 9k:1k dividers whose compensation
    # trimmers (C13/C14/C24, 2-6 pF) see different cable capacitance — so the
    # two channels transmit the overshoot spike differently, and the spike is
    # what sets pp. Measured across one sweep of this 10:10 toroid, the pp
    # ratio scattered 0.849-1.457 and averaged 1.008 at 50 kHz against 1.213
    # at 100 kHz, which tripped the wiring warning on a correctly wound DUT.
    #
    # The volt-second ratio over the same captures was 0.979-1.002, every
    # point, both frequencies. That is the quantity that matters: B is the
    # INTEGRAL of V_sec, so integration is what the flux actually depends on,
    # and integration suppresses exactly the high-frequency content the two
    # dividers disagree about. It is also why the flux came out at 50.3-50.8
    # and 101-103 mT against 50 and 100 mT targets while pp claimed a 21 %
    # winding error.
    vs_pri = float(np.trapezoid(np.abs(vpc), tc))
    vs_sec = float(np.trapezoid(np.abs(vsc), tc))
    ratio = vs_sec / vs_pri if vs_pri else float("nan")

    B_peak = float(np.ptp(B)) / 2.0
    H_peak = float(np.ptp(H)) / 2.0
    I_dc = float(ic.mean())

    mu_p, mu_s, tand = complex_permeability(tc, B, H)
    # Fundamental-only loss density, for the harmonic-content cross-check.
    pv_mu = (np.pi * f_meas * MU0 * mu_s * H_peak ** 2 / 1e3
             if np.isfinite(mu_s) and np.isfinite(f_meas) else float("nan"))
    br, hc = remanence_coercivity(B, H)

    # The MADMIX-style reading of the SAME cycle: total AC loss at the
    # primary terminals, which is core + winding. Subtracting the
    # secondary-derived core loss leaves the winding loss as a MEASURED
    # quantity, not a modelled one — and the two together are the only
    # cross-check this rig has on the current probe and shunt calibration,
    # because both loss figures use the same current and only one of them
    # uses the sense winding.
    from .single_winding import analyse_primary
    prim = analyse_primary(tc, vpc, ic, frequency=frequency, N1=N1, Ae=Ae,
                           Ve=Ve, R_dc=R_dc_ohm, f_skin=f_skin_Hz)

    return CycleAnalysis(
        i0=int(i0), i1=int(i1), n_samples=int(i1 - i0),
        period_s=period, frequency_Hz=float(frequency),
        frequency_measured_Hz=f_meas,
        sense_polarity_inverted=bool(inverted),
        turns_ratio_measured=ratio,
        v_pri_pp=v_pri_pp, i_pp=float(np.ptp(ic)),
        imbalance_pct=volt_second_imbalance(tc, vpc),
        B_peak_T=B_peak, H_peak_A_m=H_peak,
        I_dc_A=I_dc, H_dc_A_m=float(N1 * I_dc / le),
        Q_cycle_J=Q, P_core_W=P, Pv_kW_m3=Pv,
        mu_secant=secant_permeability(B_peak, H_peak),
        mu_prime=mu_p, mu_second=mu_s, tan_delta=tand,
        Pv_from_mu_second_kW_m3=float(pv_mu),
        remanence_T=br, coercivity_A_m=hc,
        P_ac_primary_W=prim.P_ac_W if prim else float("nan"),
        Pv_primary_kW_m3=prim.Pv_ac_kW_m3 if prim else float("nan"),
        P_winding_measured_W=(prim.P_ac_W - P) if prim else float("nan"),
        B_peak_primary_T=prim.B_peak_T if prim else float("nan"),
        i_rms_A=prim.i_rms_A if prim else float("nan"),
        t=tc, B=B, H=H,
    )
