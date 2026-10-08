"""Offline end-to-end decisions for the compact FHIR quality path."""

import unittest
from decimal import Decimal


from semantic_plausibility import fhir_quality_pipeline as compact  # noqa: E402
from semantic_plausibility.observation_quality import (
    ApprovedMapping,
    LoincTerm,
    ResearchCandidate,
    UnitEvidence,
)  # noqa: E402
from semantic_plausibility.observation_services import LoincFhirClient, NlmUcumClient  # noqa: E402


class Ucum:
    def __init__(self, valid=("g/dL", "g/dl", "g/L"), factors=None):
        self.valid = set(valid)
        self.factors = factors or {
            ("g/dl", "g/dL"): Decimal("1"),
            ("g/dl", "g/L"): Decimal("10"),
            ("g/L", "g/dL"): Decimal("0.1"),
        }

    def is_valid(self, unit):
        return unit in self.valid

    def factor(self, source, target):
        return Decimal("1") if source == target else self.factors.get((source, target))


class Loinc:
    def __init__(self, units=("g/dL",), system="Bld", scale="Qn"):
        self.term = LoincTerm(
            "718-7",
            "Hemoglobin",
            system=system,
            scale=scale,
            example_ucum_units=units,
            source="LOINC fixture",
        )

    def lookup(self, code):
        return self.term if code == self.term.code else None


class Research:
    def __init__(self):
        self.calls = []

    def website_units(self, code, term):
        self.calls.append("website")
        raise RuntimeError("site offline")

    def suggest_units(self, unit, term, examples):
        self.calls.append("llm")
        return [UnitEvidence("g/dL", "llm_candidate", "local gpt-oss")]

    def search_studies(self, term):
        self.calls.append("pubmed")
        return [
            ResearchCandidate(
                "reference_interval",
                "https://pubmed.ncbi.nlm.nih.gov/123/",
                low="12",
                high="16",
                unit="g/dL",
                excerpt="synthetic",
            )
        ]


def observation(unit="g/dL", value=14, loinc="718-7"):
    def bound(number):
        return {"value": number, "code": unit, "unit": unit, "system": compact.UCUM}

    return {
        "resourceType": "Observation",
        "id": "synthetic-patient-1",
        "code": {"coding": [{"system": compact.LOINC, "code": loinc}]},
        "valueQuantity": {"value": value, "code": unit, "unit": unit, "system": compact.UCUM},
        "referenceRange": [{"low": bound(12), "high": bound(16)}],
    }


