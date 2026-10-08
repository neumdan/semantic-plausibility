"""Executable case studies and semantic regressions; no public network needed."""

import json
from pathlib import Path
import tempfile
import unittest
from kernel_cases import assess_groups
from unittest.mock import patch

from semantic_plausibility.audit import Checker, SourceRecord, file_records, payload_records
from semantic_plausibility.observation_quality import UnitEvidence
from semantic_plausibility.profile_config import InputProfile, ElementRule, UCUM, load_profile, element_rule
from semantic_plausibility.terminology_router import TerminologyRouter

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests/fixtures"


def laboratory(value=14, unit="g/dL", identifier="a"):
    return {
        "resourceType": "Observation",
        "id": identifier,
        "subject": {"reference": "Patient/NEVER_EXPORT"},
        "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]},
        "valueQuantity": {"value": value, "code": unit, "unit": unit, "system": UCUM},
        "referenceRange": [
            {
                "low": {"value": 12, "code": "g/dL", "system": UCUM},
                "high": {"value": 16, "code": "g/dL", "system": UCUM},
            }
        ],
    }


def record(value=14, unit="g/dL", identifier="a", context=None):
    return SourceRecord(
        laboratory(value, unit, identifier),
        context or {"loinc_system": "Bld", "source_test": "HB"},
        identifier,
    )


