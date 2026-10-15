"""Readable actions from real parsed inputs and optional recorded service runs."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_plausibility_check, verify_audit
from semantic_plausibility.medication_report import html_report
from semantic_plausibility.medication_report_table import medication_fields, readable
from test_medication import configuration, details
from semantic_plausibility.report_comparison import field_table_html, field_details_html
from test_laboratory_report import TableParser


class MedicationComparisonTests(unittest.TestCase):
    def test_offline_membership_stays_review_and_complete_instructions_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_plausibility_check(configuration(Path(directory)))
            with result.csv.open(encoding="utf-8-sig") as stream:
                rows = {row["tuple_id"]: row for row in csv.DictReader(stream)}
            for detail in details(result):
                fields = medication_fields(rows[detail["tuple_id"]], detail)
                rendered = field_table_html(fields, prefix="test") + field_details_html(fields, prefix="test")
                for text in ("http://unitsofmeasure.org", "frequency: 2", "period unit: d", "False", "No proposal"):
                    self.assertIn(text, rendered)
                self.assertNotIn("Keep checked coding", rendered)
                self.assertNotIn("Review conditional conversion", rendered)
                for field in fields:
                    self.assertNotEqual(field.source_state, "green")
                    self.assertNotEqual(field.proposal_state, "green")
                if "liquid but dose describes" in str(detail["findings"]):
                    self.assertIn("liquid but dose describes", rendered)
            self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_all_quantity_systems_and_constraints_are_retained(self):
        # Input representation only; this is not a terminology response.
        value = {"doseQuantity": {"value": 5, "code": "mL", "system": "urn:local:units"},
            "rateRatio": {"numerator": {"value": 2, "code": "mg", "system": "http://unitsofmeasure.org"}},
            "asNeededBoolean": True, "timing": {"repeat": {"frequencyMax": 3}}}
        rendered = readable(value)
        for part in ("5 mL", "urn:local:units", "2 mg", "http://unitsofmeasure.org", "as needed boolean: True", "frequency max: 3"):
            self.assertIn(part, rendered)


@unittest.skipUnless(os.getenv("SQC_MEDICATION_REPORT_DIR"), "Supply an actual audited medication example run")
class RecordedMedicationComparisonTests(unittest.TestCase):
    def test_recorded_calculations_remain_conditional_and_mismatches_are_not_replacements(self):
        root = Path(os.environ["SQC_MEDICATION_REPORT_DIR"])
        seen_kinds, mismatch_count, conversions = set(), 0, 0
        with tempfile.TemporaryDirectory() as directory:
            for folder in sorted(root.glob("0*-medication-*")):
                audit = folder / "report.audit.jsonl"
                self.assertEqual(verify_audit(audit)["status"], "verified")
                with (folder / "report.csv").open(encoding="utf-8-sig") as stream:
                    rows = {row["tuple_id"]: row for row in csv.DictReader(stream)}
                evidence = [json.loads(line) for line in (folder / "report.evidence.jsonl").read_text().splitlines()]
                pairs = [(rows[detail["tuple_id"]], detail) for detail in evidence]
                summary = json.loads((folder / "report.summary.json").read_text())
                target = Path(directory) / (folder.name + ".html")
                html_report(target, pairs, summary, "report.csv")
                self.assertIn("Method + finding", target.read_text())
                parser = TableParser()
                parser.feed(target.read_text())
                self.assertEqual(parser.item_tables, len(pairs))
                self.assertEqual(parser.item_ids, [row["tuple_id"] for row, _ in pairs])
                self.assertEqual(parser.open_set_details, 0)
                for row, detail in pairs:
                    seen_kinds.add(row["resource_type"])
                    fields = medication_fields(row, detail)
                    rendered = field_table_html(fields, prefix="test") + field_details_html(fields, prefix="test")
                    self.assertIn(row["resource_type"], target.read_text())
                    for proposal in detail["recommendations"]:
                        if proposal["type"] == "strength_conversion":
                            conversions += 1
                            self.assertIn("Review conditional conversion", rendered)
                            self.assertIn("Not a replacement dose", rendered)
                            self.assertTrue(any(f.action == "Review conditional conversion" and f.proposal_state == "yellow" for f in fields))
                        if proposal["type"] == "infusion_comparison" and not proposal["matches"]:
                            mismatch_count += 1
                            self.assertIn("Review discrepancy", rendered)
                            self.assertIn("Do not replace the dose", rendered)
                            arithmetic = [f for f in fields if f.name.startswith("Rate × duration")]
                            self.assertTrue(all(f.source_state == "red" and f.proposal_state == "red" for f in arithmetic))
                self.assertEqual(verify_audit(audit)["status"], "verified")
        self.assertGreater(conversions, 0)
        self.assertGreater(mismatch_count, 0)
        self.assertTrue({"Medication", "MedicationRequest", "MedicationAdministration", "MedicationStatement"} <= seen_kinds)


if __name__ == "__main__":
    unittest.main()
