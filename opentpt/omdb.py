"""Optional bridge to the OpenMagnetics database (``PyOpenMagnetics``).

Two things come from here that must never be typed by hand:

* **Core geometry.** ``Ae``, ``le`` and ``Ve`` scale every result — B is
  ``∫V dt/(N2·Ae)`` and Pv is ``P/Ve`` — so a guessed geometry silently
  rescales an entire dataset.  The hardcoded table this package shipped with
  had ``T26/15/10`` at 52.3 mm² / 63.5 mm; the database says **55.0 mm² /
  64.40 mm**, which is 5.2 % in B and 6.7 % in Pv.
* **The datasheet fields MAS requires but this bench cannot measure** —
  ``resistivity``, ``curieTemperature``, ``density``, the catalogue
  ``saturation`` curve, ``manufacturerInfo``.  Rather than invent them or omit
  them and emit an invalid document, the exporter starts from the database's
  own ``coreMaterial`` record and overlays what we measured.

The dependency is **optional**.  The engine runs, gates, and reduces exactly
the same without it; only the geometry source and the completeness of the MAS
document change.  A bench PC missing the package gets the fallback table and a
clearly-labelled partial document rather than an import error mid-sweep.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, Optional

# Fallback effective parameters, used only when the database is unavailable.
# These are the values this project used before the database was wired in;
# they are kept so nothing breaks offline, but they are NOT authoritative and
# resolve_geometry() says so in the source it reports.
FALLBACK_GEOMETRY: Dict[str, Dict[str, float]] = {
    "T18": {"Ae": 10.8e-6, "le": 26.7e-3},
    "T26": {"Ae": 52.3e-6, "le": 63.5e-3},
    "TX26/15/10": {"Ae": 52.3e-6, "le": 63.5e-3},
    "E32": {"Ae": 83.0e-6, "le": 121.0e-3},
    "E32/6/20": {"Ae": 83.0e-6, "le": 121.0e-3},
    "E42": {"Ae": 178.0e-6, "le": 97.0e-3},
    "E42/21/20": {"Ae": 178.0e-6, "le": 97.0e-3},
}


@functools.lru_cache(maxsize=1)
def _om():
    try:
        import PyOpenMagnetics as om
    except Exception:                                      # noqa: BLE001
        return None
    return om


def available() -> bool:
    return _om() is not None


def _name_candidates(name: str):
    """Spellings of a shape name to try against the database.

    Catalogue names drift between vendors and lab notebooks: a toroid written
    ``TX26/15/10`` in this project is ``T 26/15/10`` in the database, and the
    lookup is exact.  Rather than make the user hunt for the canonical string,
    try the obvious transliterations and report which one matched.
    """
    seen, out = set(), []

    def add(c):
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)

    add(name)
    # TX26/15/10 -> T26/15/10 -> T 26/15/10
    letters = "".join(ch for ch in name if ch.isalpha())
    rest = name[len(letters):] if name[:len(letters)] == letters else None
    if rest is not None and letters:
        add(letters + rest)
        add(letters + " " + rest.lstrip())
        if len(letters) > 1:                # TX -> T, RM -> R ... only the head
            add(letters[0] + rest)
            add(letters[0] + " " + rest.lstrip())
    add(name.replace(" ", ""))
    return out


@functools.lru_cache(maxsize=64)
def resolve_geometry(shape: str) -> Optional[Dict[str, Any]]:
    """Effective ``Ae``/``le``/``Ve`` for a core shape.

    Returns a dict with the parameters plus ``source`` and ``resolved_name``,
    or ``None`` when neither the database nor the fallback table knows the
    shape — in which case the recipe must state the geometry explicitly.
    """
    om = _om()
    if om is not None:
        for candidate in _name_candidates(shape):
            try:
                shape_doc = om.find_core_shape_by_name(candidate)
            except Exception:                              # noqa: BLE001
                continue
            try:
                core = om.calculate_core_data({"functionalDescription": {
                    "shape": shape_doc["name"], "material": "3C90",
                    "type": "toroidal" if shape_doc.get("family") == "t"
                            else "two-piece set",
                    "gapping": [], "numberStacks": 1}}, False)
                eff = core["processedDescription"]["effectiveParameters"]
            except Exception:                              # noqa: BLE001
                continue
            return {
                "Ae": float(eff["effectiveArea"]),
                "le": float(eff["effectiveLength"]),
                "Ve": float(eff["effectiveVolume"]),
                "family": shape_doc.get("family"),
                "resolved_name": shape_doc["name"],
                "source": "OpenMagnetics database",
            }

    geo = FALLBACK_GEOMETRY.get(shape)
    if geo is None:
        return None
    return {
        "Ae": geo["Ae"], "le": geo["le"], "Ve": geo["Ae"] * geo["le"],
        "family": None, "resolved_name": shape,
        "source": "built-in fallback table (NOT authoritative - install "
                  "PyOpenMagnetics for catalogue geometry)",
    }


@functools.lru_cache(maxsize=64)
def material_document(material: str) -> Optional[Dict[str, Any]]:
    """The database's ``coreMaterial`` record, or ``None``.

    Used as the base of the exported MAS document: it already carries the
    datasheet-sourced fields the schema requires and the bench cannot measure.
    A copy is returned so callers cannot corrupt the cache.
    """
    om = _om()
    if om is None:
        return None
    try:
        import copy
        return copy.deepcopy(om.find_core_material_by_name(material))
    except Exception:                                      # noqa: BLE001
        return None


def describe() -> str:
    """One line for the log, so a dataset's geometry source is never a guess."""
    if not available():
        return ("PyOpenMagnetics not installed - using the built-in geometry "
                "table and emitting a partial MAS document")
    return "OpenMagnetics database available - catalogue geometry and material data"