class AuditTests(unittest.TestCase):
    def check(self, records, *, mode="tuples", links=False, checker=None):
        return assess_groups(
            records, checker or Checker(snapshot=EXAMPLES / "evidence.synthetic.json"), mode=mode
        )

    def test_five_rows_are_four_tuples_with_explanations_and_candidate_factor_separation(self):
        summary, rows, _, _ = self.check(
            file_records(EXAMPLES / "laboratory.csv", EXAMPLES / "laboratory.columns.json")
        )
        self.assertEqual(summary["input_records"], 5)
        self.assertEqual(summary["assessed_items"], 4)
        self.assertEqual(summary["statuses"], {"passed": 1, "suggestion": 2, "unclear": 1})
        cases = {row["einheiten_code"]: row for row in rows}
        self.assertEqual(cases["g/dL"]["werte_min"], "14")
        self.assertEqual(cases["g/dL"]["werte_max"], "18.5")
        self.assertEqual(cases["g/dL"]["haeufigkeit"], "2")
        self.assertEqual(cases["g/dl"]["vorschlag_faktor"], "1")
        self.assertEqual(cases["g/L"]["vorschlag_faktor"], "0.1")
        self.assertEqual(cases["10^6"]["vorschlag_grundlage"], "first_example_candidate")
        self.assertEqual(cases["10^6"]["vorschlag_faktor"], "")
        self.assertTrue(all(row["begruendung"] and row["status_text"] for row in rows))

    def test_outside_local_reference_is_not_a_data_error_or_patient_interpretation(self):
        _, rows, evidence, _ = self.check([record(14), record(500, identifier="b")])
        self.assertEqual(rows[0]["status"], "passed")
        self.assertEqual(rows[0]["werte_max"], "500")
        self.assertEqual(rows[0]["referenz_fachlich"], "offen")
        self.assertEqual(evidence[0]["check"]["values"]["local_reference"][0]["maximum_relation"], "above")
        self.assertEqual(
            evidence[0]["check"]["values"]["local_reference"][0]["clinical_classification"], "not_assessed"
        )

    def test_distinct_tuple_keeps_system_version_population_and_method_separate(self):
        records = [record() for _ in range(5)]
        records[1].resource["code"]["coding"][0]["version"] = "2.80"
        records[2].resource["valueQuantity"]["system"] = "https://example.org/CodeSystem/units"
        records[3].resource["referenceRange"][0]["appliesTo"] = [
            {"coding": [{"system": "urn:population", "code": "adult"}]}
        ]
        records[4].context["method"] = "photometry"
        _, rows, _, _ = self.check(records)
        self.assertEqual(len(rows), 5)

    def test_reference_bounds_reversed_is_error_and_nonfinite_values_are_errors(self):
        for value in (True, "NaN", "Infinity", "not numeric"):
            with self.subTest(value=value):
                _, rows, _, _ = self.check([record(value)])
                self.assertEqual(rows[0]["status"], "error")
                self.assertEqual(rows[0]["ungueltige_werte"], "1")
        bad = record()
        bad.resource["referenceRange"][0]["low"]["value"] = 20
        _, rows, _, _ = self.check([bad])
        self.assertEqual(rows[0]["referenz_pruefung"], "Grenzen fehlerhaft")

    def test_decimal_precision_is_preserved_and_missing_values_are_not_fabricated(self):
        _, rows, _, _ = self.check(
            [record("14.0000000000000001"), record("14.0000000000000002"), record(None)]
        )
        self.assertEqual(rows[0]["werte_min"], "14.0000000000000001")
        self.assertEqual(rows[0]["werte_max"], "14.0000000000000002")
        self.assertEqual(rows[0]["fehlende_werte"], "1")

    def test_preaggregated_value_extrema_keep_source_weights(self):
        source = record(None)
        source.aggregate_values, source.value_min, source.value_max, source.occurrences = (
            True,
            "9.5",
            "24",
            500,
        )
        _, rows, _, _ = self.check([source])
        self.assertEqual(rows[0]["haeufigkeit"], "500")
        self.assertEqual(rows[0]["werte_min"], "9.5")
        self.assertEqual(rows[0]["werte_max"], "24")
        self.assertEqual(rows[0]["numerische_werte"], "2")
        self.assertEqual(rows[0]["werte_pruefumfang"], "gelieferte Min/Max-Grenzen")
        source.value_min = "25"
        _, rows, _, _ = self.check([source])
        self.assertEqual(rows[0]["status"], "error")

    def test_reference_qualifiers_checked_without_choosing_a_patient_population(self):
        source = record()
        source.resource["referenceRange"][0]["appliesTo"] = [{"text": "adults"}]
        _, rows, evidence, _ = self.check([source])
        self.assertEqual(rows[0]["referenz_pruefung"], "Grenzen konsistent")
        self.assertTrue(evidence[0]["reference_metadata"][0]["population_qualified"])
        self.assertEqual(rows[0]["referenz_fachlich"], "offen")

    def test_undeclared_text_unit_is_a_candidate_not_valid_declared_ucum(self):
        source = record()
        source.resource["valueQuantity"].pop("system")
        source.resource["valueQuantity"].pop("code")
        _, rows, evidence, _ = self.check([source])
        self.assertEqual(rows[0]["einheit_pruefung"], "unklar")
        self.assertTrue(evidence[0]["raw_unit_ucum_candidate"])
        self.assertNotEqual(rows[0]["status"], "passed")
        self.assertEqual(rows[0]["vorschlag_grundlage"], "raw_unit_ucum_candidate")
        self.assertTrue(rows[0]["vorschlag"])

    def test_declared_display_and_coded_unit_with_different_scale_are_not_passed(self):
        source = record(unit="g/dL")
        source.resource["valueQuantity"]["unit"] = "g/L"
        _, rows, _, _ = self.check([source])
        self.assertEqual(rows[0]["status"], "unclear")
        self.assertIn("Text-Einheit", rows[0]["begruendung"])

    def test_offline_mode_ignores_live_environment_and_rejects_network_source(self):
        with (
            patch.dict("os.environ", {"TX_FHIR_BASE_URL": "https://should-not-be-called.example"}),
            patch("semantic_plausibility.audit.router_from_environment") as live_router,
        ):
            checker = Checker(snapshot=EXAMPLES / "evidence.synthetic.json")
            self.assertIsNone(checker.terminology)
            live_router.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps([{"kind": "fhir", "base_url": "https://example.org"}]))
            with self.assertRaisesRegex(ValueError, "local CSV/SQLite"):
                Checker(snapshot=EXAMPLES / "evidence.synthetic.json", terminology_sources=path)

    def test_evidence_has_one_audit_status_without_contradictory_unused_decisions(self):
        _, rows, evidence, _ = self.check([record()])
        self.assertEqual(rows[0]["status"], "passed")
        self.assertNotIn("decision", evidence[0]["report"])
        self.assertNotIn("decision", evidence[0]["report"]["measurements"][0])

    def test_material_mismatch_and_unresolved_context_are_visible(self):
        _, rows, _, _ = self.check([record(context={"loinc_system": "Ser"})])
        self.assertEqual(rows[0]["material_pruefung"], "abweichend")
        self.assertEqual(rows[0]["status"], "error")
        source = record()
        source.context = {}
        _, rows, _, _ = self.check([source])
        self.assertEqual(rows[0]["status"], "unclear")

    def test_custom_policy_and_fhir_fallback_do_not_claim_fhir_validation(self):
        checker = Checker(
            snapshot=EXAMPLES / "evidence.synthetic.json", profile_config=EXAMPLES / "local-lab.policy.json"
        )
        summary, rows, evidence, _ = self.check([record()], checker=checker)
        self.assertEqual(summary["full_fhir_validation"], "out_of_scope")
        self.assertEqual(rows[0]["status"], "passed")
        self.assertEqual(evidence[0]["report"]["profile"]["state"], "policy_selected")
        policy = load_profile("local-lab", EXAMPLES / "local-lab.policy.json")
        self.assertEqual(element_rule(policy, "Observation.referenceRange.low")[1], "FHIR R4")

    def test_component_policy_is_applied_to_component_not_parent(self):
        checker = Checker(snapshot=EXAMPLES / "evidence.synthetic.json")
        checker.custom = InputProfile(
            "component-excluded",
            element_rules={"Observation.component.valueQuantity": ElementRule(prohibited=True)},
        )
        resource = {"resourceType": "Observation", "component": [laboratory()]}
        _, rows, _, _ = self.check([SourceRecord(resource, {"loinc_system": "Bld"}, "one")], checker=checker)
        self.assertEqual(rows[0]["status"], "error")
        self.assertIn("element_excluded_by_guide", rows[0]["begruendung"])

    def test_source_configuration_resolves_local_catalog_relative_to_config(self):
        router = TerminologyRouter.from_file(EXAMPLES / "terminology.sources.json")
        try:
            self.assertEqual(router.check("https://example.org/CodeSystem/lis", "HB", "1")["state"], "valid")
            self.assertEqual(
                router.check("https://example.org/CodeSystem/lis", "absent", "1")["state"], "unknown"
            )
        finally:
            router.close()

    def test_non_numeric_observation_is_explicitly_outside_measurement_scope(self):
        source = laboratory()
        source.pop("valueQuantity")
        source.pop("referenceRange")
        source["code"] = {"text": "qualitative screen"}
        source["valueCodeableConcept"] = {"text": "negative"}
        _, rows, _, _ = self.check(payload_records(source))
        self.assertEqual(rows[0]["status"], "not_applicable")

    def test_comparator_keeps_threshold_semantics_separate(self):
        first, second = record(5), record(5)
        first.resource["valueQuantity"]["comparator"] = "<"
        _, rows, _, _ = self.check([first, second])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"passed", "unclear"})

    def test_missing_inputs_and_invalid_policy_are_configuration_errors(self):
        with self.assertRaisesRegex(ValueError, "offline"):
            Checker(snapshot=EXAMPLES / "evidence.synthetic.json", research=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps({"name": "bad", "fhir_version": "5.0.0"}))
            with self.assertRaisesRegex(ValueError, "FHIR R4"):
                load_profile("bad", path)

    def test_versioned_loinc_metadata_is_not_invented_by_unversioned_snapshot(self):
        source = record()
        source.resource["code"]["coding"][0]["version"] = "2.80"
        _, rows, _, _ = self.check([source])
        self.assertEqual(rows[0]["code_pruefung"], "unklar")
        self.assertEqual(rows[0]["status"], "unclear")

    def test_website_fallback_still_runs_when_authoritative_loinc_is_unavailable(self):
        class NoLoinc:
            def lookup(self, code):
                raise RuntimeError("API down")

        class Research:
            called = False

            def website_units(self, code, term):
                self.called = True
                return [
                    UnitEvidence(
                        "g/dL", "website_example", "https://loinc.org/718-7/", "explicit synthetic page unit"
                    )
                ]

            def suggest_units(self, unit, term, examples):
                return []

            def website_ranges(self, code, term):
                return []

        research = Research()
        offline = Checker(snapshot=EXAMPLES / "evidence.synthetic.json")
        checker = Checker(ucum=offline.ucum, loinc=NoLoinc(), evidence=research, research=True)
        _, rows, _, _ = self.check([record()], checker=checker)
        self.assertTrue(research.called)
        self.assertEqual(rows[0]["code_pruefung"], "unklar")
        self.assertEqual(rows[0]["vorschlag_grundlage"], "website_example_candidate")
        self.assertNotEqual(rows[0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
