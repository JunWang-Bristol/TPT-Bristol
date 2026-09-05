"""``opentpt`` — the command line that removes the assistant from the loop.

    opentpt validate recipe.json          plan every point, touch no hardware
    opentpt run      recipe.json          run it on the bench
    opentpt analyse  capture.csv -f 50e3  re-derive one capture offline
    opentpt replay   recipe.json          run against stored captures
    opentpt validate-dataset out.mas.json check a dataset against MAS
    opentpt accept   recipe.json -o dir  run twice, compare, accept or not
    opentpt where                        show resolved paths (packaging support)

``analyse`` matters more than it looks: because the reductions are pure, any
number in a dataset can be recomputed from its stored waveform with this
command, which is the practical form of the reproducibility claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import analysis as an
from . import events as ev
from .paths import resolve_input
from .recipe import RecipeError, load_recipe


def _cmd_validate(args):
    from .engine import Engine

    recipe = load_recipe(resolve_input(args.recipe))
    engine = Engine(recipe, bench=object(), dry_run=True)
    n, skipped = engine.validate()
    print(f"recipe '{recipe.name}': {len(recipe.procedures)} procedures "
          f"({len(recipe.ordered_procedures())} after safe ordering)")
    print(f"  DUT: {recipe.dut.shape} / {recipe.dut.material}  "
          f"N1:N2 = {recipe.dut.N1}:{recipe.dut.N2}  "
          f"Ae={recipe.dut.Ae*1e6:.1f} mm^2  le={recipe.dut.le*1e3:.1f} mm")
    print(f"  {n} points feasible, {len(skipped)} refused")
    for s in skipped:
        print(f"    refuse {s['tag']}: {s['reason']}")
    print("\n  execution order:")
    for k, p in enumerate(recipe.ordered_procedures(), 1):
        auto = "  (inserted)" if p.label == "auto demagnetize" else ""
        print(f"    {k}. {p.type}{auto}")
    return 0 if n else 1


def _cmd_run(args):
    from .engine import run_recipe

    result = run_recipe(resolve_input(args.recipe), output_dir=args.output,
                        dry_run=args.dry_run, verbose=not args.quiet,
                        emit_events=args.emit_events, resume=args.resume)
    return 1 if result.aborted else 0


def _cmd_replay(args):
    from .bench import ReplayBench
    from .engine import run_recipe

    captures = []
    for spec in args.capture:
        freq, _, path = spec.partition("=")
        if not path:
            raise SystemExit(f"--capture wants FREQ=path.csv, got {spec!r}")
        captures.append((float(freq), str(resolve_input(path))))
    bench = ReplayBench(captures)
    result = run_recipe(resolve_input(args.recipe), bench=bench,
                        output_dir=args.output,
                        verbose=not args.quiet, emit_events=args.emit_events,
                        resume=args.resume)
    return 1 if result.aborted else 0


def _cmd_analyse(args):
    import pandas as pd

    df = pd.read_csv(args.capture)
    a = an.analyse_cycle(
        df["time"].to_numpy(), df["V_pri"].to_numpy(),
        df["V_sec"].to_numpy(), df["Current"].to_numpy(),
        frequency=args.frequency, N1=args.n1, N2=args.n2,
        Ae=args.ae, le=args.le, Ve=args.ae * args.le,
    )
    if a is None:
        print("no closed cycle found in this capture", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(a.to_dict(with_loop=args.loop), indent=2, default=float))
    else:
        print(f"cycle       {a.i0}..{a.i1}  ({a.n_samples} samples, "
              f"{a.period_s*1e6:.2f} us -> {1/a.period_s/1e3:.1f} kHz)")
        print(f"imbalance   {a.imbalance_pct:.2f} %")
        print(f"B_peak      {a.B_peak_T*1e3:.2f} mT")
        print(f"H_peak      {a.H_peak_A_m:.1f} A/m   H_dc {a.H_dc_A_m:.1f} A/m")
        print(f"Q           {a.Q_cycle_J*1e6:.3f} uJ")
        print(f"Pv          {a.Pv_kW_m3:.1f} kW/m3")
        print(f"mu_a        {a.mu_secant:.0f}")
        print(f"mu' / mu''  {a.mu_prime:.0f} / {a.mu_second:.1f}   "
              f"tan d = {a.tan_delta:.4f}")
        print(f"  Pv from mu''  {a.Pv_from_mu_second_kW_m3:.1f} kW/m3 "
              f"(fundamental only; the gap to {a.Pv_kW_m3:.1f} is harmonic "
              f"content, not an error)")
        print(f"B_r / H_c   {a.remanence_T*1e3:.2f} mT / {a.coercivity_A_m:.1f} A/m "
              f"(dynamic, at {args.frequency/1e3:.0f} kHz)")
    return 0


def _cmd_validate_dataset(args):
    from . import mas_schema

    doc = json.loads(Path(args.document).read_text(encoding="utf-8"))
    report = mas_schema.validate(doc, schema=args.schema, peas_dir=args.peas)
    print(report.summary())
    for e in report.errors:
        print(f"  - {e}")
    if report.stubbed_refs and not args.peas:
        print()
        print("  Unchecked subtrees (MAS $refs the sibling PEAS spec, which "
              "ships separately):")
        for u in report.stubbed_refs:
            print(f"    {u}")
        print("  Pass --peas <dir> pointing at a PEAS checkout to check them.")
    return 0 if report.ok else 1


def _cmd_accept(args):
    from .accept import print_report, run_acceptance

    factory = None
    if args.capture:
        from .bench import ReplayBench
        caps = []
        for spec in args.capture:
            freq, _, path = spec.partition("=")
            caps.append((float(freq), path))
        factory = lambda: ReplayBench(caps)      # noqa: E731

    report, _ = run_acceptance(resolve_input(args.recipe), args.output,
                               bench_factory=factory,
                               verbose=not args.quiet)
    print_report(report)
    return 0 if report.ok else 1


def _cmd_where(args):
    """Report where the app is looking for things.

    Exists because "it cannot find the CSV" is otherwise unanswerable from a
    packaged build: the operator cannot see inside the bundle, and the
    resource root is a temp directory whose name changes every launch.
    """
    from . import paths

    print(f"frozen build     : {paths.frozen()}")
    print(f"resource root    : {paths.resource_root()}")
    print(f"  (bundled, read-only; deleted on exit in a frozen build)")
    print(f"data root        : {paths.data_root()}")
    print(f"  (your files and run output live here)")
    print(f"working directory: {Path.cwd()}")
    print(f"executable       : {sys.executable}")

    print()
    print("MAS schema:")
    from .mas_schema import SCHEMA_DIR, schema_version
    print(f"  {SCHEMA_DIR}  {'FOUND' if SCHEMA_DIR.is_dir() else 'MISSING'}"
          f"  @ {schema_version()}")

    print()
    print("OpenMagnetics catalogue:")
    from . import omdb
    print(f"  {omdb.describe()}")

    print()
    print("Sample captures (used by the Studio's Replay demo):")
    try:
        from opentpt_studio.resources import SAMPLE_CAPTURES, searched_locations
        names = [n for _, n in SAMPLE_CAPTURES]
    except Exception:                                       # noqa: BLE001
        names = ["coreloss_25kHz_200mT.csv", "coreloss_100kHz_100mT.csv"]
        searched_locations = None
    for n in names:
        found = paths.resolve_input(n)
        print(f"  {n:32s} {'FOUND  ' + str(found) if found.exists() else 'MISSING'}")
    if searched_locations:
        print("  searched:")
        for loc in searched_locations():
            print(f"    {loc}")

    print()
    print("Stock recipes:")
    seen = set()
    for d in (paths.data_root() / "recipes", paths.resource_root() / "recipes"):
        if d.is_dir() and d not in seen:
            seen.add(d)
            names = sorted(x.name for x in d.glob("*.json"))
            print(f"  {d}  ->  {', '.join(names) if names else '(none)'}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="opentpt",
                                description="Deterministic TPT sweep runner")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="plan a recipe without hardware")
    v.add_argument("recipe")
    v.set_defaults(func=_cmd_validate)

    r = sub.add_parser("run", help="run a recipe on the bench")
    r.add_argument("recipe")
    r.add_argument("-o", "--output", default=None)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--resume", action="store_true",
                   help="keep points the previous run in this output "
                        "directory already accepted, and measure only the rest")
    r.add_argument("-q", "--quiet", action="store_true")
    r.add_argument("--emit-events", action="store_true",
                   help="write the event stream as JSONL on stdout (used by "
                        "TPT Studio; suppresses the human log)")
    r.set_defaults(func=_cmd_run)

    rp = sub.add_parser("replay", help="run a recipe against stored captures")
    rp.add_argument("recipe")
    rp.add_argument("--capture", action="append", required=True,
                    metavar="FREQ=CSV", help="e.g. --capture 25e3=magnet_25kHz_200mT.csv")
    rp.add_argument("-o", "--output", default=None)
    rp.add_argument("--resume", action="store_true")
    rp.add_argument("-q", "--quiet", action="store_true")
    rp.add_argument("--emit-events", action="store_true",
                   help="write the event stream as JSONL on stdout")
    rp.set_defaults(func=_cmd_replay)

    a = sub.add_parser("analyse", help="re-derive one stored capture")
    a.add_argument("capture")
    a.add_argument("-f", "--frequency", type=float, required=True)
    a.add_argument("--n1", type=int, default=10)
    a.add_argument("--n2", type=int, default=10)
    a.add_argument("--ae", type=float, default=52.3e-6)
    a.add_argument("--le", type=float, default=63.5e-3)
    a.add_argument("--json", action="store_true")
    a.add_argument("--loop", action="store_true", help="include the B-H trace")
    a.set_defaults(func=_cmd_analyse)

    vd = sub.add_parser("validate-dataset",
                        help="check a produced .mas.json against the MAS schema")
    vd.add_argument("document")
    vd.add_argument("--schema", default=mas_schema_default())
    vd.add_argument("--peas", default=None,
                    help="path to a PEAS checkout, to validate the subtrees "
                         "MAS $refs from its sibling spec")
    vd.set_defaults(func=_cmd_validate_dataset)

    ac = sub.add_parser("accept",
                        help="run a recipe TWICE and compare - bench "
                             "repeatability acceptance")
    ac.add_argument("recipe")
    ac.add_argument("-o", "--output", required=True)
    ac.add_argument("--capture", action="append", metavar="FREQ=CSV",
                    help="use stored captures instead of hardware (for "
                         "exercising the harness itself)")
    ac.add_argument("-q", "--quiet", action="store_true")
    ac.set_defaults(func=_cmd_accept)

    w = sub.add_parser("where",
                       help="show where the app looks for schemas, recipes "
                            "and sample captures")
    w.set_defaults(func=_cmd_where)
    return p


def mas_schema_default():
    from .mas_schema import CORE_MATERIAL
    return CORE_MATERIAL


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RecipeError as exc:
        print(f"recipe error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception:                                       # noqa: BLE001
        # Nothing may escape here. In a windowed PyInstaller build an
        # unhandled exception raises a MODAL "Unhandled exception in script"
        # dialog that nobody can see or click - the process then blocks
        # forever and the Studio sits at "engine running..." indefinitely.
        # Reporting on stderr and exiting non-zero keeps the failure visible
        # through the channel the parent is already reading.
        import traceback
        traceback.print_exc()
        # Also emit it as an event on stdout. The Studio reads stdout for its
        # event stream and stderr for the log, but a frozen windowed child's
        # stderr does not reliably reach the parent — the operator then sees
        # "engine exited with code 70" over an empty log, with the one sentence
        # that explains the failure discarded. stdout is the channel already
        # proven to work, since every point result arrives on it.
        if getattr(args, "emit_events", False):
            print(json.dumps({"kind": "error",
                              "message": traceback.format_exc().strip()
                              .splitlines()[-1],
                              "traceback": traceback.format_exc()}),
                  flush=True)
        return 70


if __name__ == "__main__":
    sys.exit(main())
