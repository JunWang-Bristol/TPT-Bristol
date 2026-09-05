"""opentpt — the headless TPT measurement engine.

A run is a pure function of one recipe file: the engine walks the recipe's
procedures, drives the bench, gates every capture against the QC policy, and
emits an event stream plus a MAS (CoreDataX) dataset.  Nothing in this package
requires a GUI; TPT Studio is a renderer of the event stream, not a second
implementation of the measurement.

Layering
--------
    analysis.py     pure numpy reductions — no hardware, no I/O
    qc.py           gates over an analysed cycle
    recipe.py       the run definition (dataclasses + JSON)
    events.py       what the engine emits while running
    bench.py        instrument wrapper: startup checks, capture, auto-range
    procedures/     one module per procedure type
    engine.py       the state machine over the recipe
    mas.py          MAS coreMaterial / CoreDataX writer
    cli.py          `opentpt run recipe.json`

Only `bench` and `procedures` touch instruments.  Everything above `analysis`
is testable against the saved CSVs in the repo root.
"""

__version__ = "0.1.0"

from .analysis import (  # noqa: F401
    MU0,
    CycleAnalysis,
    analyse_cycle,
    complex_permeability,
    find_cycles,
    flux_density,
    inductance_from_ramp,
    last_full_cycle,
    magnetic_field,
    remanence_coercivity,
    volt_second_imbalance,
)
from .qc import QCPolicy, Verdict, evaluate  # noqa: F401
from .recipe import Recipe, RecipeError, load_recipe  # noqa: F401
