"""Validate a produced document against the real MAS JSON schema.

The schema is vendored under ``schemas/mas/`` (Apache-2.0, from
github.com/OpenMagnetics/MAS) so validation works offline and a dataset can be
checked years later against the schema it was written for.  ``schemas/mas/
VERSION.txt`` records the upstream commit.

**MAS $refs a sibling spec, PEAS**, which is not distributed inside the MAS
repo — the project's own ``scripts/validate-samples.py`` builds its registry
from ``("schemas", "../PEAS/schemas")``, i.e. a checkout next to MAS.  Four
URI prefixes are affected (``peas/utils.json``,
``peas/inputs/operatingPointExcitation.json`` and two output schemas).

Without PEAS those subtrees cannot be checked.  Rather than fail outright or —
much worse — silently report "valid" for a document whose PEAS-shaped parts
were never looked at, this module registers permissive stubs for the missing
URIs and **reports exactly which ones were stubbed**.  Pass ``peas_dir`` (or
``--peas`` on the CLI) pointing at a PEAS checkout to get complete validation.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .paths import resource

SCHEMA_DIR = resource("schemas", "mas")
CORE_MATERIAL = "magnetic/core/material.json"


class SchemaUnavailable(RuntimeError):
    """The vendored schema or the validation libraries are missing."""


@dataclass
class ValidationReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    stubbed_refs: List[str] = field(default_factory=list)
    schema: str = CORE_MATERIAL
    schema_version: str = "unknown"

    @property
    def complete(self) -> bool:
        """True when nothing had to be stubbed — i.e. every subtree was checked."""
        return not self.stubbed_refs

    def summary(self) -> str:
        head = "valid" if self.ok else f"INVALID ({len(self.errors)} errors)"
        tail = ""
        if self.stubbed_refs:
            tail = (f"; {len(self.stubbed_refs)} PEAS subtree(s) NOT checked "
                    f"(no PEAS checkout)")
        return f"{head} against MAS {self.schema} @ {self.schema_version}{tail}"

    def to_dict(self):
        return {"ok": self.ok, "complete": self.complete,
                "schema": self.schema, "schemaVersion": self.schema_version,
                "errors": self.errors, "stubbedRefs": self.stubbed_refs}


def schema_version() -> str:
    v = SCHEMA_DIR / "VERSION.txt"
    if v.exists():
        for line in v.read_text(encoding="utf-8").splitlines():
            if line.startswith("commit:"):
                return line.split(":", 1)[1].strip()
    return "unknown"


def _collect(dirs: Sequence[Path]):
    """``{$id: schema}`` for every schema file under the given directories."""
    found: Dict[str, Any] = {}
    for d in dirs:
        if not d or not Path(d).is_dir():
            continue
        for f in sorted(glob.glob(os.path.join(str(d), "**", "*.json"),
                                  recursive=True)):
            try:
                with open(f, encoding="utf-8") as fh:
                    s = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(s, dict) and "$id" in s and s["$id"] not in found:
                found[s["$id"]] = s
    return found


def _absolute_refs(schemas: Dict[str, Any]) -> Dict[str, set]:
    """``{base URI: {fragment, ...}}`` for every absolute ``$ref``.

    The fragments matter: a stub for a missing schema has to contain the exact
    JSON pointers that reference it (``#/$defs/manufacturerInfo`` and friends),
    or resolution raises ``PointerToNowhere`` instead of passing permissively.
    """
    out: Dict[str, set] = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "$ref" and isinstance(v, str) and "://" in v:
                    base, _, frag = v.partition("#")
                    out.setdefault(base, set()).add(frag)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for s in schemas.values():
        walk(s)
    return out


def _stub_for(uri: str, fragments) -> dict:
    """A permissive schema carrying every pointer that references it."""
    stub: Dict[str, Any] = {
        "$id": uri,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": "opentpt stub: sibling spec unavailable, subtree unchecked",
    }
    for frag in fragments:
        parts = [p for p in frag.split("/") if p]
        if not parts:
            continue
        node = stub
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node.setdefault(parts[-1], {})     # {} == "anything goes"
    return stub


def build_registry(peas_dir=None):
    """Registry over the vendored MAS schemas plus, if given, a PEAS checkout.

    Returns ``(registry, stubbed)``.  Mirrors the loader in MAS's own
    ``scripts/validate-samples.py`` — resolution is by ``$id``, not by path.
    """
    try:
        from referencing import Registry, Resource
    except ImportError as exc:                             # noqa: BLE001
        raise SchemaUnavailable(
            "validation needs 'jsonschema' and 'referencing' — "
            "pip install jsonschema") from exc

    if not SCHEMA_DIR.is_dir():
        raise SchemaUnavailable(f"vendored MAS schema missing at {SCHEMA_DIR}")

    dirs = [SCHEMA_DIR]
    if peas_dir:
        dirs.append(Path(peas_dir))
    schemas = _collect(dirs)

    refs = _absolute_refs(schemas)
    missing = sorted(uri for uri in refs if uri not in schemas)
    resources = [(k, Resource.from_contents(v)) for k, v in schemas.items()]
    # A permissive stub keeps the validator running so the MAS-native parts of
    # the document are still checked properly.  The caller is told which URIs
    # were stubbed so "valid" is never mistaken for "fully validated".
    for uri in missing:
        resources.append(
            (uri, Resource.from_contents(_stub_for(uri, refs[uri]))))
    return Registry().with_resources(resources), missing


def validate(document: dict, *, schema: str = CORE_MATERIAL, peas_dir=None,
             max_errors: int = 40) -> ValidationReport:
    """Validate ``document`` against a MAS schema.  Never raises on invalid."""
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:                             # noqa: BLE001
        raise SchemaUnavailable(
            "validation needs 'jsonschema' — pip install jsonschema") from exc

    registry, stubbed = build_registry(peas_dir)
    schema_path = SCHEMA_DIR / schema
    if not schema_path.exists():
        raise SchemaUnavailable(f"no such MAS schema: {schema}")
    with open(schema_path, encoding="utf-8") as fh:
        schema_doc = json.load(fh)

    validator = Draft202012Validator(schema_doc, registry=registry)
    errors = []
    for err in sorted(validator.iter_errors(document), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{where}: {err.message}")
        if len(errors) >= max_errors:
            errors.append(f"... truncated at {max_errors} errors")
            break

    return ValidationReport(ok=not errors, errors=errors, stubbed_refs=stubbed,
                            schema=schema, schema_version=schema_version())
