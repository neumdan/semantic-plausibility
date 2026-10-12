"""Real retrieval/model checks; never simulate terminology or model replies."""

import os
import unittest

from semantic_plausibility.fhir_quality_pipeline import FhirQualityPipeline
from semantic_plausibility.laboratory_suggestions import LaboratorySuggestions
from semantic_plausibility.observation_services import (
    LoincFhirClient, NlmUcumClient, OpenAICompatibleJsonClient, PublicEvidenceClient,
)
from semantic_plausibility.terminology_router import TerminologyRouter


@unittest.skipUnless(os.getenv("SQC_LIVE_TESTS") == "1", "Set SQC_LIVE_TESTS=1 for real terminology requests")
class CandidateTests(unittest.TestCase):
    def test_name_candidates_need_exact_identity_and_independent_unit_fit(self):
        router = TerminologyRouter([{"kind": "ols", "systems": ["http://loinc.org"],
            "base_url": "https://semanticlookup.zbmed.de/ols/api", "ontology": "loinc", "limit": 5}])
        try:
            pipeline = FhirQualityPipeline(NlmUcumClient(), LoincFhirClient("https://tx.fhir.org/r4"), terminology=router)
            result = LaboratorySuggestions(None).assess({"text": "Hemoglobin"},
                {"code": "g/dL", "system": "http://unitsofmeasure.org"}, None, pipeline, specimen="Bld")
            self.assertTrue(result["coding_candidates"])
            for row in result["coding_candidates"]:
                self.assertTrue(row["target_verified"])
                self.assertTrue(row["version"])
                self.assertTrue(row["source"].startswith("https://tx.fhir.org/"))
                self.assertNotIn("value", row)
                if row["selectable"]:
                    self.assertIn(row["unit_fit"]["class"], {"correct", "normalization_verified", "conversion_verified"})
                    self.assertEqual(row["context_conflicts"], [])
            self.assertIsNone(result["selected_code"])
            self.assertFalse(result["automatic_changes"])
        finally:
            router.close()


@unittest.skipUnless(os.getenv("SQC_LIVE_RESEARCH_TESTS") == "1", "Set SQC_LIVE_RESEARCH_TESTS=1 for actual LOINC/PubMed retrieval")
class ResearchRetrievalTests(unittest.TestCase):
    def test_canonical_loinc_page_and_retrieval_hash(self):
        client = PublicEvidenceClient(scope="lookup")
        try:
            text = client._website_text("718-7")
        except RuntimeError as exc:
            if "HTTP 403" not in str(exc):
                raise
            self.assertEqual(client.source_records["unavailable:718-7"]["state"], "unavailable")
            self.skipTest("Real LOINC website refuses automated access (403); exact FHIR lookup remains tested")
        self.assertIn("Example Units", text)
        self.assertIn("g/dL", text)
        records = list(client.source_records.values())
        self.assertEqual(records[0]["source"], "https://loinc.org/718-7")
        self.assertEqual(len(records[0]["sha256"]), 64)

    def test_pubmed_retrieval_records_actual_articles(self):
        client = PublicEvidenceClient(pubmed_retmax=1)
        ids = client._pubmed_ids("hemoglobin reference interval")
        self.assertTrue(ids)
        records = client._pubmed_records(ids)
        self.assertTrue(records)
        self.assertIn(records[0]["pmid"], ids)
        self.assertTrue(records[0]["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/"))
        self.assertTrue(client.source_records)

    def test_europe_pmc_retrieval_has_actual_abstract_and_query_trace(self):
        client = PublicEvidenceClient(europe_pmc_retmax=1, research_sources=("europe_pmc",))
        records = client._europe_pmc_records("hemoglobin reference interval")
        self.assertTrue(records)
        self.assertTrue(records[0]["url"].startswith("https://europepmc.org/article/"))
        self.assertTrue(records[0]["abstract"])
        self.assertEqual(client.query_log[0]["source"], "europe_pmc")
        self.assertTrue(client.source_records)

    def test_unmapped_source_unit_display_is_a_verified_candidate_only(self):
        pipeline = FhirQualityPipeline(NlmUcumClient(), LoincFhirClient("https://tx.fhir.org/r4"))
        term = pipeline.loinc.lookup("718-7")
        result = LaboratorySuggestions(None).assess({"text": "Hemoglobin"},
            {"code": "GDL", "unit": "g/dL", "system": "https://example.org/lis/units"}, term, pipeline, specimen="Bld")
        self.assertTrue(result["unit_interpretations"])
        self.assertFalse(result["unit_interpretations"][0]["source_mapping_verified"])
        candidate = result["coding_candidates"][0]
        self.assertTrue(candidate["selectable"])
        self.assertIsNone(candidate["unit_fit"]["factor_from_original"])
        self.assertIsNone(result["selected_code"])


@unittest.skipUnless(os.getenv("SQC_LIVE_MODEL_TESTS") == "1", "Start a real local model and set SQC_LIVE_MODEL_TESTS=1")
class LocalModelTests(unittest.TestCase):
    def test_real_model_can_only_select_sourced_name_and_code(self):
        llm = OpenAICompatibleJsonClient(
            base_url=os.environ["SQC_TEST_MODEL_BASE_URL"], model=os.environ["SQC_TEST_MODEL_NAME"],
            timeout=180, max_tokens=2048,
        )
        pipeline = FhirQualityPipeline(NlmUcumClient(), LoincFhirClient("https://tx.fhir.org/r4"))
        term = pipeline.loinc.lookup("718-7")
        result = LaboratorySuggestions(llm).assess({"text": "Hämoglobim"},
            {"code": "g/dL", "system": "http://unitsofmeasure.org"}, term, pipeline, specimen="Bld")
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["model_used"])
        selected = result["selected_code"]
        if selected:
            candidate = next(row for row in result["coding_candidates"] if row["code"] == selected)
            self.assertTrue(candidate["selectable"])
            if result["selected_name"]:
                self.assertIn(result["selected_name"], candidate["names"])
        self.assertTrue(llm.audit_records)
        self.assertFalse(result["automatic_changes"])
