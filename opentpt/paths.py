"""Where things live, from source and from a packaged build.

Two roots, and conflating them is the classic packaging bug:

* :func:`resource_root` — read-only data shipped *inside* the application:
  the vendored MAS schema, the stock recipes. In a PyInstaller build these are
  unpacked to a temporary directory (``sys._MEIPASS``) that is deleted on
  exit, so nothing may ever be written there.
* :func:`data_root` — the operator's working directory: their
  ``hardware_configuration.json``, their recipes, their datasets. This must
  stay next to the executable (or wherever they launched it), never inside the
  bundle, or a run's output would vanish when the app closes.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Root of the read-only data shipped with the application."""
    if frozen():
        # PyInstaller sets _MEIPASS to the unpacked bundle; onedir builds fall
        # back to the directory holding the executable.
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _PACKAGE_ROOT


def data_root() -> Path:
    """Root for the operator's own files — config, recipes, datasets."""
    if frozen():
        return Path.cwd()
    return _PACKAGE_ROOT


def resource(*parts) -> Path:
    return resource_root().joinpath(*parts)


def resolve_input(path) -> Path:
    """Resolve a user-supplied path, falling back to bundled copies.

    A frozen app is launched from wherever the operator happens to be, so
    ``recipes/tx26_3c90.recipe.json`` will usually not exist relative to the
    working directory even though it ships inside the bundle. Try the literal
    path first — an explicit path must always win — then the same name under
    the resource root.
    """
    p = Path(path)
    if p.exists() or p.is_absolute():
        return p
    for root in (data_root(), resource_root()):
        candidate = root / p
        if candidate.exists():
            return candidate
    return p          # let the caller raise a normal "no such file"
