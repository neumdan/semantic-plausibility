"""Output contract: unit plans, extrema flags, bounded research and database reports."""

from pathlib import Path
import unittest
from kernel_cases import assess_groups
from unittest.mock import patch

from semantic_plausibility.audit import Checker, SourceRecord, offline_services
from semantic_plausibility.db_source import build_query
from semantic_plausibility.observation_quality import LoincTerm, ResearchCandidate
from semantic_plausibility.observation_services import PublicEvidenceClient

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests/fixtures"
UCUM = "http://unitsofmeasure.org"


def observation(value=14, unit="g/dL"):
    return {
        "resourceType": "Observation",
        "code": {"coding": [{"system": "http://loinc.org", "code": "718-7"}]},
        "valueQuantity": {"value": value, "code": unit, "unit": unit, "system": UCUM},
        "referenceRange": [
            {
                "low": {"value": 12, "code": "g/dL", "system": UCUM},
                "high": {"value": 16, "code": "g/dL", "system": UCUM},
            }
        ],
    }


class ContractTests(unittest.TestCase):
    def run_resources(self, resources, **kwargs):
        from semantic_plausibility.audit import payload_records

        records = payload_records(resources, context={"loinc_system": "Bld"})
        summary, rows, evidence, _ = assess_groups(
            records, Checker(snapshot=EXAMPLES / "evidence.synthetic.json", **kwargs)
        )
        return summary, rows, evidence

    def test_value_and_reference_bounds_get_their_own_factors(self):
        _, rows, evidence = self.run_resources(observation(140, "g/L"))
        plan = evidence[0]["recommendation"]["transformation"]
        self.assertEqual(plan["factor"], "0.1")
        self.assertEqual([op["factor"] for op in plan["operations"]], ["0.1", "1", "1"])
        self.assertEqual(plan["operations"][1]["converted_bound"], "12")
        self.assertIn("valueQuantity: y = x * 0.1", rows[0]["vorschlag_transformation"])
        self.assertIn("referenceRange[0].low: y = x * 1", rows[0]["vorschlag_transformation"])

    def test_ambiguous_unit_never_gets_an_executable_factor(self):
        _, rows, evidence = self.run_resources(observation(14, "10^6"))
        plan = evidence[0]["recommendation"]["transformation"]
        self.assertEqual(plan["state"], "unresolved")
        self.assertIsNone(plan["factor"])
        self.assertIsNone(plan["formula"])
        self.assertIn("Faktor ungeklärt", rows[0]["vorschlag_transformation"])

    def test_reference_with_other_system_is_not_silently_transformed(self):
        resource = observation(140, "g/L")
        resource["referenceRange"][0]["low"]["system"] = "https://example.org/unit"
        _, _, evidence = self.run_resources(resource)
        plan = evidence[0]["recommendation"]["transformation"]
        self.assertIsNone(plan["operations"][1]["factor"])
        self.assertEqual(plan["state"], "unresolved")

    def test_both_extrema_are_flagged_without_turning_an_abnormal_value_into_data_error(self):
        summary, rows, evidence = self.run_resources([observation(14), observation(30)])
        self.assertEqual(summary["assessed_items"], 1)
        self.assertEqual(rows[0]["status"], "passed")
        check = evidence[0]["check"]["values"]["local_reference"][0]
        self.assertEqual(check["minimum_relation"], "inside")
        self.assertEqual(check["maximum_relation"], "above")
        self.assertIn("außerhalb", rows[0]["werte_referenz_pruefung"])

    def test_censored_values_are_not_compared_as_exact_values(self):
        resource = observation(30)
        resource["valueQuantity"]["comparator"] = "<"
        _, rows, evidence = self.run_resources(resource)
        self.assertEqual(evidence[0]["check"]["values"]["local_reference"][0]["state"], "unresolved")
        self.assertEqual(rows[0]["werte_referenz_pruefung"], "nicht beurteilbar")

    def test_multiple_reference_contexts_do_not_claim_a_single_applicable_interval(self):
        resource = observation(18)
        resource["referenceRange"].append(
            {
                "low": {"value": 15, "code": "g/dL", "system": UCUM},
                "high": {"value": 20, "code": "g/dL", "system": UCUM},
            }
        )
        _, rows, evidence = self.run_resources(resource)
        contexts = evidence[0]["check"]["values"]["local_reference"]
        self.assertEqual([c["state"] for c in contexts], ["outside_reported_range", "inside_reported_range"])
        self.assertIn("mindestens eines", rows[0]["werte_referenz_pruefung"])
        self.assertIn("Kontextzuordnung offen", rows[0]["werte_referenz_pruefung"])

    def test_evidence_flags_maximum_and_disjoint_ranges_but_keeps_context_unverified(self):
        class Evidence:
            def website_units(self, *args):
                return []

            def suggest_units(self, *args):
                return []

            def search_studies(self, *args):
                return [
                    ResearchCandidate("analytical_interval", "synthetic:analytical", "1", "20", "g/dL"),
                    ResearchCandidate("reference_interval", "synthetic:reference", "1", "5", "g/dL"),
                    ResearchCandidate("documented_extreme", "synthetic:case", "1", "18", "g/dL"),
                ]

        ucum, loinc = offline_services(EXAMPLES / "evidence.synthetic.json")
        checker = Checker(ucum=ucum, loinc=loinc, evidence=Evidence(), research=True)
        _, rows, evidence, _ = assess_groups(
            [SourceRecord(observation(v), {"loinc_system": "Bld"}, "synthetic") for v in [14, 30]], checker
        )
        checks = evidence[0]["check"]["values"]["evidence"]
        self.assertEqual(checks[0]["maximum_relation"], "above")
        self.assertEqual(checks[0]["state"], "potential_implausibility_context_unverified")
        self.assertFalse(checks[0]["context_verified"])
        self.assertEqual(checks[1]["reference_relation"], "disjoint_context_unverified")
        self.assertEqual(checks[2]["state"], "outside_documented_example")
        self.assertIn("möglicherweise unplausibel", rows[0]["werte_evidenz_pruefung"])
        self.assertIn("überlappen nicht", rows[0]["referenz_evidenz_pruefung"])
        self.assertNotEqual(rows[0]["status"], "error")

    def test_lookup_scope_never_requests_pubmed(self):
        client = PublicEvidenceClient(scope="lookup")
        with patch.object(client, "_ncbi_get", side_effect=AssertionError("PubMed forbidden")):
            self.assertEqual(client.search_studies(LoincTerm("718-7", "Hemoglobin")), [])

    def test_mixed_jsonb_resource_table_filters_the_declared_resource_type(self):
        mapping = {
            "resource_type": "Observation",
            "relation": "audit.fhir_resources",
            "resource_type_column": "resource_type",
            "fields": {"resource_json": "resource"},
        }
        query, params = build_query(mapping)
        self.assertIn('"resource_type" = %s', query.as_string())
        self.assertEqual(params, ["Observation"])
