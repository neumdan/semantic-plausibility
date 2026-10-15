"""Real retrieval/model checks; never simulate terminology or model replies."""

import os
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility.fhir_quality_pipeline import FhirQualityPipeline
from semantic_plausibility.laboratory_suggestions import LaboratorySuggestions
from semantic_plausibility.observation_services import (
    LoincFhirClient, NlmUcumClient, OpenAICompatibleJsonClient, PublicEvidenceClient,
)
from semantic_plausibility.terminology_router import TerminologyRouter
from semantic_plausibility import research_prompts as prompts
from semantic_plausibility import run_plausibility_check, research_evidence, verify_audit
from test_batch import configuration, ROOT


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
            identities = [(row["code"], row["version"]) for row in result["coding_candidates"]]
            self.assertEqual(len(identities), len(set(identities)))
            self.assertTrue(any(row['code'] == '718-7' and row['selectable'] for row in result['coding_candidates']))
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
    def test_real_research_report_keeps_source_and_names_when_model_is_unavailable(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = configuration(root, connected=True)
            with (ROOT / "examples/laboratory.csv").open(newline="") as handle:
                reader = csv.DictReader(handle)
                fields, row = reader.fieldnames, next(reader)
            source = root / "source.csv"
            with source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
            text = config.read_text().replace(str(ROOT / "examples/laboratory.csv"), str(source))
            text = text.replace('base_url = "http://127.0.0.1:11434/v1"', 'base_url = "http://127.0.0.1:9/v1"')
            text = text.replace('container_base_url = "http://ollama:11434/v1"', 'container_base_url = "http://127.0.0.1:9/v1"')
            text = text.replace('timeout_seconds = 180', 'timeout_seconds = 1')
            text = text.replace('unit_catalog = "local/unit-catalog.json"', '')
            text = text.replace('max_articles_per_loinc = 4', 'max_articles_per_loinc = 1')
            config.write_text(text)
            check = run_plausibility_check(config)
            original_hash = hashlib.sha256(check.csv.read_bytes()).hexdigest()
            dossier = research_evidence(config)
            self.assertEqual(dossier["model_state"], "no_accepted_response")
            self.assertEqual(dossier["groups_with_study_evidence"], 0)
            self.assertGreater(dossier["evidence_counts"]["articles_retrieved"], 0)
            self.assertEqual(dossier["evidence_counts"]["extracted_measurements"], 0)
            self.assertTrue(Path(dossier["studies"]).is_file())
            with Path(dossier["csv"]).open(encoding="utf-8-sig", newline="") as handle:
                result = next(csv.DictReader(handle))
            assessment = json.loads(result["assessment_context_json"])
            self.assertEqual(assessment["check_result_basis"], "original_A1_report")
            self.assertGreater(assessment["execution"]["new_model_calls"], 0)
            context = json.loads(Path(dossier["csv"]).with_suffix(".summary.json").read_text())["run_context"]
            self.assertEqual(context["level"], "A3")
            self.assertFalse(context["input"]["hospital_data_requeried"])
            self.assertTrue(result["canonical_name"])
            self.assertTrue(result["suggested_name"])
            full = json.loads(result["recommendation_set_json"])
            self.assertEqual(full["code"]["code"], "718-7")
            self.assertEqual(full["canonical_name"]["value"], result["canonical_name"])
            self.assertTrue(full["canonical_name"]["sources"])
            self.assertFalse(full["automatic_changes"])
            self.assertEqual(hashlib.sha256(check.csv.read_bytes()).hexdigest(), original_hash)
            html = Path(dossier["html"]).read_text()
            self.assertIn("no_accepted_response", html)
            self.assertIn("europepmc.org/article/", html)
            self.assertEqual(verify_audit(Path(dossier["audit"]))["status"], "verified")

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
    def test_real_hemoglobin_measurement_does_not_become_thiamine_reference_interval(self):
        llm = OpenAICompatibleJsonClient(base_url=os.environ["SQC_TEST_MODEL_BASE_URL"],
            model=os.environ["SQC_TEST_MODEL_NAME"], timeout=180, max_tokens=2048)
        client = PublicEvidenceClient(llm=llm, europe_pmc_retmax=1, research_sources=("europe_pmc",))
        record = client._europe_pmc_records("EXT_ID:42473864 AND SRC:MED")[0]
        term = LoincFhirClient("https://tx.fhir.org/r4").lookup("718-7")
        payload = {"loinc_code": term.code, "title": record["title"],
            "analyte_definition": {"component": term.component, "property": term.property, "system": term.system},
            **client._abstract_payload(record)}
        try:
            bounds = llm.json(prompts.STUDY_BOUNDS, {"evidence_type": "unclassified_discovery", **payload})
        except ValueError:
            # Actual small-model mistakes must be rejected; rejection cannot
            # disable the subsequent independent measurement task.
            self.assertEqual(llm._failures, 0)
        else:
            self.assertIsNone(bounds["low"])
            self.assertIsNone(bounds["high"])
        measurements = llm.json(prompts.STUDY_MEASUREMENTS, {"analyte": term.component, **payload})
        self.assertTrue(measurements["measurements"])
        for item in measurements["measurements"]:
            self.assertIn(item["excerpt"], payload["excerpt_candidates"])
            self.assertIn("hemoglobin", item["excerpt"].casefold())
            self.assertEqual(item["unit"], "g/dL")

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
