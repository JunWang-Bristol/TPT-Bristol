"""What this bench can actually reach, computed rather than assumed.

Every constraint here is a real one that has bitten a run:

* **PSU floor** — a supply that ignores setpoints below some voltage makes low
  flux at low frequency simply unreachable; the planner already refuses those,
  but nothing showed the operator *where* the floor lies.  Zero on this bench,
  since the BK9129B regulates to 0 V.
* **PSU ceiling** — B scales as V/f, so the top of the flux range collapses as
  frequency rises. 200 mT is fine at 50 kHz and impossible at 100 kHz.
* **Core saturation** — the one that is not about the electronics. A point at
  200 mT AC plus 100 mA DC bias drove the measured permeability from 3535 to
  485 and the ripple current from 0.63 A to 4.75 A: the core saturated, and
  the "bias" that came back was meaningless. B_ac + B_dc has to stay under
  the material's saturation flux.
* **Scope timebase** — the 2408B cannot sustain three channels below ~16 ns,
  which with 50 samples per half-period caps the frequency.
* **Current limit** — the recipe's i_limit_A, which this supply cannot
  enforce in hardware, so it is a planning constraint or nothing.

Returns plain dicts so the CLI, the Studio and the tests can all render the
same numbers without importing each other.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .drive import voltage_to_flux

# Scope-side ceiling: 2408B with three channels live.
MIN_DT_S = 16e-9
SAMPLES_PER_HALF_PERIOD = 50

# Fallback saturation flux when the catalogue has no figure for the material.
# 3C90 is 0.47 T at 25 C, 0.38 T at 100 C; the lower value is the safe one to
# plan against because the core self-heats during a sweep.
DEFAULT_B_SAT_T = 0.38

# The catalogue's B_sat is DEEP saturation (3C90: 470 mT at 25 C, measured at
# H = 1200 A/m). The usable knee is far below it, and planning against B_sat
# lets through points the core cannot hold. Three independent measurements on
# this DUT put the knee at 0.60 x B_sat:
#
#   * DC bias at 200 mT AC collapsed mu_a from 3535 to 485 between 52 mA and
#     100 mA, i.e. a total peak of 249 -> 281 mT;
#   * the guarded saturation ramp stopped at 15 V / 25 kHz = 273 mT;
#   * 0.60 x 470 mT = 282 mT.
#
# So B_knee is what the planner should respect; B_sat is reported alongside it
# only so the margin is visible.
KNEE_FRACTION = 0.60


def scope_frequency_ceiling_Hz(samples_per_half=SAMPLES_PER_HALF_PERIOD,
                               min_dt_s=MIN_DT_S) -> float:
    """Highest frequency the timebase can resolve at the configured density."""
    return 1.0 / (2.0 * samples_per_half * min_dt_s)


def saturation_flux_T(material: str, temperature_C: float = 25.0) -> Dict:
    """B_sat from the OpenMagnetics catalogue, or a stated fallback."""
    try:
        from . import omdb
        doc = omdb.material_document(material)
    except Exception:                                    # noqa: BLE001
        doc = None
    points = (doc or {}).get("saturation") or []
    best, best_gap = None, None
    for p in points:
        try:
            t = float(p.get("temperature"))
            b = float(p.get("magneticFluxDensity"))
        except (TypeError, ValueError):
            continue
        gap = abs(t - temperature_C)
        if best_gap is None or gap < best_gap:
            best, best_gap = (b, t), gap
    if best:
        return {"B_sat_T": best[0], "at_temperature_C": best[1],
                "source": "OpenMagnetics catalogue"}
    return {"B_sat_T": DEFAULT_B_SAT_T, "at_temperature_C": 100.0,
            "source": "fallback (no catalogue entry)"}


def envelope(dut, limits, frequencies=None) -> List[Dict]:
    """Reachable flux band at each frequency, and what bounds it.

    ``B_min`` is set by the supply's floor, ``B_max`` by whichever of the
    supply ceiling, the current limit or core saturation binds first — the
    ``B_max_limited_by`` field says which, because "why can't I measure this
    point" is the question an operator actually asks.
    """
    if frequencies is None:
        frequencies = [1e3, 5e3, 10e3, 25e3, 50e3, 100e3, 150e3, 200e3]

    sat = saturation_flux_T(dut.material, getattr(dut, "temperature_C", 25.0))
    b_sat = sat["B_sat_T"]
    b_knee = KNEE_FRACTION * b_sat
    f_ceiling = scope_frequency_ceiling_Hz()
    dead = getattr(limits, "deadtime_s", 0.0)
    L = getattr(dut, "L_estimate_H", None)
    i_limit = getattr(limits, "i_limit_A", None)

    rows = []
    for f in frequencies:
        row = {"frequency_Hz": f, "reachable": True, "notes": []}
        if f < limits.f_min_Hz or f > limits.f_max_Hz:
            row["reachable"] = False
            row["notes"].append(
                f"outside the recipe's {limits.f_min_Hz/1e3:.0f}-"
                f"{limits.f_max_Hz/1e3:.0f} kHz band")
        if f > f_ceiling:
            row["reachable"] = False
            row["notes"].append(
                f"above the scope timebase ceiling ({f_ceiling/1e3:.0f} kHz)")

        b_min = voltage_to_flux(limits.psu_min_V, dut.N1, dut.Ae, f, dead)
        b_psu = voltage_to_flux(limits.psu_max_V, dut.N1, dut.Ae, f, dead)

        # Current limit: the ripple is V/(2*f*L), so a current ceiling implies
        # a voltage ceiling and hence a flux ceiling.
        b_current = None
        if L and i_limit:
            v_i = 2.0 * f * L * i_limit
            b_current = voltage_to_flux(v_i, dut.N1, dut.Ae, f, dead)

        caps = {"PSU ceiling": b_psu, "core knee": b_knee}
        if b_current is not None:
            caps["current limit"] = b_current
        which = min(caps, key=lambda k: caps[k])
        b_max = caps[which]

        row.update({
            "B_min_T": b_min, "B_max_T": b_max, "B_max_limited_by": which,
            "V_at_B_min": limits.psu_min_V,
            "B_sat_T": b_sat, "B_knee_T": b_knee,
        })
        if b_max <= b_min:
            row["reachable"] = False
            row["notes"].append("no flux is reachable: the supply floor "
                                "already exceeds the ceiling")
        rows.append(row)
    return rows


def summary(dut, limits) -> Dict:
    """One-shot description of the bench envelope, for display."""
    sat = saturation_flux_T(dut.material, getattr(dut, "temperature_C", 25.0))
    rows = [r for r in envelope(dut, limits) if r["reachable"]]
    out = {
        "scope_f_ceiling_Hz": scope_frequency_ceiling_Hz(),
        "psu_V": (limits.psu_min_V, limits.psu_max_V),
        "saturation": sat,
        "usable_f_Hz": (min(r["frequency_Hz"] for r in rows),
                        max(r["frequency_Hz"] for r in rows)) if rows else None,
        "rows": rows,
    }
    return out


def bias_headroom_T(B_ac_T: float, dut, limits) -> Optional[float]:
    """How much DC flux can be added at this AC amplitude before saturation.

    Negative means the AC amplitude alone is already past B_sat. This is what
    makes a 200 mT / 100 mA point unmeasurable on a TX26/15/10-3C90: the AC
    peak leaves no room, so the bias drives the core over instead of shifting
    the operating point.
    """
    sat = saturation_flux_T(dut.material, getattr(dut, "temperature_C", 25.0))
    return KNEE_FRACTION * sat["B_sat_T"] - abs(B_ac_T)


def max_dc_bias_A(dut, limits, frequency, B_ac_T=None, mu_a=None) -> Dict:
    """Largest DC bias the core will hold at a given AC amplitude.

    The DC flux a bias adds is

        B_dc = mu0 * mu_a * N1 * I_dc / le

    so the ceiling is

        I_dc_max = (B_knee - B_ac) * le / (mu0 * mu_a * N1)

    Two things fall out of that, and they answer the questions people
    actually ask when swapping a DUT:

    * it does **not** depend on Ae — a fatter core does not buy more bias
      current, it buys more flux for the same volt-seconds;
    * it scales as **1/N1** — doubling the turns halves the bias current the
      core will take, because each amp produces twice the H.

    Checked against the bench: at 213 mT AC this predicts 101 mA, and the
    measured collapse sat between 52 mA (mu_a 3535, healthy) and 100 mA
    (mu_a 485, saturated).

    ``B_ac_T`` defaults to the *minimum* reachable AC amplitude at this
    frequency, i.e. the supply floor, which is where the bias headroom is
    largest.
    """
    from .analysis import MU0

    sat = saturation_flux_T(dut.material, getattr(dut, "temperature_C", 25.0))
    b_knee = KNEE_FRACTION * sat["B_sat_T"]
    dead = getattr(limits, "deadtime_s", 0.0)

    if B_ac_T is None:
        B_ac_T = voltage_to_flux(limits.psu_min_V, dut.N1, dut.Ae, frequency,
                                 dead)
    if mu_a is None:
        L = getattr(dut, "L_estimate_H", None)
        mu_a = (L * dut.le / (MU0 * dut.N1 ** 2 * dut.Ae)) if L else 3000.0

    headroom = b_knee - abs(B_ac_T)
    i_max = (headroom * dut.le / (MU0 * mu_a * dut.N1)) if headroom > 0 else 0.0
    return {
        "frequency_Hz": frequency,
        "B_ac_T": B_ac_T,
        "B_knee_T": b_knee,
        "headroom_T": headroom,
        "mu_a_assumed": mu_a,
        "I_dc_max_A": i_max,
        "H_dc_max_A_m": dut.N1 * i_max / dut.le if i_max else 0.0,
        "limited_by": ("core knee" if headroom > 0 else
                       "AC amplitude alone is already past the knee"),
    }


def describe(dut, limits) -> str:
    """Human-readable envelope, used by the CLI and the Studio."""
    s = summary(dut, limits)
    sat = s["saturation"]
    lines = [
        f"DUT {dut.shape} / {dut.material}, N1={dut.N1} "
        f"Ae={dut.Ae*1e6:.1f} mm^2 le={dut.le*1e3:.1f} mm",
        f"PSU {s['psu_V'][0]:.0f}-{s['psu_V'][1]:.0f} V   "
        f"scope ceiling {s['scope_f_ceiling_Hz']/1e3:.0f} kHz   "
        f"B_knee {KNEE_FRACTION*sat['B_sat_T']*1e3:.0f} mT "
        f"(B_sat {sat['B_sat_T']*1e3:.0f} mT @ "
        f"{sat['at_temperature_C']:.0f} C, {sat['source']})",
        "",
        f"{'f':>8s}  {'B min':>8s}  {'B max':>8s}   limited by",
    ]
    for r in envelope(dut, limits):
        if not r["reachable"]:
            lines.append(f"{r['frequency_Hz']/1e3:7.0f}k  {'--':>8s}  "
                         f"{'--':>8s}   {'; '.join(r['notes'])}")
            continue
        lines.append(
            f"{r['frequency_Hz']/1e3:7.0f}k  {r['B_min_T']*1e3:7.0f}mT  "
            f"{r['B_max_T']*1e3:7.0f}mT   {r['B_max_limited_by']}")
    return "\n".join(lines)
