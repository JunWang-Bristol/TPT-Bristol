"""CoreDataX output — a schema-valid MAS ``coreMaterial`` carrying our points.

The document is **built on the OpenMagnetics database record** for the
material, not assembled from scratch. That is not a shortcut; it is the only
honest way to satisfy the schema. MAS requires ``resistivity``, ``saturation``
and ``manufacturerInfo``, none of which this bench can measure. Starting from
the catalogue record means those arrive with their real datasheet values, and
we overlay only the slots we actually measured.

What that overlay may and may not do:

* **Slots we measured are replaced, not merged.** ``permeabilityPoint`` has no
  ``origin`` field, so a datasheet point and a measured point sitting in the
  same array are indistinguishable to any reader. Replacing the slot wholesale
  keeps each array single-sourced, and the provenance sidecar records which
  slots became measurements.
* **Only gated points get in.** Rejects live in the journal and the points CSV,
  never in the document.
* **Nothing invented.** Fields we cannot measure keep their catalogue values;
  if the database is unavailable the exporter emits what it can and says the
  document is partial rather than filling gaps with guesses.

Provenance — the recipe, QC journal, bench checks and caveats — goes in a
**separate sidecar file**, because the MAS ``coreMaterial`` schema sets
``additionalProperties: false`` at the root and an ``_opentpt`` block would
make the document invalid.

Every write is validated against the vendored schema
(:mod:`opentpt.mas_schema`) and the result is reported, so an invalid dataset
is never produced silently.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__, omdb

# Cap on the exported B-H trace. MAS wants a curve, not a raw capture; the
# full-resolution waveform is already a sidecar.
BH_CYCLE_POINTS = 200


def _strip_nulls(o):
    """Drop null-valued keys.

    PyOpenMagnetics serialises absent optionals as ``null``, and a null does
    not satisfy a typed MAS slot — the database's own record fails validation
    with 16 errors until they are removed, and passes cleanly afterwards.
    """
    if isinstance(o, dict):
        return {k: _strip_nulls(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [_strip_nulls(v) for v in o]
    return o


def _dedup(points: List[dict]) -> List[dict]:
    """Stable dedup — several MAS arrays declare ``uniqueItems: true``."""
    seen, out = set(), []
    for p in points:
        key = json.dumps(p, sort_keys=True, default=float)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _decimate(seq, n=BH_CYCLE_POINTS):
    seq = list(seq)
    if len(seq) <= n:
        return seq
    step = (len(seq) - 1) / (n - 1)
    return [seq[int(round(k * step))] for k in range(n)]


def _perm_point(a, dut, *, value, biased=False, tolerance=None):
    """A ``permeabilityPoint``: only the keys the schema allows."""
    p = {"value": float(value),
         "temperature": dut.temperature_C,
         "frequency": float(a.frequency_Hz),
         "magneticFluxDensityPeak": float(a.B_peak_T)}
    if biased:
        p["magneticFieldDcBias"] = float(a.H_dc_A_m)
    if tolerance is not None:
        p["tolerance"] = float(tolerance)
    return p


def _bh_point(B, H, dut):
    return {"magneticFluxDensity": float(B),
            "magneticField": float(H),
            "temperature": dut.temperature_C}


def _skeleton(dut) -> dict:
    """Minimal base when the OpenMagnetics database is unavailable.

    Deliberately incomplete: the required datasheet fields are absent rather
    than fabricated, so validation fails loudly and the operator knows the
    document needs the database (or hand-supplied datasheet values).
    """
    return {
        "name": f"{dut.material} (OpenTPT measured)",
        "material": "ferrite",
        "type": "custom",
        "manufacturerInfo": {"name": "unknown"},
        "permeability": {},
        "volumetricLosses": {},
    }


def build_mas(recipe, result) -> dict:
    """Assemble the MAS ``coreMaterial`` document for one run."""
    dut = recipe.dut

    base = omdb.material_document(dut.material)
    doc = _strip_nulls(base) if base else _skeleton(dut)
    doc["name"] = f"{dut.material} - OpenTPT measured ({dut.catalogue_name})"
    doc["type"] = "custom"     # a measured record, not the vendor's catalogue entry

    amplitude: List[dict] = []
    incremental: List[dict] = []
    reversible: List[dict] = []
    mu_real: List[dict] = []
    mu_imag: List[dict] = []
    loss_points: List[dict] = []
    remanence: List[dict] = []
    coercive: List[dict] = []
    saturation: List[dict] = []
    initial: Optional[dict] = None
    best_loop = None
    knee_fired = False

    for pr in result.procedures:
        for point in pr.accepted:
            a = point.analysis
            if a is None:
                continue
            biased = bool(point.plan.get("dc_bias_A"))

            loss_points.append({
                "origin": recipe.export.origin,
                "value": float(a.P_core_W / dut.Ve),        # W/m^3, MAS base unit
                "temperature": dut.temperature_C,
                "magneticFluxDensity": {"processed": {
                    "label": "triangular",
                    "peak": float(a.B_peak_T),
                    "peakToPeak": float(2.0 * a.B_peak_T),
                    "offset": 0.0,
                    "dutyCycle": float(point.plan.get("duty", 0.5) or 0.5),
                    "frequency": float(a.frequency_Hz)}},
            })

            # mu_a falls out of every loop already captured.
            (incremental if biased else amplitude).append(
                _perm_point(a, dut, value=a.mu_secant, biased=biased))
            mu_real.append(_perm_point(a, dut, value=a.mu_prime, biased=biased))
            mu_imag.append(_perm_point(a, dut, value=a.mu_second, biased=biased))

            if not biased:
                # Only meaningful on an unbiased loop: the crossings of a
                # biased minor loop are not B_r and H_c.
                remanence.append(_bh_point(a.remanence_T, 0.0, dut))
                coercive.append(_bh_point(0.0, a.coercivity_A_m, dut))
                if a.B is not None and a.H is not None:
                    if best_loop is None or a.B_peak_T > best_loop[0]:
                        best_loop = (a.B_peak_T, a.H, a.B)

        d = pr.derived
        for row in d.get("reversible", []):
            reversible.append({
                "value": float(row["value"]),
                "temperature": row.get("temperature", dut.temperature_C),
                "frequency": float(row["frequency"]),
                "magneticFieldDcBias": float(row["magneticFieldDcBias"]),
                **({"tolerance": float(row["standardError"])}
                   if row.get("standardError") == row.get("standardError")
                   and row.get("standardError") is not None else {}),
            })
        if d.get("initial"):
            i = d["initial"]
            initial = {"value": float(i["value"]),
                       "temperature": i.get("temperature", dut.temperature_C)}
            if i.get("standardError") is not None:
                initial["tolerance"] = float(i["standardError"])
        for row in d.get("spectrum", []):
            common = {"temperature": row.get("temperature", dut.temperature_C),
                      "frequency": float(row["frequency"]),
                      "magneticFluxDensityPeak": float(
                          row["magneticFluxDensityPeak"])}
            mu_real.append({"value": float(row["real"]), **common})
            mu_imag.append({"value": float(row["imaginary"]), **common})
        for row in d.get("bh_envelope", []):
            saturation.append(_bh_point(row["B_peak_T"], row["H_peak_A_m"], dut))
        if any(p.extra.get("mu_drop_pct", 0) >= 50 for p in pr.accepted):
            knee_fired = True

    # ── overlay, replacing whole slots so each array is single-sourced ──────
    perm = doc.setdefault("permeability", {})
    if amplitude:
        perm["amplitude"] = _dedup(amplitude)
    if incremental:
        perm["incremental"] = _dedup(incremental)
    if reversible:
        perm["reversible"] = _dedup(reversible)
    if initial:
        perm["initial"] = initial
    if mu_real and mu_imag:
        perm["complex"] = {"real": _dedup(mu_real), "imaginary": _dedup(mu_imag)}

    if remanence:
        doc["remanence"] = _dedup(remanence)
    if coercive:
        doc["coerciveForce"] = _dedup(coercive)

    if best_loop is not None:
        _, H, B = best_loop
        cycle = [_bh_point(b, h, dut) for h, b in zip(H, B)]
        if len(cycle) >= 4:                      # schema minimum
            doc["bhCycle"] = _decimate(cycle)

    # The catalogue saturation curve is authoritative and required. Ours only
    # replaces it when the permeability-drop gate actually fired — otherwise
    # the envelope is a lower bound on B_sat, not a measurement of it, and it
    # stays in the sidecar.
    if saturation and knee_fired:
        doc["saturation"] = _dedup(saturation)

    losses = _dedup(loss_points)
    if len(losses) >= 2:                          # roshen requires minItems 2
        family = dut.family or "default"
        doc.setdefault("volumetricLosses", {}).setdefault(family, [])
        doc["volumetricLosses"][family] = [
            e for e in doc["volumetricLosses"][family]
            if e.get("method") != "roshen"
        ] + [{"method": "roshen", "referenceVolumetricLosses": losses}]

    return doc


def build_provenance(recipe, result, validation=None) -> dict:
    """Everything needed to re-derive the document — kept out of the document.

    The MAS root forbids extra properties, so this cannot live inside the
    dataset. It is written beside it instead, and it is what makes a dataset
    reproducible from its own header.
    """
    dut = recipe.dut
    prov = {
        "engineVersion": __version__,
        "recipe": recipe.to_dict(),
        "dut": dut.to_dict(),
        "geometrySource": dut.geometry_source,
        "catalogueName": dut.catalogue_name,
        "openMagneticsDatabase": omdb.describe(),
        "benchChecks": result.bench_checks,
        "startedAt": result.started_at,
        "finishedAt": result.finished_at,
        "aborted": result.aborted,
        "procedures": [
            {"type": pr.type, "label": pr.label,
             "accepted": len(pr.accepted), "rejected": len(pr.rejected),
             "skipped": len(pr.skipped), "derived": pr.derived}
            for pr in result.procedures
        ],
        "caveats": [
            "current_probe_scale is uncalibrated on this rig: every absolute "
            "watt figure scales linearly with it. Calibrate against a known "
            "resistor (V/I must equal R) before publishing absolutes.",
            "Ambient temperature only - no thermal control. Every point is "
            "recorded at the DUT's nominal ambient.",
            "remanence and coerciveForce are dynamic (AC loop crossings at the "
            "stated frequency), not DC catalogue values.",
            "Measured points replace whole permeability slots rather than "
            "merging with catalogue points, because permeabilityPoint carries "
            "no origin field and mixed arrays would be unattributable.",
            "Measured volumetric losses are carried in a 'roshen' method entry's "
            "referenceVolumetricLosses: that is the only array of "
            "volumetricLossesPoint the coreMaterial schema provides, so it is "
            "the conformant home for raw measured points, not a claim that a "
            "Roshen model was fitted.",
        ],
    }
    if validation is not None:
        prov["schemaValidation"] = validation.to_dict()
    return prov


_CSV_COLUMNS = [
    "procedure", "tag", "accepted", "attempts",
    "plan_frequency_Hz", "plan_B_target_T", "plan_dc_bias_A", "plan_duty",
    "plan_voltage_V", "frequency_Hz", "frequency_measured_Hz", "period_s",
    "n_samples", "B_peak_T", "H_peak_A_m", "I_dc_A", "H_dc_A_m",
    "Q_cycle_J", "P_core_W", "Pv_kW_m3",
    "mu_secant", "mu_prime", "mu_second", "tan_delta",
    "Pv_from_mu_second_kW_m3", "remanence_T", "coercivity_A_m",
    "imbalance_pct", "i_pp", "v_pri_pp", "sense_polarity_inverted",
    "turns_ratio_measured",
    # Primary-side (single-winding) reading of the SAME cycle. Their
    # difference from the secondary-derived core loss is a MEASURED winding
    # loss, and B from the primary flux linkage is an independent check on B
    # from the sense winding — the only cross-check this rig has that does not
    # go through the sense winding at all. The writer uses
    # extrasaction="ignore", so a quantity missing from this list is computed
    # and then silently dropped; that is how these went unrecorded at first.
    "P_ac_primary_W", "Pv_primary_kW_m3", "P_winding_measured_W",
    "B_peak_primary_T", "i_rms_A",
    "waveform", "reject_reason",
]


def write_points_csv(result, path):
    """One row per attempt, rejects included and labelled.

    The MAS document carries only accepted points; this file carries
    everything, which is what makes a gap in the dataset explainable without
    reading the journal line by line.
    """
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for pr in result.procedures:
            for point in pr.points:
                row = point.to_dict()
                if not point.accepted and point.verdict is not None:
                    row["reject_reason"] = point.verdict.reason
                w.writerow(row)


def write_dataset(recipe, result, out_dir, *, validate=True, peas_dir=None):
    """Write the dataset. Returns ``(paths, validation_report_or_None)``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    doc = build_mas(recipe, result)

    report = None
    if validate:
        try:
            from . import mas_schema
            report = mas_schema.validate(doc, peas_dir=peas_dir)
        except Exception as exc:                           # noqa: BLE001
            report = None
            print(f"  schema validation unavailable: {exc}")

    written = []
    mas_path = out / f"{recipe.name}.mas.json"
    mas_path.write_text(json.dumps(doc, indent=2, default=float),
                        encoding="utf-8")
    written.append(str(mas_path))

    prov_path = out / f"{recipe.name}.provenance.json"
    prov_path.write_text(
        json.dumps(build_provenance(recipe, result, report), indent=2,
                   default=float), encoding="utf-8")
    written.append(str(prov_path))

    csv_path = out / f"{recipe.name}_points.csv"
    write_points_csv(result, csv_path)
    written.append(str(csv_path))

    recipe_path = out / "recipe.used.json"
    recipe_path.write_text(json.dumps(recipe.to_dict(), indent=2),
                           encoding="utf-8")
    written.append(str(recipe_path))
    return written, report
