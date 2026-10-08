"""Scientific/accounting, source-equivalence and offline-security acceptance tests."""
from copy import deepcopy
import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from semantic_plausibility import run_batch, compare_reports, verify_audit
from semantic_plausibility.audit import SourceRecord
from semantic_plausibility.export_grouping import ExportGroups, weighted_statistics
from semantic_plausibility.export_review import ReliableLookup
from semantic_plausibility.profile_import import import_structure_definition
from semantic_plausibility.snapshot_services import SnapshotLoinc, SnapshotUcum
from semantic_plausibility.observation_quality import LoincTerm
from semantic_plausibility.fhir_quality_pipeline import FhirQualityPipeline
from semantic_plausibility.profile_config import InputProfile, ElementRule
from semantic_plausibility.synthetic_study import config_text, create_examples, create_hub_database
from semantic_plausibility.terminology_router import FhirArtifactSource, CsvCodeSource

ROOT = Path(__file__).resolve().parents[1]


class ExportReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.rows = create_examples(self.data, ROOT / "examples/studies/reference-pack.synthetic.json")

    def config(self, kind="csv", *, source="lis", items=False, name="review", extra=""):
        path = self.root / (name + ".toml")
        path.write_text(config_text(self.data, kind=kind, source=source, items=items,
                                   output=self.root / (name + ".csv")) + extra)
        return path

    def report_rows(self, result):
        with result.csv.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_weighted_distinct_export_and_explicit_numeric_readiness(self):
        result = run_batch(self.config())
        rows = self.report_rows(result)
        self.assertEqual((result.input_records, result.distinct_tuples), (8, 7))
        hb = next(row for row in rows if row["occurrence_count"] == "45")
        self.assertEqual(Decimal(hb["value_mean"]), Decimal("14.5"))
        self.assertEqual(Decimal(hb["value_median"]), Decimal("14"))
        self.assertEqual(Decimal(hb["value_iqr"]), 0)
        self.assertEqual(hb["value_reference_check"], "outside_reported_range")
        self.assertEqual(hb["mii_numeric_set_ready"], "True")
        self.assertNotIn("source_id", rows[0])
        self.assertTrue(all(row["lookup_credibility"] == "synthetic_demo_only" for row in rows))
        summary = json.loads(result.summary.read_text())
        self.assertEqual(summary["metrics"]["mii_numeric_set_ready"]["numerator"], 3)
        self.assertEqual(summary["weighted_readiness"]["denominator"], 84)
        self.assertEqual(summary["full_fhir_validation"], "not_performed")
        self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_no_http_calls_without_a_reference_pack_and_without_remote_fallback(self):
        config = self.config()
        config.write_text("\n".join(line for line in config.read_text().splitlines() if not line.startswith("snapshot=")))
        with patch("semantic_plausibility.observation_services._HttpSession.get", side_effect=AssertionError("No HTTP")), patch("semantic_plausibility.observation_services._HttpSession.post", side_effect=AssertionError("No model")):
            result = run_batch(config)
        summary = json.loads(result.summary.read_text())
        self.assertEqual(summary["metrics"]["mii_numeric_set_ready"]["numerator"], 0)
        self.assertEqual(summary["evidence_kind"], "no_lookup_evidence")

    def test_items_retain_provenance_without_aggregating_source_ids(self):
        result = run_batch(self.config("fhir_file", items=True))
        rows = self.report_rows(result)
        self.assertEqual(len(rows), 84)
        self.assertEqual(len({row["source_id"] for row in rows}), 84)
        self.assertTrue(all(row["measurement_path"] == "Observation" for row in rows))

    def test_summary_or_distinct_input_cannot_restore_original_items(self):
        with self.assertRaisesRegex(ValueError, "individual"):
            run_batch(self.config(items=True))
        p = self.config("fhir_file")
        p.write_text(p.read_text().replace('granularity="individual"', 'granularity="distinct"'))
        with self.assertRaisesRegex(ValueError, "Native FHIR Observations"):
            run_batch(p)
        p = self.config("fhir_file")
        p.write_text(p.read_text() + '\n[input.columns]\ncode="ignored_code"\n')
        with self.assertRaisesRegex(ValueError, "native elements"):
            run_batch(p)

    def test_matched_lis_fhir_and_hub_changes_are_detected(self):
        create_hub_database(self.data / "hub.sqlite", self.rows)
        lis = run_batch(self.config(name="lis"))
        fhir = run_batch(self.config("fhir_file", source="fhir", name="fhir"))
        hub = run_batch(self.config("sqlite", source="hub", name="hub"))
        report = compare_reports(lis.csv, [fhir.csv, hub.csv], self.root / "comparison")
        same, changed = report["comparisons"]
        self.assertEqual(same["shared_groups"], 7)
        self.assertEqual(same["changed_known_frequencies"], 0)
        self.assertEqual(changed["shared_groups"], 6)
        self.assertEqual(changed["changed_known_frequencies"], 1)
        self.assertEqual(changed["added_or_changed_context"], 1)

    def test_unknown_frequencies_and_extrema_do_not_invent_statistics(self):
        path = self.config()
        path.write_text(path.read_text().replace('count="count"\n', ''))
        result = run_batch(path)
        self.assertTrue(all(row["value_mean"] == "" and row["value_se"] == "" for row in self.report_rows(result)))
        self.assertIsNone(json.loads(result.summary.read_text())["weighted_readiness"])
        path = self.config(name="bounds")
        path.write_text(path.read_text().replace('value="value"', 'value_min="value"'))
        result = run_batch(path)
        self.assertTrue(all(row["value_median"] == "" for row in self.report_rows(result)))

    def test_malformed_csv_fails_with_redacted_audit_and_no_partial_report(self):
        path = self.data / "lis.distinct.csv"
        path.write_text("code,code\nX,Y\n")
        with self.assertRaisesRegex(ValueError, "unique"):
            run_batch(self.config())
        logs = list(self.root.glob("*.failed.*.audit.jsonl"))
        self.assertEqual(len(logs), 1)
        self.assertIn('"phase": "failed"', logs[0].read_text())
        self.assertFalse((self.root / "review.csv").exists())
        self.assertFalse(list(self.root.glob("*.run.lock")))

    def test_reference_pack_gap_does_not_disable_known_cached_units(self):
        client = ReliableLookup(SnapshotUcum({"g/dL": True}), circuit_breaker=False)
        for unit in ("unknown1", "unknown2", "unknown3"):
            self.assertIsNone(client.is_valid(unit))
        self.assertTrue(client.is_valid("g/dL"))

    def test_failing_connected_lookup_opens_a_circuit_and_never_claims_invalid(self):
        class Down:
            def is_valid(self, unit):
                raise OSError("PRIVATE_PASSWORD")
        client = ReliableLookup(Down())
        for unit in ("a", "b", "c", "d"):
            self.assertIsNone(client.is_valid(unit))
        self.assertEqual(len(client.errors), 3)
        self.assertNotIn("PRIVATE_PASSWORD", json.dumps(client.errors))

    def test_conceptmap_is_only_a_verified_target_proposal_not_applied(self):
        result = run_batch(self.config())
        local = next(row for row in self.report_rows(result) if row["code"] == "LIS-HB")
        self.assertEqual(local["suggested_code"], "718-7")
        proposal = json.loads(local["coding_proposal_json"])[0]
        self.assertTrue(proposal["target_verified"] and proposal["review_required"])
        self.assertIsNone(proposal["transformation"])
        self.assertEqual(local["code_system"], "urn:example:lis-tests")

    def test_fhir_quantity_encoding_errors_and_duplicate_resource_ids_are_visible(self):
        result = run_batch(self.config("fhir_file"))
        malformed = next(row for row in self.report_rows(result) if row["n_invalid"] == "1")
        self.assertEqual(malformed["status"], "error")
        bundle = json.loads((self.data / "fhir.bundle.json").read_text())
        bundle["entry"].append(deepcopy(bundle["entry"][0]))
        (self.data / "fhir.bundle.json").write_text(json.dumps(bundle))
        with self.assertRaises(ValueError):
            run_batch(self.config("fhir_file", name="duplicates"))

    def test_mutated_report_is_not_silently_compared_or_verified(self):
        result = run_batch(self.config())
        result.csv.write_text(result.csv.read_text() + "\nCHANGED\n")
        with self.assertRaisesRegex(ValueError, "differs"):
            compare_reports(result.csv, [], self.root / "changed")
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_audit(result.log)

    def test_public_cloud_models_and_overwriting_inputs_are_rejected(self):
        p = self.config(extra='\n[models.local]\nbase_url="https://api.example.com/v1"\nmodel="cloud"\n')
        with self.assertRaisesRegex(ValueError, "local/LAN"):
            run_batch(p)
        p.write_text(p.read_text().replace('base_url="https://api.example.com/v1"', 'base_url_env="TEST_MODEL_ENDPOINT"'))
        with patch.dict("os.environ", {"TEST_MODEL_ENDPOINT": "https://api.example.com/v1"}), self.assertRaisesRegex(ValueError, "local/LAN"):
            run_batch(p)
        p = self.config()
        p.write_text(p.read_text().replace(str(self.root / "review.csv"), str(self.data / "lis.distinct.csv")))
        with self.assertRaisesRegex(ValueError, "overwrite"):
            run_batch(p)

    def test_frequency_quantiles_are_not_expanded_and_zero_is_preserved(self):
        stats = weighted_statistics([("0", 1000000000), ("10", 1)])
        self.assertEqual(Decimal(stats["median"]), 0)
        self.assertEqual(Decimal(stats["q3"]), 0)
        self.assertGreater(Decimal(stats["mean"]), 0)

    def test_multiple_reference_sets_and_modifiers_guard_grouping(self):
        resource = {"resourceType": "Observation", "status": "final", "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]},
                    "valueQuantity": {"value": 0, "unit": "g/dL", "code": "g/dL", "system": "http://unitsofmeasure.org"},
                    "referenceRange": [{"low": {"value": 0}}, {"high": {"value": 20}}]}
        changed = deepcopy(resource)
        changed["modifierExtension"] = [{"url": "urn:unknown:qualifier", "valueBoolean": True}]
        groups = ExportGroups(self.root, mode="distinct", group_value=False)
        try:
            groups.ingest([SourceRecord(resource, {}, "one"), SourceRecord(changed, {}, "two")])
            output = list(groups.groups())
            self.assertEqual(len(output), 2)
            self.assertTrue(all(stats["known_occurrences"] == 1 for _,_,stats,_ in output))
            self.assertTrue(all(stats["minimum"] == "0" for _,_,stats,_ in output))
        finally:
            groups.close()

    def test_installed_profile_snapshot_is_pinned_and_imports_selected_rules(self):
        profile = {"resourceType": "StructureDefinition", "type": "Observation", "status": "active", "url": "urn:profile:lab", "version": "1", "fhirVersion": "4.0.1",
            "snapshot": {"element": [{"path": "Observation.value[x].system", "fixedUri": "http://unitsofmeasure.org", "min": 1},
                                     {"path": "Observation.value[x].code", "min": 1}]}}
        path = self.root / "profile.json"
        path.write_text(json.dumps(profile))
        imported = import_structure_definition(path)
        rule = imported["element_rules"]["Observation.valueQuantity"]
        self.assertTrue(rule["require_code"] and rule["require_system"])
        self.assertEqual(rule["allowed_systems"], ["http://unitsofmeasure.org"])
        profile.pop("snapshot")
        path.write_text(json.dumps(profile))
        with self.assertRaisesRegex(ValueError, "resolved snapshot"):
            import_structure_definition(path)

    def test_partial_expansion_and_missing_edition_remain_unknown(self):
        path = self.root / "valueset.json"
        document = {"resourceType": "ValueSet", "url": "urn:vs", "version": "v1", "expansion": {
            "total": 2, "contains": [{"system": "urn:codes", "code": "A", "version": "2025"}]}}
        path.write_text(json.dumps(document))
        source = FhirArtifactSource(path)
        self.assertEqual(source.check_binding("urn:vs", "urn:codes", "A", "2025", "v1")["state"], "member")
        self.assertEqual(source.check_binding("urn:vs", "urn:codes", "B", "2025", "v1")["state"], "unknown")
        csvpath = self.root / "codes.csv"
        csvpath.write_text("system,code,version\nurn:codes,A,2025\n")
        self.assertEqual(CsvCodeSource(csvpath, complete=True).check("urn:codes", "A", "2026")["state"], "unknown")

    def test_snapshot_multiple_versions_cannot_pick_one_arbitrarily(self):
        source = SnapshotLoinc({"a": LoincTerm("718-7", "Hb", version="v1"), "b": LoincTerm("718-7", "Hb", version="v2")})
        self.assertIsNone(source.lookup("718-7"))
        self.assertEqual(source.lookup("718-7", "v2").version, "v2")

    def test_required_codeableconcept_binding_can_use_one_of_multiple_codings(self):
        class LocalTerminology:
            def check(self, system, code, version="", **kwargs):
                return {"state": "valid", "source": "synthetic", "metadata": {}, "errors": [], "candidates": []}
            def check_binding(self, binding, system, code, version="", binding_version=""):
                return {"state": "member" if system == "http://loinc.org" else "not_member", "source": "synthetic"}
        policy = InputProfile("test", element_rules={"Observation.code": ElementRule(binding="urn:vs", binding_strength="required", binding_version="1")})
        pipeline = FhirQualityPipeline(SnapshotUcum({"g/dL": True}), SnapshotLoinc({"hb": LoincTerm("718-7", "Hb", example_ucum_units=("g/dL",))}),
                                       profile=policy, terminology=LocalTerminology())
        report = pipeline.check({"resourceType": "Observation", "code": {"coding": [
            {"system": "http://loinc.org", "code": "718-7"}, {"system": "urn:lis", "code": "HB"}]},
            "valueQuantity": {"value": 14, "code": "g/dL", "system": "http://unitsofmeasure.org"}})
        self.assertTrue(all(check.get("concept_binding_satisfied") for check in report["measurements"][0]["code_checks"]))

    def test_profile_import_limitations_and_capture_are_auditable(self):
        profile = {"resourceType": "StructureDefinition", "type": "Observation", "status": "active", "url": "urn:profile:lab", "version": "1", "fhirVersion": "4.0.1",
            "snapshot": {"element": [{"path": "Observation.value[x]", "constraint": [{"key": "example-1", "expression": "value.exists()"}]}]}}
        path = self.root / "profile.json"
        path.write_text(json.dumps(profile))
        config = self.config()
        config.write_text(config.read_text().replace('PROFILE="fhir-r4-lab"', 'PROFILE="fhir-r4-lab"\nSTRUCTURE_DEFINITION=' + json.dumps(str(path)))
                          + 'lookup_pack=' + json.dumps(str(self.root / 'captured.json')) + '\n')
        result = run_batch(config)
        summary = json.loads(result.summary.read_text())
        self.assertEqual(summary["imported_profile"]["limitations"][0]["state"], "requires_full_fhir_validator")
        self.assertEqual(verify_audit(result.log)["status"], "verified")
        (self.root / 'captured.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, "differs"):
            verify_audit(result.log)

    def test_manifest_rejects_identifying_fields(self):
        manifest = self.root / "manifest.json"
        manifest.write_text('{"patient_ids": ["secret"]}')
        config = self.config()
        config.write_text(config.read_text().replace('[input]\n', '[input]\nmanifest=' + json.dumps(str(manifest)) + '\n'))
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            run_batch(config)
        self.assertNotIn("secret", next(self.root.glob('*.failed.*.audit.jsonl')).read_text())

    def test_disabled_lookup_apis_and_offline_configuration_probe_never_fall_through(self):
        from semantic_plausibility.deployment_checks import check_configuration
        config = self.config()
        config.write_text("\n".join(line for line in config.read_text().splitlines() if not line.startswith("snapshot=")))
        with patch("semantic_plausibility.observation_services._HttpSession.get", side_effect=AssertionError("No HTTP")):
            check_configuration(config, connections=True)
            config.write_text(config.read_text().replace('mode="offline"', 'mode="lookups"'))
            result = run_batch(config)
        rows = self.report_rows(result)
        self.assertTrue(all(row["unit_check"] == "unknown" for row in rows))

    def test_published_mii_laboratory_snapshot_matches_selected_rules_and_mapping_target(self):
        profile = ROOT / "examples/profiles/ObservationLab-2026.0.3.json"
        policy = import_structure_definition(profile)
        self.assertEqual(policy["guide_version"], "2026.0.3")
        self.assertEqual(policy["element_rules"]["Observation.code"]["binding_strength"], "preferred")
        self.assertEqual(policy["element_rules"]["Observation.valueQuantity"]["allowed_systems"], ["http://unitsofmeasure.org"])
        config = self.config()
        config.write_text(config.read_text().replace('PROFILE="fhir-r4-lab"', 'PROFILE="fhir-r4-lab"\nSTRUCTURE_DEFINITION=' + json.dumps(str(profile))))
        result = run_batch(config)
        self.assertTrue(all(row["ig_canonical"] == policy["canonical"] for row in self.report_rows(result)))
        self.assertTrue(all(json.loads(row["fhir_mapping_proposal_json"])["target_profile"] == policy["canonical"] for row in self.report_rows(result)))

    def test_numeric_encoding_budget_rejects_huge_exponents_without_expanding_them(self):
        from semantic_plausibility.fhir_quality_pipeline import number
        self.assertIsNone(number("1e1000000000"))
        self.assertIsNone(number("1e-1000000000"))
        self.assertIsNone(number("9" * 4097))
        self.assertEqual(number("1e-20"), Decimal("1e-20"))
        self.assertEqual(number("0"), 0)
        upper = number("1e1023")
        self.assertEqual(number(format(upper, "f")), upper)

    def test_comparison_accepts_its_own_wide_evidence_columns(self):
        path = self.data / "fhir.bundle.json"
        bundle = json.loads(path.read_text())
        bundle["entry"] = bundle["entry"][:1]
        bundle["entry"][0]["resource"]["code"]["text"] = "Synthetic source context " * 10000
        path.write_text(json.dumps(bundle))
        result = run_batch(self.config("fhir_file"))
        limit = csv.field_size_limit()
        comparison = compare_reports(result.csv, [result.csv], self.root / "wide-comparison")
        self.assertEqual(comparison["comparisons"][0]["shared_groups"], 1)
        self.assertEqual(csv.field_size_limit(), limit)
