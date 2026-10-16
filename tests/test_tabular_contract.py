"""Scalar inputs/reports and guarded hand-off; terminology evidence is never simulated."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility.aggregate_statistics import supplied_statistics
from semantic_plausibility.batch import BatchConfiguration, roles, run_plausibility_check
from semantic_plausibility.db_source import resource_from_row
from semantic_plausibility.evidence_research import research_plan
from semantic_plausibility.medication_input import validate_fields
from semantic_plausibility.review_feedback import report_details
from semantic_plausibility.tabular_report import ColumnWriter, flatten
from test_run_modes import configuration

ROOT = Path(__file__).resolve().parents[1]


class TabularContractTests(unittest.TestCase):
    def test_csv_roundtrip_preserves_zero_false_separators_and_later_columns(self):
        output = io.StringIO()
        with ColumnWriter(output) as writer:
            writer.writerow({"id": "a", "value": 0, "flag": False, "name": 'Quoted "name", first\nsecond'})
            writer.writerow({"id": "b", "reference_high": "16", "name": "=1+1"})
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(rows[0]["value"], "0")
        self.assertEqual(rows[0]["flag"], "False")
        self.assertEqual(rows[0]["name"], 'Quoted "name", first\nsecond')
        self.assertEqual(rows[0]["reference_high"], "")
        self.assertEqual(rows[1]["reference_high"], "16")
        self.assertEqual(rows[1]["name"], "'=1+1")

    def test_repeated_ranges_keep_each_bound_and_qualifier(self):
        result = {}
        flatten([{"low": {"value": 0}, "appliesTo": [{"text": "group A"}]},
                 {"high": {"value": 9}, "appliesTo": [{"text": "group B"}]}], "reference", result)
        self.assertEqual(result["reference.1.low.value"], 0)
        self.assertEqual(result["reference.2.high.value"], 9)
        self.assertEqual(result["reference.1.appliesTo.1.text"], "group A")
        self.assertEqual(result["reference.2.appliesTo.1.text"], "group B")
        self.assertTrue(all(not isinstance(v, (dict, list)) for v in result.values()))

    def test_starter_csv_is_scalar_and_research_reads_audited_details(self):
        with tempfile.TemporaryDirectory() as name:
            config = configuration(Path(name))
            result = run_plausibility_check(config)
            with result.csv.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(not key.endswith("_json") for key in rows[0]))
            self.assertTrue(all("input.reference_ranges.1.low.value" in row for row in rows))
            self.assertIn("assessment.code.source_status", rows[0])
            full, details = report_details(result.csv, hydrate=True)
            self.assertEqual({r["tuple_id"] for r in full}, set(details))
            self.assertTrue(all("evidence_json" in row for row in full))
            plan = research_plan(config, result.csv)
            self.assertEqual(plan["execution"], "plan_only_no_network_or_model_call")
            # Modifying a scalar cell invalidates the research hand-off as before.
            result.csv.write_bytes(result.csv.read_bytes().replace(b"718-7", b"718-8", 1))
            with self.assertRaises(ValueError):
                report_details(result.csv, hydrate=True)

    def test_scalar_example_files_have_explicit_reference_or_dose_columns(self):
        for name in ("laboratory", "medication-request", "medication-administration", "medication-statement"):
            with self.subTest(name=name), (ROOT / "examples" / (name + ".csv")).open() as handle:
                rows = list(csv.DictReader(handle))
                self.assertTrue(rows)
                self.assertFalse(any(k.endswith("_json") for k in rows[0]))
                self.assertIn("reference_low" if name == "laboratory" else "dose_value", rows[0])

    def test_ambiguous_role_and_dosage_mappings_fail_before_lookup(self):
        with self.assertRaisesRegex(ValueError, "once"):
            roles({"analyte_name": "name", "code_text": "other_name"})
        with self.assertRaisesRegex(ValueError, "common reference_unit"):
            roles({"reference_unit": "unit", "range_high_code": "other_unit"})
        with self.assertRaisesRegex(ValueError, "dosage columns"):
            validate_fields({"resource_type": "MedicationRequest", "fields": {
                k: k for k in ("product_code", "product_system", "status", "intent", "dose_value", "dosage_instructions_json")}})
        with tempfile.TemporaryDirectory() as name:
            config = configuration(Path(name))
            config.write_text(config.read_text().replace('[input.columns]', '[input.columns]\nreference_ranges_json="ranges"'))
            with self.assertRaisesRegex(ValueError, "reference columns"):
                BatchConfiguration(config)

    def test_extrema_only_statistics_validate_units_and_order(self):
        for data in ({"value_min": "1", "value_max": "4", "value_min_unit": "mg/L"},
                     {"value_min": "4", "value_max": "1"}, {"value_min": "NaN"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                supplied_statistics(data, {"code": "g/L"})

    def test_db_json_rejects_nonfinite_values_and_keeps_complete_resource(self):
        mapping = {"resource_type": "Observation", "fields": {"resource_json": "resource"}}
        resource = {"resourceType": "Observation", "valueQuantity": {"value": 0}}
        parsed, _ = resource_from_row({"resource_json": json.dumps(resource)}, mapping)
        self.assertEqual(parsed["valueQuantity"]["value"], 0)
        with self.assertRaises(ValueError):
            resource_from_row({"resource_json": '{"resourceType":"Observation","valueQuantity":{"value":NaN}}'}, mapping)
