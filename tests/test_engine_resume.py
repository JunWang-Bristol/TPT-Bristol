"""Resume-after-abort and the twice-over acceptance harness.

Both are about run-level reproducibility rather than about any single
measurement, so both are tested by driving whole runs against stored captures.
"""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from opentpt import events as ev                          # noqa: E402
from opentpt.accept import compare                        # noqa: E402
from opentpt.bench import ReplayBench                     # noqa: E402
from opentpt.engine import Engine                         # noqa: E402
from opentpt.recipe import Recipe                         # noqa: E402
from opentpt.resume import (                              # noqa: E402
    ResumeRefused, load_prior, recipe_fingerprint,
)

CAPTURES = [(25e3, os.path.join(_ROOT, "coreloss_25kHz_200mT.csv")),
            (100e3, os.path.join(_ROOT, "coreloss_100kHz_100mT.csv"))]

RECIPE = {
    "name": "resume_test",
    "dut": {"shape": "TX26/15/10", "material": "3C90", "N1": 10, "N2": 10,
            "L_estimate_H": 404e-6},
    "procedures": [{"type": "core_loss_map",
                    "frequency_Hz": [25e3, 100e3],
                    "B_peak_T": [0.1, 0.2]}],
}


def _have():
    return all(os.path.exists(p) for _, p in CAPTURES)


def _run(out, recipe_dict, *, resume=False):
    """One engine run against the stored captures, journalling into `out`."""
    recipe = Recipe.from_dict(copy.deepcopy(recipe_dict))
    bus = ev.EventBus(os.path.join(out, "journal.jsonl"))
    bench = ReplayBench(CAPTURES, bus=bus)
    result = Engine(recipe, bench=bench, bus=bus, output_dir=out,
                    resume=resume).run()
    bus.close()
    return result, bench, bus


class TestFingerprint(unittest.TestCase):

    def test_same_recipe_same_fingerprint(self):
        a = recipe_fingerprint(Recipe.from_dict(copy.deepcopy(RECIPE)))
        b = recipe_fingerprint(Recipe.from_dict(copy.deepcopy(RECIPE)))
        self.assertEqual(a, b)

    def test_changing_what_a_point_means_changes_the_fingerprint(self):
        base = recipe_fingerprint(Recipe.from_dict(copy.deepcopy(RECIPE)))
        for section, patch in (
            ("limits", {"limits": {"psu_max_V": 45.0}}),
            ("qc", {"qc": {"max_imbalance_pct": 9.0}}),
            ("dut", {"dut": {**RECIPE["dut"], "N1": 12}}),
            ("procedures", {"procedures": [{"type": "core_loss_map",
                                            "frequency_Hz": [25e3],
                                            "B_peak_T": [0.1]}]}),
        ):
            with self.subTest(section=section):
                d = copy.deepcopy(RECIPE)
                d.update(patch)
                self.assertNotEqual(recipe_fingerprint(Recipe.from_dict(d)),
                                    base, f"{section} did not affect it")

    def test_cosmetic_changes_do_not(self):
        # Where the dataset is written does not change what a point means.
        d = copy.deepcopy(RECIPE)
        d["export"] = {"directory": "somewhere/else"}
        self.assertEqual(recipe_fingerprint(Recipe.from_dict(d)),
                         recipe_fingerprint(Recipe.from_dict(
                             copy.deepcopy(RECIPE))))


