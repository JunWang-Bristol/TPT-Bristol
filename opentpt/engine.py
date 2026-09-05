"""The engine: walk the recipe, gate every capture, emit the stream.

This is the whole determinism contract in one place.  Given a recipe and a
bench it runs the procedures in safe order, and the only things it decides for
itself are ones a rerun would decide identically: which points are infeasible,
which captures pass the gates, when to retry.

Two invariants it will not break:

* **Rails off on every exit path**, including an abort or a crash inside a
  procedure.  A run that dies with the bridge energised is a hardware risk.
* **A fatal bench check stops the run before the first point.**  Past that
  point every symptom looks like a measurement problem, and this rig has cost
  entire sessions to exactly that ambiguity — a dead 12 V gate-driver rail is
  indistinguishable from a firmware bug once a sweep is running.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from . import events as ev
from . import procedures as procs
from .bench import HardwareBench
from .recipe import Recipe, load_recipe
from .resume import ResumeRefused, load_prior, recipe_fingerprint
from .results import ProcedureResult, RunResult


class RunAborted(RuntimeError):
    pass


class Engine:
    """Runs one recipe against one bench."""

    def __init__(self, recipe: Recipe, bench=None, bus=None, output_dir=None,
                 dry_run=False, resume=False):
        self.recipe = recipe
        self.bench = bench
        self.bus = bus or ev.EventBus()
        self.dry_run = dry_run
        self.resume = resume
        self.output_dir = Path(output_dir or Path(recipe.export.directory) /
                               recipe.name)
        self._owns_bench = bench is None

    # ── validation ───────────────────────────────────────────────────────────

    def validate(self):
        """Plan every point without touching hardware.

        Returns ``(n_feasible, skipped)``.  This is what makes "Validate" and
        "Dry run" honest buttons rather than optimistic ones: the same planner
        the run uses decides here, so a point that validates will be attempted
        and a point that is refused here would have been refused there.
        """
        from .drive import plan_point

        def as_list(value, default):
            if value is None:
                return list(default)
            return [float(v) for v in (value if isinstance(value, (list, tuple))
                                       else [value])]

        feasible, skipped = 0, []
        for proc in self.recipe.ordered_procedures():
            if proc.type == "demagnetize":
                continue
            p = proc.params
            freqs = as_list(p.get("frequency_Hz"), [50e3])
            fluxes = as_list(p.get("B_peak_T", p.get("B_ac_T")), [0.05])
            biases = as_list(p.get("dc_bias_A"), [0.0])
            # The duty axis has to be walked here too. It was not, so a recipe
            # using duty had its point count understated — a 10-point sweep
            # validated as 2 — and now that duty moves the negative rail and can
            # therefore push a point past the supply ceiling, validate could
            # report "0 refused" on a recipe the run then skipped half of. A
            # validation that disagrees with the run is worse than none.
            duties = as_list(p.get("duty"), [0.5])
            # Ask the planner the same question the run will: bias_method can
            # itself make a point infeasible, so validate has to pass it too.
            opts = {k: p[k] for k in ("bias_method", "bias_cycles", "bias_lead",
                                      "n_pulses") if k in p}
            for f in freqs:
                for b in fluxes:
                    for i in biases:
                        for d in duties:
                            plan = plan_point(self.recipe.dut,
                                              self.recipe.limits, frequency=f,
                                              B_target_T=b, dc_bias_A=i, duty=d,
                                              **opts)
                            if plan.feasible:
                                feasible += 1
                            else:
                                skipped.append({"procedure": proc.type,
                                                "tag": plan.tag,
                                                "reason": plan.reason})
        return feasible, skipped

    # ── run ──────────────────────────────────────────────────────────────────

    def run(self) -> RunResult:
        recipe = self.recipe
        result = RunResult(recipe=recipe.to_dict(), started_at=time.time())
        ordered = recipe.ordered_procedures()

        self.bus.emit(ev.RUN_STARTED, recipe=recipe.name,
                      n_procedures=len(ordered),
                      dut=recipe.dut.to_dict(),
                      output_dir=str(self.output_dir),
                      # The whole recipe, so a later resume can prove it is
                      # continuing the same run and not stitching two together.
                      recipe_document=recipe.to_dict(),
                      fingerprint=recipe_fingerprint(recipe))

        if self.dry_run:
            n, skipped = self.validate()
            self.bus.log(f"dry run: {n} points feasible, {len(skipped)} refused")
            for s in skipped:
                self.bus.emit(ev.POINT_SKIPPED, **s)
            result.finished_at = time.time()
            self.bus.emit(ev.RUN_FINISHED, accepted=0, duration_s=0.0,
                          dry_run=True)
            return result

        if self.bench is None:
            self.bench = HardwareBench(recipe.hardware_configuration, bus=self.bus)

        try:
            checks = self.bench.startup_checks(recipe.limits)
            result.bench_checks = checks
            fatal = [c for c in checks if c.get("fatal") and not c.get("ok")]
            if fatal:
                reasons = "; ".join(f"{c['check']}: {c['detail']}" for c in fatal)
                result.aborted = f"bench check failed — {reasons}"
                self.bus.warn(result.aborted)
                raise RunAborted(result.aborted)

            prior = None
            if self.resume:
                prior = load_prior(self.output_dir, recipe)
                if len(prior):
                    self.bus.log(f"resuming: {len(prior)} accepted point(s) "
                                 f"restored from the previous journal")
                else:
                    self.bus.log("resume requested but the previous journal "
                                 "holds no accepted points - running in full")

            state = {}
            for k, proc in enumerate(ordered, start=1):
                ctx = procs.Context(
                    dut=recipe.dut, limits=recipe.limits, qc=recipe.qc,
                    bench=self.bench, bus=self.bus, params=proc.params,
                    waveform_dir=self.output_dir / "waveforms",
                    save_waveforms=recipe.export.save_waveforms,
                    state=state, index=k, total=len(ordered),
                    prior=prior,
                )
                self.bus.log(f"procedure {k}/{len(ordered)}: {proc.type}")
                try:
                    pr = procs.get(proc.type)(ctx)
                except Exception as exc:                       # noqa: BLE001
                    self.bus.warn(f"procedure {proc.type} failed: {exc!r}")
                    pr = ProcedureResult(proc.type, proc.label)
                    pr.derived["error"] = repr(exc)
                result.procedures.append(pr)

        except ResumeRefused as exc:
            result.aborted = str(exc)
            self.bus.warn(result.aborted)
        except RunAborted:
            pass
        except KeyboardInterrupt:
            result.aborted = "interrupted by the operator"
            self.bus.warn(result.aborted)
        finally:
            try:
                self.bench.rails_off()
            except Exception:                                  # noqa: BLE001
                pass
            if self._owns_bench and self.bench is not None:
                try:
                    self.bench.close()
                except Exception:                              # noqa: BLE001
                    pass

        result.finished_at = time.time()
        self.bus.emit(ev.RUN_FINISHED,
                      accepted=len(result.accepted_points),
                      duration_s=result.finished_at - result.started_at,
                      aborted=result.aborted)
        return result


def run_recipe(path, *, bench=None, output_dir=None, dry_run=False,
               journal=True, verbose=True, emit_events=False,
               resume=False) -> RunResult:
    """Load a recipe, run it, and write the dataset next to its journal."""
    from .mas import write_dataset

    recipe = load_recipe(path)
    out = Path(output_dir or Path(recipe.export.directory) / recipe.name)
    out.mkdir(parents=True, exist_ok=True)

    bus = ev.EventBus(str(out / "journal.jsonl") if journal else None)
    if emit_events:
        # Machine-readable mode: stdout carries the event stream and nothing
        # else, so a parent process can consume it line by line.  The human
        # printer is suppressed rather than interleaved.
        bus.subscribe(ev.jsonl_writer())
    else:
        bus.subscribe(ev.console_printer(verbose=verbose))

    engine = Engine(recipe, bench=bench, bus=bus, output_dir=out,
                    dry_run=dry_run, resume=resume)
    try:
        result = engine.run()
        if not dry_run:
            paths, report = write_dataset(recipe, result, out)
            summary = report.summary() if report else "schema not checked"
            if emit_events:
                bus.emit("dataset_written", paths=paths,
                         validation=report.to_dict() if report else None,
                         summary=summary)
            else:
                for p in paths:
                    print(f"  wrote {p}")
                print(f"  MAS schema: {summary}")
                if report and not report.ok:
                    for e in report.errors[:10]:
                        print(f"    - {e}")
        return result
    finally:
        bus.close()
