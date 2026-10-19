"""Illustrative review decisions exercise the workflow; all lookup verdicts are real."""

import csv
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_plausibility_check, import_reviews
from semantic_plausibility.evidence_research import research_plan
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.profile_lookups import terminology_plan, selected_profile
from semantic_plausibility.review_feedback import REVIEW_FIELDS, ReviewRegistry, mapping_scope, verify_report_profile
from test_batch import configuration, ROOT


class ProfileRouteTests(unittest.TestCase):
    def test_pinned_real_mii_profile_derives_ucum_and_preferred_loinc(self):
        config = BatchConfiguration(ROOT / "pipeline.example.toml")
        plan = terminology_plan(config)
        code = next(row for row in plan["elements"] if row["element"] == "Observation.code")
        unit = next(row for row in plan["elements"] if row["element"] == "Observation.valueQuantity")
        self.assertEqual(plan["profile_version"], "2026.0.3")
        self.assertEqual(code["binding_strength"], "preferred")
        self.assertEqual(code["preferred_or_bound_systems"], ["http://loinc.org"])
        self.assertEqual(code["required_systems"], [])
        self.assertEqual(unit["required_systems"], ["http://unitsofmeasure.org"])
        self.assertEqual(unit["primary_services"], ["ucum"])
        self.assertTrue(plan["profile_does_not_define_endpoint_urls"])


class SeparateResearchTests(unittest.TestCase):
    def test_plan_uses_audited_export_and_displays_prompts_without_model_or_source_access(self):
        with tempfile.TemporaryDirectory() as folder:
            config = configuration(Path(folder))
            result = run_plausibility_check(config)
            plan = research_plan(config, result.csv)
            self.assertEqual(plan["execution"], "plan_only_no_network_or_model_call")
            self.assertTrue(plan["targets"])
            self.assertIn("STUDY_MEASUREMENTS", plan["prompts"])
            self.assertIn("response_schema", plan["prompts"]["STUDY_MEASUREMENTS"])
            self.assertTrue(all("value" not in target and "reference_ranges" not in target for target in plan["targets"]))
            summary = json.loads(result.summary.read_text())
            summary["profile_rules_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "Profile rules"):
                verify_report_profile(BatchConfiguration(config), summary)

    def test_preparing_report_research_does_not_require_source_database_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            config = configuration(Path(folder))
            text = config.read_text().replace('type = "csv"', 'type = "postgres"')
            text += '\n[database]\nDB_TABLE="laboratory"\nDB_DSN_ENV="SQC_TEST_MISSING_SOURCE_DSN"\n'
            config.write_text(text)
            self.assertNotIn("SQC_TEST_MISSING_SOURCE_DSN", os.environ)
            deployment = BatchConfiguration(config).deployment
            with deployment.prepare(research=False, source_access=False):
                self.assertNotIn("SQC_CFG_DB_DSN", os.environ)
            with self.assertRaisesRegex(ValueError, "Missing dsn"):
                with deployment.prepare(research=False, source_access=True):
                    pass


@unittest.skipUnless(os.getenv("SQC_LIVE_TESTS") == "1", "Set SQC_LIVE_TESTS=1 to verify real targets/factors")
class ClinicianLoopTests(unittest.TestCase):
    def test_import_reuse_context_guard_idempotence_and_revocation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = configuration(root, connected=True)
            result = run_plausibility_check(config)
            with result.csv.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            source = next(row for row in rows if row["code"] == "718-7" and row["unit_code"] == "g/L")
            decision = {"tuple_id": source["tuple_id"], "review_decision": "approved",
                "reviewer": "Workflow integration test reviewer", "reviewed_at": "2026-10-10T12:00:00+00:00",
                "approved_code": "718-7", "approved_code_system": "http://loinc.org",
                "approved_unit": "g/dL", "approved_unit_system": "http://unitsofmeasure.org", "approved_factor": "0.1",
                "review_basis": "linear_conversion", "review_evidence": "Functionality test of the actual NLM g/L to g/dL factor; no clinical judgement"}
            reviewed, registry = root / "clinician-test.csv", root / "registry.json"

            def write_review():
                with reviewed.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["tuple_id", *REVIEW_FIELDS])
                    writer.writeheader()
                    writer.writerow(decision)

            write_review()
            imported = import_reviews(result.csv, reviewed, registry, config)
            self.assertEqual(imported["decisions_imported"], 1)
            self.assertEqual(import_reviews(result.csv, reviewed, registry, config)["decisions_imported"], 0)
            store = ReviewRegistry(registry)
            original = store.document["decisions"][0]
            self.assertEqual(original["verification_at_import"]["factor_basis"], "verified_ucum_factor")
            self.assertTrue(original["verification_at_import"]["reference_units_resolved"])
            detail = next(json.loads(line) for line in result.csv.with_suffix(".evidence.jsonl").read_text().splitlines()
                          if json.loads(line)["tuple_id"] == source["tuple_id"])
            profile = selected_profile(BatchConfiguration(config))
            payload = detail["metadata"]
            catalogues = original["scope"]["catalogue_fingerprint"]
            _, key = mapping_scope(payload, source["source_namespace"], profile, catalogues)
            self.assertIn(key, store.latest)
            changed = deepcopy(payload)
            changed["reference_ranges"][0]["high"]["value"] = 170
            self.assertNotEqual(mapping_scope(changed, source["source_namespace"], profile, catalogues)[1], key)
            self.assertNotEqual(mapping_scope(payload, "different-site", profile, catalogues)[1], key)
            changed = deepcopy(payload)
            changed["quantity"]["value"] = 150
            self.assertEqual(mapping_scope(changed, source["source_namespace"], profile, catalogues)[1], key)
            text = config.read_text().replace(BatchConfiguration(config).output["path"], str(root / "rechecked.csv"))
            text += '\n[quality]\nreview_registry = ' + json.dumps(str(registry)) + '\n'
            config.write_text(text)
            self.assertEqual(BatchConfiguration(config).data['quality']['review_registry'], str(registry))
            checked = run_plausibility_check(config)
            with checked.csv.open(encoding="utf-8-sig", newline="") as handle:
                reused = next(row for row in csv.DictReader(handle) if row["tuple_id"] == source["tuple_id"])
            self.assertEqual(reused["mapping_review_state"], "reused_clinician_approval")
            self.assertEqual(reused["suggested_factor"], "0.1")
            self.assertEqual(reused["unit_code"], "g/L")
            decision["review_decision"] = "revoked"
            decision["reviewed_at"] = "2026-10-10T13:00:00+00:00"
            write_review()
            self.assertEqual(import_reviews(result.csv, reviewed, registry, config)["decisions_imported"], 1)
            self.assertEqual(ReviewRegistry(registry).latest[key]["decision"], "revoked")
            decision["review_decision"] = "approved"
            decision["reviewed_at"] = "2026-10-10T12:00:00+00:00"
            write_review()
            with self.assertRaisesRegex(ValueError, "later"):
                import_reviews(result.csv, reviewed, registry, config)
            tampered = json.loads(registry.read_text())
            tampered["decisions"][0]["target"]["factor"] = "10"
            registry.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, "integrity"):
                ReviewRegistry(registry)
