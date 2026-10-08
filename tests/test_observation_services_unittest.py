"""Offline protocol tests for NLM, LOINC and PubMed adapters."""

import unittest
from decimal import Decimal


from semantic_plausibility.observation_services import (
    LoincFhirClient,
    NlmUcumClient,
    PublicEvidenceClient,
    _explicit_token,
)  # noqa: E402
from semantic_plausibility.observation_quality import LoincTerm  # noqa: E402


class Response:
    def __init__(self, text="", data=None, status=200, url="https://source.example/test"):
        self.text, self._data, self.status_code, self.url = text, data, status, url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class ServiceTests(unittest.TestCase):
    def test_source_tokens_allow_sentence_period_but_reject_larger_numbers_or_units(self):
        self.assertTrue(_explicit_token("16", "The reported value was 16."))
        self.assertTrue(_explicit_token("g/dL", "Patient values 12 to 16 g/dL."))
        self.assertFalse(_explicit_token("16", "116 or 16.5"))
        self.assertFalse(_explicit_token("g/dL", "mg/dL or g/dL/s"))

    def test_ucum_validation_and_factor_are_not_guessed(self):
        xml = (
            "<UCUMWebServiceResponse><Response><SourceQuantity>1</SourceQuantity>"
            "<TargetUnit>g/dL</TargetUnit><ResultQuantity>1</ResultQuantity>"
            "</Response></UCUMWebServiceResponse>"
        )
        session = Session(
            [
                Response(text="true"),
                Response(text=xml),
                Response(text=xml.replace("<ResultQuantity>1", "<ResultQuantity>0")),
                Response(status=400),
            ]
        )
        service = NlmUcumClient(session=session)
        self.assertTrue(service.is_valid("g/dl"))
        self.assertEqual(service.factor("g/dl", "g/dL"), Decimal("1"))
        self.assertIsNone(service.factor("g/dL", "s"))
        self.assertIn("/isValidUCUM/g/dl", session.calls[0][0])
        self.assertIn("/ucumtransform/1/from/g/dl/to/g/dL", session.calls[1][0])

    def test_loinc_semicolon_units_are_separate_examples(self):
        payload = {
            "parameter": [
                {"name": "display", "valueString": "Test"},
                {
                    "name": "property",
                    "part": [
                        {"name": "code", "valueCode": "EXAMPLE_UCUM_UNITS"},
                        {"name": "value", "valueString": "[in_us];cm;m"},
                    ],
                },
                {
                    "name": "property",
                    "part": [
                        {"name": "code", "valueCode": "SYSTEM"},
                        {"name": "value", "valueCoding": {"display": "Bld"}},
                    ],
                },
            ]
        }
        client = LoincFhirClient(session=Session([Response(data=payload)]))
        term = client.lookup("8302-2")
        self.assertEqual(term.example_ucum_units, ("[in_us]", "cm", "m"))
        self.assertEqual(term.system, "Bld")
        self.assertIs(term, client.lookup("8302-2"))

    def test_pubmed_studies_without_llm_have_no_numeric_bounds(self):
        xml = (
            "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>"
            "<Article><ArticleTitle>Example study</ArticleTitle><Abstract>"
            "<AbstractText>A reported interval.</AbstractText></Abstract></Article>"
            "</MedlineCitation></PubmedArticle></PubmedArticleSet>"
        )
        responses = []
        for _ in range(3):
            responses += [Response(data={"esearchresult": {"idlist": ["123"]}}), Response(text=xml)]
        research = PublicEvidenceClient(session=Session(responses))
        term = LoincTerm("718-7", "Hemoglobin", component="Hemoglobin", system="Blood")
        evidence = research.search_studies(term)
        self.assertEqual(
            {e.evidence_type for e in evidence},
            {"reference_interval", "analytical_interval", "documented_extreme"},
        )
        self.assertTrue(all(e.low is None and e.high is None for e in evidence))
        self.assertTrue(all(e.status == "candidate" for e in evidence))

    def test_llm_cannot_add_bounds_missing_from_abstract(self):
        class Llm:
            def json(self, instruction, evidence):
                return {
                    "low": "12",
                    "high": "16",
                    "unit": "g/dL",
                    "excerpt": "A reported interval.",
                    "population": "adults",
                }

        research = PublicEvidenceClient(llm=Llm())
        records = [
            {
                "pmid": "123",
                "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
                "title": "Example",
                "abstract": "A reported interval.",
            }
        ]
        self.assertEqual(
            research._extract_study_candidates(
                "reference_interval", records, LoincTerm("718-7", "Hemoglobin")
            ),
            [],
        )

    def test_search_bucket_does_not_make_patient_values_a_reference_interval(self):
        class Llm:
            def json(self, instruction, evidence):
                return {"low": "12", "high": "16", "unit": "g/dL", "excerpt": "Patient values 12 to 16 g/dL."}

        records = [
            {
                "pmid": "123",
                "url": "https://pubmed.ncbi.nlm.nih.gov/123/",
                "title": "Example",
                "abstract": "Patient values 12 to 16 g/dL.",
            }
        ]
        found = PublicEvidenceClient(llm=Llm())._extract_study_candidates(
            "reference_interval", records, LoincTerm("718-7", "Hemoglobin")
        )
        self.assertEqual(found[0].evidence_type, "unclassified_numeric_range")

    def test_loinc_empty_or_operation_outcome_cannot_prove_code_membership(self):
        for payload in (
            {},
            {"resourceType": "OperationOutcome", "issue": []},
            {"parameter": [{"name": "version", "valueString": "2.80"}]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                LoincFhirClient(session=Session([Response(data=payload)])).lookup("718-7")


if __name__ == "__main__":
    unittest.main()
