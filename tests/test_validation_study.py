"""Study arithmetic and real offline report contracts; no invented lookup replies."""

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from semantic_plausibility import prepare_study_review, evaluate_study_review, run_batch
from semantic_plausibility.cli import run_main
from semantic_plausibility.validation_study import FIELDS, _summarize
from test_batch import configuration


class StudyArithmeticTests(unittest.TestCase):
    def test_detection_quality_and_effort_have_separate_denominators(self):
        # Controlled study labels test the maths, not the clinical truth of any example.
        cases = [
            ("error", True, True, "acceptable", "supported_applicable", 60),
            ("error", True, True, "partly_correct", "unsupported_or_inapplicable", 120),
            ("error", False, True, "incorrect", "uncertain", None),
            ("correct", True, True, "acceptable", "supported_applicable", 0),
            ("correct", False, False, "acceptable", "not_present", 30),
            ("metadata_only", True, True, "no_proposal", "not_present", None),
            ("indeterminate", False, True, "unassessable", "", None),
            ("", False, False, "", "", None),
        ]
        result = _summarize([dict(zip(("reference_assessment", "system_error", "system_review",
            "recommendation_assessment", "evidence_assessment", "review_seconds"), case)) for case in cases])
        self.assertEqual(result["confusion_matrix"], {"tp": 2, "fp": 1, "fn": 1, "tn": 1})
        expected = {
            "reference_review_completion": (7, 8), "error_evaluation_coverage": (5, 8),
            "error_detection_sensitivity": (2, 3), "error_flag_precision": (2, 3),
            "review_capture_sensitivity": (3, 3), "review_queue_fraction": (6, 8),
            "recommendation_review_completion": (7, 8), "recommendation_acceptability": (3, 5),
            "proposal_availability_among_assessed": (6, 7), "evidence_applicability": (2, 3),
        }
        for key, (numerator, denominator) in expected.items():
            with self.subTest(metric=key):
                metric = result["metrics"][key]
                self.assertEqual((metric["numerator"], metric["denominator"]), (numerator, denominator))
                self.assertAlmostEqual(metric["value"], numerator / denominator)
        self.assertEqual(result["excluded_error_flags"], {"metadata_only": 1, "indeterminate": 0})
        self.assertEqual(result["effort"], {"timed_sets": 4, "total_minutes": 3.5, "median_seconds_per_set": 45})
        low, high = result["metrics"]["error_detection_sensitivity"]["wilson_95"]
        self.assertAlmostEqual(low, 0.2076596, places=6)
        self.assertAlmostEqual(high, 0.9385081, places=6)

    def test_no_ground_truth_or_times_produces_unknown_not_zero_accuracy(self):
        for cases in ([], [{"reference_assessment": "indeterminate", "system_error": False,
                          "system_review": True, "recommendation_assessment": "unassessable"}]):
            result = _summarize(cases)
            for name in ("error_detection_sensitivity", "error_flag_precision", "recommendation_acceptability"):
                self.assertIsNone(result["metrics"][name]["value"])
                self.assertIsNone(result["metrics"][name]["wilson_95"])
            self.assertIsNone(result["effort"]["total_minutes"])
            self.assertIsNone(result["effort"]["median_seconds_per_set"])


class StudyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.report = run_batch(configuration(cls.folder)).csv

    def setUp(self):
        self.review = self.folder / f"{self._testMethodName}.csv"
        prepare_study_review(self.report, self.review)
        with self.review.open(newline="") as handle:
            self.rows = list(csv.DictReader(handle))
        self.out = self.folder / f"{self._testMethodName}-metrics"

    def write_review(self, rows=None, fields=FIELDS):
        with self.review.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows if rows is None else rows)

    def test_blank_template_and_library_workflow(self):
        self.assertEqual(len(self.rows), 7)
        self.assertTrue(all(not row["reference_assessment"] for row in self.rows))
        self.assertEqual(len({r["report_sha256"] for r in self.rows}), 1)
        self.assertNotIn("system_error", self.rows[0])  # Do not prelabel clinician judgements.
        before = self.report.read_bytes()
        result = evaluate_study_review(self.report, self.review, self.out)
        self.assertEqual(result["workflow"], "lookup")
        self.assertEqual(result["sets"], 7)
        self.assertEqual(result["metrics"]["reference_review_completion"]["numerator"], 0)
        self.assertIsNone(result["metrics"]["error_detection_sensitivity"]["value"])
        self.assertIn("Not estimated", Path(result["html"]).read_text())
        self.assertEqual(json.loads(Path(result["json"]).read_text())["confusion_matrix"],
                         {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        self.assertEqual(self.report.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "already exists"):
            evaluate_study_review(self.report, self.review, self.out)
        with self.assertRaises(FileExistsError):
            prepare_study_review(self.report, self.review)

    def test_cli_partial_review_uses_recorded_flags(self):
        # Exercise partial-review handling, not a clinical adjudication of these data.
        self.rows[0].update(reference_assessment="indeterminate", reviewer="test-reviewer", review_seconds="15")
        self.write_review()
        with redirect_stdout(io.StringIO()) as console:
            self.assertEqual(run_main(["study-metrics", "--report", str(self.report),
                                       "--review", str(self.review), "--out", str(self.out)]), 0)
        result = json.loads(console.getvalue())
        self.assertEqual(result["counts"]["reference_assessment"]["indeterminate"], 1)
        self.assertEqual(result["metrics"]["reference_review_completion"]["numerator"], 1)
        self.assertEqual(result["metrics"]["recommendation_review_completion"]["numerator"], 0)
        self.assertEqual(result["effort"]["total_minutes"], 0.25)
        template = self.folder / "cli-review.csv"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run_main(["study-template", "--report", str(self.report), "--out", str(template)]), 0)
        with template.open(newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle))[0]["tuple_id"], self.rows[0]["tuple_id"])

    def test_recorded_error_flags_are_distinct_from_unavailable_checks(self):
        from semantic_plausibility.validation_study import _study_report
        _, rows, _ = _study_report(self.report)
        # Real offline checks: one reversed reference interval; others unresolved.
        self.assertEqual(sum(row["system_error"] for row in rows.values()), 1)
        self.assertTrue(all(row["system_review"] for row in rows.values()))

    def test_identity_rows_and_source_hash_are_protected(self):
        original = [dict(row) for row in self.rows]
        for key in ("report_sha256", "tuple_id", "code", "analyte_name", "unit_code"):
            with self.subTest(changed=key):
                self.rows = [dict(row) for row in original]
                self.rows[0][key] += "edited"
                self.write_review()
                with self.assertRaisesRegex(ValueError, "identity/source|tuple_id"):
                    evaluate_study_review(self.report, self.review, self.out)
        for rows in (original + [original[0]], original[:-1]):
            self.write_review(rows)
            with self.assertRaisesRegex(ValueError, "duplicate|every template row"):
                evaluate_study_review(self.report, self.review, self.out)

    def test_invalid_review_labels_and_times_are_rejected(self):
        original = [dict(row) for row in self.rows]
        for update in (
            {"reference_assessment": "probably"}, {"recommendation_assessment": "great"},
            {"evidence_assessment": "verified"}, {"reference_assessment": "error"},
            {"reference_assessment": "metadata_only"}, {"reviewer": ""},
            {"reference_assessment": ""}, *({"review_seconds": v} for v in ("NaN", "inf", "-1", "ten")),
        ):
            with self.subTest(update=update):
                self.rows = [dict(row) for row in original]
                self.rows[0].update(reference_assessment="correct", reviewer="test-reviewer")
                self.rows[0].update(update)
                self.write_review()
                with self.assertRaises(ValueError):
                    evaluate_study_review(self.report, self.review, self.out)
                self.assertFalse(self.out.with_suffix(".json").exists())

    def test_modified_audited_report_is_rejected(self):
        copy = self.folder / "changed"
        copy.mkdir()
        for path in self.report.parent.glob("review.*"):
            shutil.copyfile(path, copy / path.name)
        altered = copy / self.report.name
        altered.write_bytes(altered.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare_study_review(altered, copy / "clinician.csv")

    def test_item_mode_is_rejected(self):
        folder = self.folder / "items"
        folder.mkdir()
        config = configuration(folder)
        text = config.read_text().replace('mode = "distinct"', 'mode = "items"')
        text = text.replace('granularity = "distinct"', 'granularity = "individual"')
        text = text.replace("examples/laboratory.csv", "examples/lab/individual.csv")
        fields = ("id", "code", "code_system", "unit", "unit_code", "unit_system", "value",
                  "reference_low", "reference_high", "specimen", "status")
        columns = '[input.columns]\nanalyte_name="name"\n' + "\n".join(f'{key}="{key}"' for key in fields) + "\n"
        start, end = text.index("[input.columns]"), text.index("[output]")
        config.write_text(text[:start] + columns + text[end:])
        report = run_batch(config).csv
        with self.assertRaisesRegex(ValueError, "distinct"):
            prepare_study_review(report, folder / "clinician.csv")


if __name__ == "__main__":
    unittest.main()
