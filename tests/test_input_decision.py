"""Laya input contracts; integration/replay uses an actual local model response."""

import copy
import csv
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from semantic_plausibility.batch import BatchConfiguration, run_plausibility_check
from semantic_plausibility.input_decision import InputDecision, decision_state, evaluate_answers, settings
from semantic_plausibility.review_feedback import report_details
from semantic_plausibility.study_compare import verify_audit
from test_batch import ROOT, configuration


class InputDecisionTests(unittest.TestCase):
    def test_optional_default_needs_no_laya_runtime(self):
        config = BatchConfiguration(ROOT / "pipeline.example.toml")
        self.assertFalse(settings(config)["enabled"])
        self.assertIsNone(InputDecision(config).assess({}, {}))
        self.assertEqual(config.data["models"]["local"]["model"], "ministral-3:3b")

    def test_invalid_settings_rejected_before_network(self):
        for key, value in (("enabled", "yes"), ("device", []), ("min_probability", float("nan")),
                           ("min_probability", True), ("timeout_seconds", 0), ("revision", "main"),
                           ("base_url", 42), ("base_url", "https://public.example"),
                           ("base_url", "http://user:secret@localhost:8085"),
                           ("container_base_url", "http://laya:8000?secret=123")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                settings(SimpleNamespace(data={"decision": {key: value}}))

    def test_request_contains_bounded_metadata_and_native_type_only(self):
        from semantic_plausibility.audit import SourceRecord, semantic_items
        resource = json.loads((ROOT / "examples/lab/observation.json").read_text())
        payload, _, _ = next(semantic_items(SourceRecord(resource=resource, context={}, source_ref="test")))
        config = SimpleNamespace(input_type="fhir_file", full_fhir_input=True)
        state = decision_state(config, payload, {"analyte_name": "Hemoglobin", "source_id": "patient-specific-id"})
        self.assertEqual(state["resource_type"], "Observation")
        self.assertEqual(set(state["unit"]), {"system", "code", "unit"})
        self.assertNotIn("value", state["unit"])
        self.assertNotIn("patient-specific-id", json.dumps(state))
        self.assertNotIn("referenceRange", json.dumps(state))
        config.input_type, config.full_fhir_input = "csv", False
        self.assertIsNone(decision_state(config, payload, {})["resource_type"])

    def test_check_works_when_decision_is_disabled(self):
        with tempfile.TemporaryDirectory() as name:
            config = configuration(Path(name))
            result = run_plausibility_check(config)
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary["input_decision"], {"enabled": False, "calls": 0, "states": {}})
            with result.csv.open(encoding="utf-8-sig", newline="") as stream:
                self.assertFalse(any(key.startswith("decision.") for key in next(csv.DictReader(stream))))


class RealLayaTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SQC_LIVE_LAYA_CONFIG"), "Set SQC_LIVE_LAYA_CONFIG to a real enabled laboratory TOML/service")
    def test_live_laboratory_run_is_auditable_and_advisory(self):
        with tempfile.TemporaryDirectory() as name:
            config = Path(os.environ["SQC_LIVE_LAYA_CONFIG"])
            self.assertTrue(settings(BatchConfiguration(config))["enabled"])
            result = run_plausibility_check(config, output=Path(name) / "report.csv")
            self.assertEqual(verify_audit(result.log)["status"], "verified")
            rows, details = report_details(result.csv)
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary["input_decision"]["calls"], len(rows))
            for detail in details.values():
                record = detail["input_decision"]
                self.assertEqual(record["request"]["state"]["unit"].get("system"), detail["metadata"]["quantity"].get("system"))
                self.assertEqual(record["request"]["state"]["unit"].get("code"), detail["metadata"]["quantity"].get("code"))
                self.assertIn(record["state"], {"advisory", "configuration_conflict"})
                self.assertFalse(record["source_changed"])
                self.assertFalse(record["services_selected_automatically"])
                self.assertEqual(evaluate_answers(record["response"], minimum=record["min_probability"]), record["decisions"])
                # Exercise rejection/abstention by modifying a real response, not a fabricated fixture.
                cut = copy.deepcopy(record["response"])
                cut["usage"]["truncated"] = True
                self.assertTrue(all(r["state"] == "abstained" for r in evaluate_answers(cut, minimum=0.8).values()))
                corrupt = copy.deepcopy(record["response"])
                corrupt["answers"]["domain"]["probabilities"]["laboratory"] = float("nan")
                with self.assertRaises(ValueError):
                    evaluate_answers(corrupt, minimum=0.8)
            with result.csv.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    self.assertEqual(row["assessment.input_decision.source_status"], "grey")
                    record = details[row["tuple_id"]]["input_decision"]
                    expected = "yellow" if any(d["state"] == "candidate" for d in record["decisions"].values()) else "red"
                    self.assertEqual(row["assessment.input_decision.proposal_status"], expected)
                    self.assertIn(row["decision.revision"], result.report.read_text())


if __name__ == "__main__":
    unittest.main()
