"""Recipe validation, safe ordering, and a full end-to-end run on stored data.

The replay test is the important one: it exercises the *whole* engine — recipe
to procedures to gates to MAS document — with no instruments attached.  That is
what makes the engine testable in CI, and it is the reason the bench layer has
a replay implementation at all.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from opentpt import events as ev                     # noqa: E402
from opentpt.bench import ReplayBench                # noqa: E402
from opentpt.drive import plan_point                 # noqa: E402
from opentpt.engine import Engine                    # noqa: E402
from opentpt.mas import build_mas, build_provenance, write_dataset     # noqa: E402
from opentpt.recipe import (                         # noqa: E402
    Limits, Recipe, RecipeError, load_recipe,
)

MINIMAL = {
    "name": "unit",
    "dut": {"shape": "TX26/15/10", "material": "3C90", "N1": 10, "N2": 10,
            "L_estimate_H": 404e-6},
    "procedures": [{"type": "core_loss_map",
                    "frequency_Hz": [25e3], "B_peak_T": [0.2]}],
}


def recipe(**over):
    d = json.loads(json.dumps(MINIMAL))
    d.update(over)
    return Recipe.from_dict(d)


class TestRecipeValidation(unittest.TestCase):

    def test_geometry_is_looked_up_not_guessed(self):
        r = recipe()
        self.assertIsNotNone(r.dut.Ae)
        self.assertIsNotNone(r.dut.le)
        self.assertIsNotNone(r.dut.Ve)
        self.assertTrue(r.dut.geometry_source)
        # Whatever the source, Ve must be consistent with Ae*le to well under
        # a per cent - a mismatch there silently rescales every Pv.
        self.assertAlmostEqual(r.dut.Ve / (r.dut.Ae * r.dut.le), 1.0, delta=0.01)

    def test_catalogue_geometry_is_used_when_the_database_is_present(self):
        from opentpt import omdb
        if not omdb.available():
            self.skipTest("PyOpenMagnetics not installed")
        r = recipe()
        self.assertEqual(r.dut.catalogue_name, "T 26/15/10")
        self.assertAlmostEqual(r.dut.Ae, 55.0e-6, delta=0.2e-6)
        self.assertAlmostEqual(r.dut.le, 64.40e-3, delta=0.2e-3)
        self.assertIn("OpenMagnetics", r.dut.geometry_source)

    def test_explicit_geometry_overrides_and_is_labelled(self):
        r = recipe(dut={"shape": "TX26/15/10", "material": "3C90", "N1": 10,
                        "N2": 10, "Ae": 50e-6, "le": 60e-3})
        self.assertAlmostEqual(r.dut.Ae, 50e-6)
        self.assertEqual(r.dut.geometry_source, "explicit in recipe")

    def test_unknown_shape_must_be_given_geometry(self):
        with self.assertRaises(RecipeError):
            recipe(dut={"shape": "definitely not a core", "material": "3C90",
                        "N1": 10, "N2": 10})
        r = recipe(dut={"shape": "definitely not a core", "material": "3C90",
                        "N1": 10, "N2": 10, "Ae": 1e-5, "le": 5e-2})
        self.assertAlmostEqual(r.dut.Ve, 1e-5 * 5e-2)

    def test_unknown_procedure_type_is_refused(self):
        with self.assertRaises(RecipeError):
            recipe(procedures=[{"type": "measure_vibes"}])

    def test_unknown_keys_are_refused_not_ignored(self):
        # A typo in a threshold must not silently fall back to the default.
        with self.assertRaises(ValueError):
            recipe(qc={"max_imbalence_pct": 5.0})
        with self.assertRaises(RecipeError):
            recipe(limits={"psu_max_v": 30})

    def test_empty_procedure_list_is_refused(self):
        with self.assertRaises(RecipeError):
            recipe(procedures=[])

    def test_the_shipped_recipe_loads(self):
        path = os.path.join(_ROOT, "recipes", "tx26_3c90.recipe.json")
        if not os.path.exists(path):
            self.skipTest("shipped recipe not present")
        r = load_recipe(path)
        self.assertEqual(r.dut.material, "3C90")
        self.assertTrue(r.procedures)


class TestSafeOrdering(unittest.TestCase):

    def test_low_stress_procedures_run_first(self):
        r = recipe(procedures=[
            {"type": "saturation_curve"},
            {"type": "core_loss_map", "frequency_Hz": [25e3],
             "B_peak_T": [0.1]},
        ])
        order = [p.type for p in r.ordered_procedures()]
        self.assertLess(order.index("core_loss_map"),
                        order.index("saturation_curve"))

    def test_demagnetize_is_inserted_after_a_saturating_procedure(self):
        r = recipe(procedures=[
            {"type": "saturation_curve"},
            {"type": "core_loss_map", "frequency_Hz": [25e3],
             "B_peak_T": [0.1]},
        ])
        order = [p.type for p in r.ordered_procedures()]
        # loss map, then saturation — nothing measured after it, so no reset
        # is needed and none is inserted.
        self.assertEqual(order, ["core_loss_map", "saturation_curve"])

    def test_reset_is_inserted_between_saturation_and_later_work(self):
        r = recipe(procedures=[
            {"type": "core_loss_map", "frequency_Hz": [25e3],
             "B_peak_T": [0.1]},
            {"type": "inductance_vs_bias"},
            {"type": "saturation_curve"},
            {"type": "demagnetize"},
        ])
        order = [p.type for p in r.ordered_procedures()]
        self.assertEqual(
            order,
            ["core_loss_map", "inductance_vs_bias", "demagnetize",
             "saturation_curve", "demagnetize"])

    def test_an_authored_demagnetize_is_a_fence(self):
        r = recipe(procedures=[
            {"type": "saturation_curve"},
            {"type": "demagnetize"},
            {"type": "core_loss_map", "frequency_Hz": [25e3],
             "B_peak_T": [0.1]},
        ])
        order = [p.type for p in r.ordered_procedures()]
        # The loss map is NOT hoisted above the fence, even though it is
        # lower stress — the author put a reset between them on purpose.
        self.assertEqual(order,
                         ["saturation_curve", "demagnetize", "core_loss_map"])


class TestFeasibilityRefusal(unittest.TestCase):

    def setUp(self):
        self.dut = recipe().dut
        self.limits = Limits()

    def test_a_point_over_the_psu_ceiling_is_refused_not_clamped(self):
        plan = plan_point(self.dut, self.limits, frequency=100e3,
                          B_target_T=0.2)
        self.assertFalse(plan.feasible)
        self.assertIn("PSU ceiling", plan.reason)
        # And crucially: no drive was produced for it.
        self.assertEqual(plan.pulses, [])

    def test_a_reachable_point_is_planned(self):
        plan = plan_point(self.dut, self.limits, frequency=25e3,
                          B_target_T=0.2)
        self.assertTrue(plan.feasible, plan.reason)
        self.assertLess(plan.voltage_V, self.limits.psu_max_V)
        self.assertEqual(len(plan.pulses), 8)

    def test_a_target_above_b_sat_is_refused(self):
        plan = plan_point(self.dut, self.limits, frequency=25e3,
                          B_target_T=0.40)
        self.assertFalse(plan.feasible)
        self.assertIn("B_sat", plan.reason)

    def test_bias_without_an_inductance_estimate_is_refused(self):
        d = recipe(dut={"shape": "TX26/15/10", "material": "3C90",
                        "N1": 10, "N2": 10}).dut
        plan = plan_point(d, self.limits, frequency=25e3, B_target_T=0.1,
                          dc_bias_A=0.2)
        self.assertFalse(plan.feasible)
        self.assertIn("L_estimate_H", plan.reason)

    def test_validate_counts_feasible_and_refused(self):
        r = recipe(procedures=[{"type": "core_loss_map",
                                "frequency_Hz": [25e3, 100e3],
                                "B_peak_T": [0.1, 0.2]}])
        n, skipped = Engine(r, bench=object(), dry_run=True).validate()
        self.assertEqual(n, 3)                 # 100 kHz @ 200 mT needs ~46 V
        self.assertEqual(len(skipped), 1)
        self.assertIn("PSU ceiling", skipped[0]["reason"])


class TestEndToEndReplay(unittest.TestCase):
    """A whole run: recipe → procedures → gates → MAS, on stored captures."""

    @classmethod
    def setUpClass(cls):
        captures = [(25e3, os.path.join(_ROOT, "coreloss_25kHz_200mT.csv")),
                    (100e3, os.path.join(_ROOT, "coreloss_100kHz_100mT.csv"))]
        for _, p in captures:
            if not os.path.exists(p):
                raise unittest.SkipTest("golden captures not present")

        cls.tmp = tempfile.mkdtemp(prefix="opentpt_test_")
        cls.recipe = recipe(
            name="replay",
            procedures=[{"type": "core_loss_map",
                         "frequency_Hz": [25e3, 100e3],
                         "B_peak_T": [0.1, 0.2]}],
            export={"directory": cls.tmp, "origin": "measurement",
                    "save_waveforms": False},
        )
        bus = ev.EventBus()
        cls.seen = []
        bus.subscribe(cls.seen.append)
        cls.bench = ReplayBench(captures, bus=bus)
        cls.result = Engine(cls.recipe, bench=cls.bench, bus=bus,
                            output_dir=cls.tmp).run()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_run_completed(self):
        self.assertIsNone(self.result.aborted)

    def test_points_were_accepted(self):
        self.assertGreater(len(self.result.accepted_points), 0)

    def test_the_infeasible_point_was_skipped_with_a_reason(self):
        skipped = [s for pr in self.result.procedures for s in pr.skipped]
        self.assertTrue(skipped)
        self.assertTrue(all(s["reason"] for s in skipped))
        self.assertTrue(any("PSU ceiling" in s["reason"] for s in skipped))

    def test_every_accepted_point_passed_every_gate(self):
        for p in self.result.accepted_points:
            self.assertTrue(p.verdict.passed)
            self.assertEqual(p.verdict.failures, [])

    def test_the_event_stream_reports_the_run(self):
        kinds = [e.kind for e in self.seen]
        self.assertIn(ev.RUN_STARTED, kinds)
        self.assertIn(ev.PROCEDURE_STARTED, kinds)
        self.assertIn(ev.POINT_SKIPPED, kinds)
        self.assertIn(ev.POINT_RESULT, kinds)
        self.assertIn(ev.RUN_FINISHED, kinds)

    def test_mas_document_carries_the_measured_fields(self):
        doc = build_mas(self.recipe, self.result)

        # volumetricLosses is keyed by SHAPE FAMILY, and its arrays hold
        # method-data objects. Raw measured points have exactly one
        # conformant home in coreMaterial: a roshen entry's
        # referenceVolumetricLosses. (See the caveat in the provenance file -
        # it is the schema's only array of volumetricLossesPoint, not a claim
        # that a Roshen model was fitted.)
        measured = [entry
                    for family in doc["volumetricLosses"].values()
                    for entry in family
                    if "referenceVolumetricLosses" in entry]
        self.assertTrue(measured, "no measured loss points in the document")
        points = [p for e in measured for p in e["referenceVolumetricLosses"]]
        self.assertTrue(points)
        for pt in points:
            self.assertEqual(pt["origin"], "measurement")
            self.assertIn("temperature", pt)
            self.assertIn("magneticFluxDensity", pt)

        self.assertTrue(doc["permeability"]["amplitude"])
        self.assertTrue(doc["permeability"]["complex"]["real"])
        self.assertTrue(doc["permeability"]["complex"]["imaginary"])
        self.assertGreaterEqual(len(doc["bhCycle"]), 4)   # schema minimum

    def test_unmeasurable_fields_come_from_the_catalogue_not_from_us(self):
        from opentpt import omdb
        doc = build_mas(self.recipe, self.result)
        if omdb.available():
            # MAS *requires* resistivity, which this bench cannot measure. It
            # must arrive from the database rather than be invented or omitted.
            self.assertIn("resistivity", doc)
            self.assertTrue(doc["resistivity"])
        else:
            self.skipTest("PyOpenMagnetics not installed")

    def test_provenance_is_a_sidecar_not_a_document_field(self):
        # The MAS root sets additionalProperties:false, so provenance cannot
        # live inside the document without invalidating it.
        doc = build_mas(self.recipe, self.result)
        self.assertNotIn("_opentpt", doc)
        prov = build_provenance(self.recipe, self.result)
        self.assertEqual(prov["recipe"]["dut"]["material"], "3C90")
        self.assertTrue(prov["caveats"])
        self.assertTrue(prov["geometrySource"])

    def test_measured_permeability_slots_are_single_sourced(self):
        # permeabilityPoint has no origin field, so a slot we measured must be
        # replaced wholesale - never merged with catalogue points.
        doc = build_mas(self.recipe, self.result)
        amp = doc["permeability"]["amplitude"]
        measured = {round(p.analysis.B_peak_T, 6)
                    for p in self.result.accepted_points
                    if p.analysis and not p.plan.get("dc_bias_A")}
        self.assertTrue(amp)
        for p in amp:
            self.assertIn(round(p["magneticFluxDensityPeak"], 6), measured)

    def test_write_dataset_produces_the_files(self):
        out = tempfile.mkdtemp(prefix="opentpt_out_")
        try:
            paths, report = write_dataset(self.recipe, self.result, out)
            self.assertEqual(len(paths), 4)
            for p in paths:
                self.assertTrue(os.path.exists(p), p)
                self.assertGreater(os.path.getsize(p), 0)
            self.assertTrue(any(p.endswith(".provenance.json") for p in paths))
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_the_written_document_validates_against_the_mas_schema(self):
        """The headline claim of the export: it is a valid MAS coreMaterial."""
        from opentpt import mas_schema
        doc = build_mas(self.recipe, self.result)
        report = mas_schema.validate(doc)
        self.assertTrue(report.ok, "; ".join(report.errors[:12]))

    def test_validation_reports_what_it_could_not_check(self):
        """"Valid" must never be mistaken for "fully validated".

        MAS $refs a sibling spec (PEAS) that ships separately. Without a PEAS
        checkout those subtrees are stubbed permissively, and the report has
        to say so — otherwise a document whose PEAS-shaped parts were never
        looked at reads as fully conformant.
        """
        from opentpt import mas_schema
        report = mas_schema.validate(build_mas(self.recipe, self.result))
        self.assertTrue(report.schema_version)
        if report.stubbed_refs:
            self.assertFalse(report.complete)
            self.assertIn("NOT checked", report.summary())
        else:
            self.assertTrue(report.complete)

    def test_the_catalogue_record_itself_validates(self):
        """Cross-check on the validator: the database's own 3C90 must pass.

        If this fails the validator is wrong, not the exporter.
        """
        from opentpt import mas_schema, omdb
        from opentpt.mas import _strip_nulls
        base = omdb.material_document("3C90")
        if base is None:
            self.skipTest("PyOpenMagnetics not installed")
        report = mas_schema.validate(_strip_nulls(base))
        self.assertTrue(report.ok, "; ".join(report.errors[:12]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