class PipelineTest(unittest.TestCase):
    def test_real_adapters_parse_official_response_shapes_together(self):
        class Response:
            status_code = 200
            url = "https://fixture.example/lookup"

            def __init__(self, text="", payload=None):
                self.text, self.payload = text, payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        class Session:
            def get(self, url, **kwargs):
                if "isValidUCUM" in url:
                    return Response("true")
                return Response(
                    payload={
                        "parameter": [
                            {"name": "display", "valueString": "Hemoglobin"},
                            *(
                                {
                                    "name": "property",
                                    "part": [
                                        {"name": "code", "valueCode": key},
                                        {"name": "value", "valueString": value},
                                    ],
                                }
                                for key, value in (
                                    ("SYSTEM", "Bld"),
                                    ("SCALE_TYP", "Qn"),
                                    ("EXAMPLE_UCUM_UNITS", "g/dL"),
                                )
                            ),
                        ]
                    }
                )

        session = Session()
        pipeline = compact.FhirQualityPipeline(
            NlmUcumClient(session=session), LoincFhirClient(session=session)
        )
        report = pipeline.check(observation(), loinc_system="Bld")
        self.assertEqual(report["decision"], "screen_supported")
        self.assertEqual(report["measurements"][0]["loinc_examples"], ["g/dL"])

    def test_outside_interval_is_not_unit_error(self):
        source = observation(value=20)
        report = compact.FhirQualityPipeline(Ucum(), Loinc()).check(source, loinc_system="Bld")
        result = report["measurements"][0]
        self.assertEqual(result["mapping"]["class"], "correct")
        self.assertEqual(result["local_interval"]["state"], "above")
        self.assertEqual(result["decision"], "screen_supported")
        self.assertEqual(source["valueQuantity"]["value"], 20)

    def test_normalization_requires_local_approval(self):
        source = observation("g/dl")
        result = compact.FhirQualityPipeline(Ucum(), Loinc()).check(source, loinc_system="Bld")[
            "measurements"
        ][0]
        self.assertEqual(result["mapping"]["class"], "normalization_verified")
        self.assertFalse(result["mapping"]["approved"])
        self.assertEqual(result["decision"], "needs_review")
        approval = ApprovedMapping(
            "HB_LOCAL", "718-7", "g/dl", "g/dL", Decimal("1"), "Bld", "", "Dr Lab", "2026-09-28", "SOP-42"
        )
        pipeline = compact.FhirQualityPipeline(Ucum(), Loinc(), approvals=[approval])
        result = pipeline.check(source, source_test="HB_LOCAL", loinc_system="Bld")["measurements"][0]
        self.assertTrue(result["mapping"]["approved"])
        self.assertEqual(result["decision"], "screen_supported")
        self.assertEqual(source["valueQuantity"]["code"], "g/dl")

    def test_multiple_examples_do_not_trigger_first_example_fallback(self):
        pipeline = compact.FhirQualityPipeline(Ucum(), Loinc(("g/dL", "g/L")))
        result = pipeline.check(observation("g/dl"), loinc_system="Bld")["measurements"][0]
        self.assertEqual(result["mapping"]["class"], "needs_refinement")
        self.assertEqual(len(result["mapping"]["candidates"]), 2)
        self.assertIsNone(result["mapping"]["target"])

    def test_context_and_scale_are_part_of_the_screen(self):
        wrong_system = compact.FhirQualityPipeline(Ucum(), Loinc()).check(
            observation(), loinc_system="Ser/Plas"
        )["measurements"][0]
        self.assertEqual(wrong_system["system_fit"], "mismatch")
        self.assertEqual(wrong_system["decision"], "needs_review")
        wrong_scale = compact.FhirQualityPipeline(Ucum(), Loinc(scale="Ord")).check(
            observation(), loinc_system="Bld"
        )["measurements"][0]
        self.assertEqual(wrong_scale["scale_fit"], "mismatch")
        self.assertEqual(wrong_scale["decision"], "needs_review")

    def test_reference_bounds_are_checked_without_a_value(self):
        source = observation(value=None)
        source["referenceRange"][0]["low"]["value"] = 18
        result = compact.FhirQualityPipeline(Ucum(), Loinc()).check(source, loinc_system="Bld")[
            "measurements"
        ][0]
        self.assertEqual(result["value_state"], "missing")
        self.assertEqual(result["local_interval"]["state"], "invalid_bounds")
        self.assertEqual(result["decision"], "needs_review")

    def test_reference_bound_requires_ucum_system(self):
        source = observation()
        del source["referenceRange"][0]["low"]["system"]
        result = compact.FhirQualityPipeline(Ucum(), Loinc()).check(source, loinc_system="Bld")[
            "measurements"
        ][0]
        self.assertEqual(result["local_interval"]["state"], "unit_unresolved")

    def test_research_candidates_cannot_change_decision(self):
        research = Research()
        pipeline = compact.FhirQualityPipeline(Ucum(), Loinc(), research)
        result = pipeline.check(observation("g/dll"), loinc_system="Bld", research=True)["measurements"][0]
        self.assertEqual(research.calls, ["website", "llm", "pubmed"])
        self.assertEqual(result["mapping"]["class"], "needs_refinement")
        self.assertEqual(result["decision"], "needs_review")
        self.assertEqual(result["unit_candidates"][0]["status"], "candidate_only")
        self.assertEqual(result["study_candidates"][0]["status"], "candidate")
        self.assertEqual(result["study_comparisons"], [])
        self.assertIn("LOINC website unavailable", result["errors"][0])
        supported = pipeline.check(observation(), loinc_system="Bld", research=True)["measurements"][0]
        self.assertEqual(
            supported["study_comparisons"][0]["status"], "candidate_only_context_and_method_unverified"
        )

    def test_ucum_outage_is_unresolved(self):
        class DownUcum(Ucum):
            def factor(self, source, target):
                raise RuntimeError("offline")

        result = compact.FhirQualityPipeline(DownUcum(), Loinc()).check(
            observation("g/dl"), loinc_system="Bld"
        )["measurements"][0]
        self.assertEqual(result["mapping"]["class"], "unresolved")
        self.assertEqual(result["decision"], "needs_review")


if __name__ == "__main__":
    unittest.main()