class TestResume(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not _have():
            raise unittest.SkipTest("golden captures not present")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opentpt_resume_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_accepted_points_come_back_from_the_journal(self):
        first, _, _ = _run(self.tmp, RECIPE)
        accepted = len(first.accepted_points)
        self.assertGreater(accepted, 0)

        prior = load_prior(self.tmp, Recipe.from_dict(copy.deepcopy(RECIPE)))
        self.assertEqual(len(prior), accepted)

    def test_restored_values_match_the_originals(self):
        first, _, _ = _run(self.tmp, RECIPE)
        prior = load_prior(self.tmp, Recipe.from_dict(copy.deepcopy(RECIPE)))
        for p in first.accepted_points:
            restored = prior.get(p.procedure, p.tag)
            self.assertIsNotNone(restored, p.tag)
            for f in ("B_peak_T", "Pv_kW_m3", "mu_secant", "mu_prime",
                      "mu_second", "imbalance_pct", "Q_cycle_J"):
                self.assertAlmostEqual(
                    getattr(restored.analysis, f), getattr(p.analysis, f),
                    places=6, msg=f"{p.tag}.{f}")

    def test_rejects_are_not_restored_they_are_retried(self):
        # Tighten the imbalance gate so the 25 kHz capture (3.21 %) fails
        # while the 100 kHz one (1.16 %) still passes - that guarantees a
        # mix of accepted and rejected points to check against.
        strict = copy.deepcopy(RECIPE)
        strict["qc"] = {"max_imbalance_pct": 2.0}
        first, _, _ = _run(self.tmp, strict)
        rejected = [p for p in first.all_points if not p.accepted]
        self.assertTrue(rejected, "the tightened gate produced no rejects")
        self.assertTrue(first.accepted_points, "everything was rejected")

        prior = load_prior(self.tmp, Recipe.from_dict(copy.deepcopy(strict)))
        for p in rejected:
            self.assertIsNone(prior.get(p.procedure, p.tag),
                              f"{p.tag} was rejected but came back")
        for p in first.accepted_points:
            self.assertIsNotNone(prior.get(p.procedure, p.tag), p.tag)

    def test_skipped_points_are_not_restored_either(self):
        first, _, _ = _run(self.tmp, RECIPE)
        skipped = [s["tag"] for pr in first.procedures for s in pr.skipped]
        self.assertTrue(skipped, "expected the 100 kHz / 200 mT refusal")
        prior = load_prior(self.tmp, Recipe.from_dict(copy.deepcopy(RECIPE)))
        restored_tags = {tag for _proc, tag in prior.points}
        for tag in skipped:
            self.assertNotIn(tag, restored_tags)

    def test_a_resumed_run_does_not_re_measure(self):
        first, _, _ = _run(self.tmp, RECIPE)
        second, bench, bus = _run(self.tmp, RECIPE, resume=True)

        restored = [e for e in bus.events if e.kind == ev.POINT_RESTORED]
        self.assertEqual(len(restored), len(first.accepted_points))
        # The whole point: the bench was not asked for those captures again.
        self.assertLess(len(bench.calls), len(first.all_points),
                        "resumed run re-captured points it already had")

    def test_a_resumed_run_still_produces_the_full_dataset(self):
        first, _, _ = _run(self.tmp, RECIPE)
        second, _, _ = _run(self.tmp, RECIPE, resume=True)
        self.assertEqual({p.tag for p in second.accepted_points},
                         {p.tag for p in first.accepted_points})

    def test_restored_points_are_marked_as_such(self):
        _run(self.tmp, RECIPE)
        second, _, _ = _run(self.tmp, RECIPE, resume=True)
        self.assertTrue(all(p.extra.get("restored_from_journal")
                            for p in second.accepted_points))

    def test_a_changed_recipe_is_refused(self):
        _run(self.tmp, RECIPE)
        changed = copy.deepcopy(RECIPE)
        changed["limits"] = {"psu_max_V": 45.0}
        with self.assertRaises(ResumeRefused) as cm:
            load_prior(self.tmp, Recipe.from_dict(changed))
        self.assertIn("limits", str(cm.exception))

    def test_the_engine_aborts_rather_than_mixing_datasets(self):
        _run(self.tmp, RECIPE)
        changed = copy.deepcopy(RECIPE)
        changed["qc"] = {"max_imbalance_pct": 20.0}
        result, _, _ = _run(self.tmp, changed, resume=True)
        self.assertIsNotNone(result.aborted)
        self.assertIn("different recipe", result.aborted)

    def test_fingerprint_comes_from_the_first_run_not_the_last(self):
        """Regression: a resumed run must not compare the recipe to itself.

        The journal is appended across runs and a resumed run emits its own
        ``run_started`` *before* reading it. Taking the last ``run_started``
        made a changed recipe match itself, so the refusal never fired and two
        incompatible half-datasets would have been merged silently.
        """
        _run(self.tmp, RECIPE)
        changed = copy.deepcopy(RECIPE)
        changed["limits"] = {"psu_max_V": 45.0}
        # Simulate the poisoning: append a run_started for the changed recipe.
        r2 = Recipe.from_dict(copy.deepcopy(changed))
        with open(os.path.join(self.tmp, "journal.jsonl"), "a",
                  encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "run_started", "t": 0,
                                 "recipe_document": r2.to_dict()},
                                default=float) + "\n")
        with self.assertRaises(ResumeRefused):
            load_prior(self.tmp, r2)      # still refused: first entry wins

    def test_no_prior_journal_is_not_an_error(self):
        prior = load_prior(self.tmp, Recipe.from_dict(copy.deepcopy(RECIPE)))
        self.assertEqual(len(prior), 0)


class TestAcceptance(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not _have():
            raise unittest.SkipTest("golden captures not present")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opentpt_accept_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _two_runs(self):
        a, _, _ = _run(os.path.join(self.tmp, "a"), RECIPE)
        b, _, _ = _run(os.path.join(self.tmp, "b"), RECIPE)
        return a, b

    def test_two_identical_runs_are_accepted(self):
        report = compare(*self._two_runs())
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.counts()["deviates"], 0)
        self.assertEqual(report.worst("Pv"), 0.0)

    def test_a_value_drifting_past_tolerance_fails(self):
        a, b = self._two_runs()
        # Nudge one point's loss 20 % - well past the 5 % Pv tolerance.
        b.accepted_points[0].analysis.Pv_kW_m3 *= 1.20
        report = compare(a, b)
        self.assertFalse(report.ok)
        self.assertEqual(report.counts()["deviates"], 1)
        self.assertIn("Pv", report.points[0].exceeded
                      if report.points[0].exceeded else
                      [x for p in report.points for x in p.exceeded])

    def test_drift_inside_tolerance_still_passes(self):
        a, b = self._two_runs()
        b.accepted_points[0].analysis.Pv_kW_m3 *= 1.01     # 1 %, under 5 %
        self.assertTrue(compare(a, b).ok)

    def test_a_point_accepted_in_only_one_run_fails_acceptance(self):
        """The most important signal: a rig sitting on a QC threshold."""
        a, b = self._two_runs()
        b.procedures[0].points[0].accepted = False
        report = compare(a, b)
        self.assertFalse(report.ok)
        self.assertEqual(report.counts()["only_in_a"], 1)

    def test_the_report_says_what_it_does_not_prove(self):
        report = compare(*self._two_runs())
        self.assertIn("current_probe_scale", report.note)
        self.assertIn("Repeatability only", report.note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
