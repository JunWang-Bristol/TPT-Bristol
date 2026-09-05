"""The recipe — a run is a pure function of this one file.

Same recipe + same bench = same dataset.  Everything the engine may decide is
either fixed here or derived deterministically from a capture; nothing is
improvised at run time.  In particular ``limits`` is a refusal boundary, not a
target: a point that needs more than the bench can deliver is *skipped at
validation with a reason*, never quietly clamped into a wrong answer.

The procedure list is ordered by the author, but the engine may only reorder
it in one direction — see :func:`Recipe.ordered_procedures`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import omdb
from .qc import QCPolicy


class RecipeError(ValueError):
    """A recipe that cannot be run as written."""


# ─── Core geometry ────────────────────────────────────────────────────────────

# Kept only as the fallback for an offline bench and as the shape list the
# Studio offers.  The authoritative source is the OpenMagnetics database via
# :mod:`opentpt.omdb`; these numbers are known to be wrong for at least
# E32/6/20 (83 mm2 / 121 mm here, 128.6 mm2 / 41.8 mm in the catalogue).
CORE_GEOMETRY: Dict[str, Dict[str, float]] = omdb.FALLBACK_GEOMETRY

MATERIAL_PROPERTIES: Dict[str, Dict[str, float]] = {
    "3C90": {"mu_r": 2300, "B_sat": 0.35},
    "3F3":  {"mu_r": 2000, "B_sat": 0.35},
    "N87":  {"mu_r": 2200, "B_sat": 0.39},
    "N97":  {"mu_r": 2300, "B_sat": 0.40},
}


@dataclass
class DUT:
    """The device under test: geometry, material and windings."""

    shape: str
    material: str
    N1: int
    N2: int
    Ae: Optional[float] = None       # m² — overrides the catalogue
    le: Optional[float] = None       # m
    Ve: Optional[float] = None       # m³ — defaults to the catalogue value
    L_estimate_H: Optional[float] = None
    temperature_C: float = 25.0      # this rig is ambient-only; recorded honestly
    geometry_source: Optional[str] = None    # filled in, not authored
    catalogue_name: Optional[str] = None     # ditto
    family: Optional[str] = None             # ditto

    def __post_init__(self):
        # Geometry is looked up, never guessed: Ae scales B and Ve scales Pv,
        # so a wrong number here silently rescales the whole dataset.  An
        # explicit Ae/le in the recipe still wins — that is the escape hatch
        # for a core the catalogue does not carry — but it is recorded as
        # such so a reader can tell measured-on-catalogue-geometry from
        # measured-on-someone's-estimate.
        if self.Ae is None or self.le is None:
            geo = omdb.resolve_geometry(self.shape)
            if geo is None:
                raise RecipeError(
                    f"unknown core shape {self.shape!r}: not in the "
                    f"OpenMagnetics database and not in the fallback table. "
                    f"Give Ae and le explicitly in the recipe.")
            self.Ae = self.Ae if self.Ae is not None else geo["Ae"]
            self.le = self.le if self.le is not None else geo["le"]
            if self.Ve is None:
                self.Ve = geo["Ve"]
            self.geometry_source = geo["source"]
            self.catalogue_name = geo["resolved_name"]
            self.family = geo["family"]
        else:
            self.geometry_source = "explicit in recipe"
            self.catalogue_name = self.shape
        if self.Ve is None:
            self.Ve = self.Ae * self.le
        if self.N1 <= 0 or self.N2 <= 0:
            raise RecipeError("N1 and N2 must be positive")

    @property
    def B_sat(self):
        mat = MATERIAL_PROPERTIES.get(self.material)
        return mat["B_sat"] if mat else None

    def to_dict(self):
        return asdict(self)


# ─── Procedures ───────────────────────────────────────────────────────────────

@dataclass
class Procedure:
    """One block of the run.  ``params`` is validated by the executor."""

    type: str
    params: Dict[str, Any] = field(default_factory=dict)
    label: Optional[str] = None

    def to_dict(self):
        d = {"type": self.type, **self.params}
        if self.label:
            d["label"] = self.label
        return d


# Ordering key: low-stress procedures run first so a saturation event cannot
# contaminate the loss map that follows it.  Demagnetisation is a separator,
# not a stage, so it keeps whatever position the author gave it.
_STRESS_ORDER = {
    "core_loss_map":        10,
    "permeability_set":     20,
    "complex_mu_spectrum":  30,
    "inductance_vs_bias":   40,
    "saturation_curve":     50,
}

KNOWN_PROCEDURES = set(_STRESS_ORDER) | {"demagnetize"}


@dataclass
class Limits:
    """Bench envelope.  The engine refuses points outside it; it never improvises."""

    psu_max_V: float = 30.0
    # Supplies can have a floor as well as a ceiling. One that refuses
    # setpoints below some voltage keeps its previous rail instead, so a point
    # planned under that floor would measure at the wrong voltage and still
    # look valid. The BK9129B on this bench regulates down to 0 V, hence the
    # default; the field exists so a supply that does have a floor can declare
    # it and have the planner refuse those points up front.
    psu_min_V: float = 0.0
    i_limit_A: float = 2.0
    f_min_Hz: float = 1e3
    f_max_Hz: float = 200e3
    deadtime_s: float = 500e-9

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise RecipeError(f"unknown limits keys: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self):
        return asdict(self)


@dataclass
class ExportSpec:
    format: str = "MAS coreMaterial + outputs"
    origin: str = "measurement"
    directory: str = "datasets"
    save_waveforms: bool = True

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise RecipeError(f"unknown export keys: {sorted(unknown)}")
        return cls(**d)

    def to_dict(self):
        return asdict(self)


@dataclass
class Recipe:
    """A complete, runnable definition of one dataset."""

    dut: DUT
    procedures: List[Procedure]
    qc: QCPolicy = field(default_factory=QCPolicy)
    limits: Limits = field(default_factory=Limits)
    export: ExportSpec = field(default_factory=ExportSpec)
    name: str = "untitled"
    hardware_configuration: str = "hardware_configuration.json"
    source_path: Optional[str] = None

    # ── construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, d: Dict[str, Any], *, source_path=None) -> "Recipe":
        if "dut" not in d:
            raise RecipeError("recipe has no 'dut' section")
        if not d.get("procedures"):
            raise RecipeError("recipe has no procedures — nothing to run")

        dut_d = dict(d["dut"])
        unknown = set(dut_d) - set(DUT.__dataclass_fields__)
        if unknown:
            raise RecipeError(f"unknown dut keys: {sorted(unknown)}")
        dut = DUT(**dut_d)

        procedures = []
        for k, p in enumerate(d["procedures"]):
            if "type" not in p:
                raise RecipeError(f"procedure {k} has no 'type'")
            ptype = p["type"]
            if ptype not in KNOWN_PROCEDURES:
                raise RecipeError(
                    f"procedure {k}: unknown type {ptype!r}; "
                    f"known types are {sorted(KNOWN_PROCEDURES)}")
            params = {kk: vv for kk, vv in p.items() if kk not in ("type", "label")}
            procedures.append(Procedure(ptype, params, p.get("label")))

        return cls(
            dut=dut,
            procedures=procedures,
            qc=QCPolicy.from_dict(d.get("qc")),
            limits=Limits.from_dict(d.get("limits")),
            export=ExportSpec.from_dict(d.get("export")),
            name=d.get("name", "untitled"),
            hardware_configuration=d.get(
                "hardware_configuration", "hardware_configuration.json"),
            source_path=source_path,
        )

    def to_dict(self):
        return {
            "name": self.name,
            "hardware_configuration": self.hardware_configuration,
            "dut": self.dut.to_dict(),
            "procedures": [p.to_dict() for p in self.procedures],
            "qc": self.qc.to_dict(),
            "limits": self.limits.to_dict(),
            "export": self.export.to_dict(),
        }

    # ── ordering ─────────────────────────────────────────────────────────────

    def ordered_procedures(self) -> List[Procedure]:
        """Procedures in safe execution order, with demagnetisation inserted.

        Sort is *stable* by stress level, so procedures of equal stress keep
        the author's order.  A ``demagnetize`` block written by the author is a
        fence: nothing is reordered across it.
        """
        out: List[Procedure] = []
        block: List[Procedure] = []
        for p in self.procedures:
            if p.type == "demagnetize":
                out.extend(sorted(block, key=lambda q: _STRESS_ORDER[q.type]))
                block = []
                out.append(p)
            else:
                block.append(p)
        out.extend(sorted(block, key=lambda q: _STRESS_ORDER[q.type]))

        # Any procedure that can drive the core into saturation must be
        # followed by a reset before the next measurement sees the core.
        result: List[Procedure] = []
        for k, p in enumerate(out):
            result.append(p)
            saturating = p.type in ("saturation_curve", "inductance_vs_bias")
            more = k + 1 < len(out)
            next_is_demag = more and out[k + 1].type == "demagnetize"
            if saturating and more and not next_is_demag:
                result.append(Procedure(
                    "demagnetize",
                    {"auto_after_saturation": True, "verify_reset": True},
                    label="auto demagnetize"))
        return result


def load_recipe(path) -> Recipe:
    """Read and validate a recipe file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RecipeError(f"no such recipe: {path}")
    except json.JSONDecodeError as exc:
        raise RecipeError(f"{path}: invalid JSON — {exc}")
    return Recipe.from_dict(data, source_path=str(path))
