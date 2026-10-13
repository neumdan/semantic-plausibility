"""Export and safety contracts without fabricated terminology evidence."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_batch, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.profile_import import import_structure_definition
from semantic_plausibility.model_config import allowed_endpoint
from semantic_plausibility.deployment import environment

ROOT = Path(__file__).resolve().parents[1]


def configuration(folder, *, connected=False, name="review.csv"):
    text = (ROOT / "pipeline.example.toml").read_text()
    text = text.replace('"examples/', '"' + str(ROOT / "examples") + '/')
    text = text.replace('path = "examples/laboratory.csv"', 'path = ' + json.dumps(str(ROOT / "examples/laboratory.csv")))
    text = text.replace('path = "output/laboratory-review.csv"', 'path = ' + json.dumps(str(folder / name)))
    if not connected:
        text = text.replace('mode = "lookups"', 'mode = "offline"')
    path = folder / "pipeline.toml"
    path.write_text(text)
    return path


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = configuration(self.folder)

    def test_grouping_and_statistics_need_no_terminology_fabrication(self):
        result = run_batch(self.config)
        self.assertEqual((result.input_records, result.distinct_tuples), (8, 7))
        summary = json.loads(result.summary.read_text())
        self.assertEqual(summary["weighted_readiness"]["denominator"], 84)
        self.assertEqual(summary["metrics"]["mii_numeric_set_ready"]["numerator"], 0)
        self.assertEqual(summary["evidence_kind"], "no_lookup_evidence")
        with result.csv.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(all(row["code_verified"] == "False" for row in rows))
        self.assertTrue(all(row["unit_verified"] == "False" for row in rows))
        self.assertEqual(sum(row["status"] == "error" for row in rows), 1)
        self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_exact_values_can_be_separate_tuples(self):
        self.config.write_text(self.config.read_text() + '\n[aggregation]\ngroup_by=["code","code_system","unit","unit_system","reference_range","value"]\n')
        self.assertEqual(run_batch(self.config).distinct_tuples, 8)

    def test_existing_outputs_are_protected_and_explicit_overwrite_is_repeatable(self):
        result = run_batch(self.config)
        original = result.csv.read_bytes()
        with self.assertRaisesRegex(ValueError, "Output already exists"):
            run_batch(self.config)
        self.assertEqual(result.csv.read_bytes(), original)
        self.config.write_text(self.config.read_text().replace('[output]', '[output]\noverwrite=true'))
        repeated = run_batch(self.config)
        self.assertEqual(repeated.distinct_tuples, 7)
        self.assertEqual(verify_audit(repeated.log)["status"], "verified")

    def test_missing_input_and_malformed_headers_fail_with_audit(self):
        broken = self.folder / "bad.csv"
        broken.write_text("unrelated\nvalue\n")
        self.config.write_text(self.config.read_text().replace(str(ROOT / "examples/laboratory.csv"), str(broken)))
        with self.assertRaisesRegex(ValueError, "columns"):
            run_batch(self.config)
        self.assertFalse((self.folder / "review.csv").exists())
        self.assertTrue(list(self.folder.glob("*.failed.*.audit.jsonl")))

    def test_output_cannot_replace_the_input(self):
        self.config.write_text(self.config.read_text().replace(str(self.folder / "review.csv"), str(ROOT / "examples/laboratory.csv")))
        with self.assertRaisesRegex(ValueError, "overwrite configuration, source"):
            run_batch(self.config)

    def test_unsupported_configuration_is_rejected(self):
        original = self.config.read_text()
        for old, new in [('config_version = 3', 'config_version = 2'), ('RESOURCE_TYPE = "Observation"', 'RESOURCE_TYPE = "Medication"'), ('FHIR_VERSION = "4.0.1"', 'FHIR_VERSION = "5.0.0"')]:
            with self.subTest(new=new):
                self.config.write_text(original.replace(old, new))
                with self.assertRaises(ValueError):
                    BatchConfiguration(self.config)

    def test_one_config_routes_native_and_compose_model_without_exposing_keys(self):
        config = BatchConfiguration(self.config)
        original = os.getenv("SQC_IN_CONTAINER")
        for container, expected in (("0", "http://127.0.0.1:11434/v1"), ("1", "http://ollama:11434/v1")):
            with self.subTest(container=container), environment({"SQC_IN_CONTAINER": container, "LOCAL_MODEL_BASE_URL": "http://127.0.0.1:11434/v1"}):
                with config.deployment.prepare(research=True, action="validate", source_access=False) as prepared:
                    model = json.loads(prepared.models_file.read_text())["models"][0]
                    self.assertEqual(model["base_url"], expected)
                    self.assertNotIn("container_base_url", model)
        self.assertEqual(os.getenv("SQC_IN_CONTAINER"), original)

    def test_profile_links_cannot_carry_embedded_secrets(self):
        text = self.config.read_text()
        for url in ("https://user:password@example.org/profile", "https://example.org/profile?token=secret", "javascript:alert(1)"):
            with self.subTest(url=url):
                self.config.write_text(text.replace('https://hl7.org/fhir/R4/observation.html', url))
                with self.assertRaisesRegex(ValueError, "documentation links"):
                    BatchConfiguration(self.config)

    def test_optional_package_configuration_is_pinned_and_does_not_activate_sources(self):
        source = ROOT / 'terminology.example.toml'
        config = BatchConfiguration(source)
        self.assertFalse(config.data['packages']['loinc_de']['enabled'])
        self.assertFalse(config.data['lookups']['icd11']['enabled'])
        self.config.write_text(source.read_text().replace('version = "2.82.0"', 'version = "latest"'))
        with self.assertRaisesRegex(ValueError, 'exact semantic version'):
            BatchConfiguration(self.config)

    def test_distinct_input_cannot_recreate_ids(self):
        self.config.write_text(self.config.read_text().replace('mode = "distinct"', 'mode = "items"'))
        with self.assertRaisesRegex(ValueError, "Item mode needs individual"):
            BatchConfiguration(self.config)

    def test_native_fhir_bundle_has_same_group_count_and_frequency(self):
        text = self.config.read_text()
        start = text.index('[input.columns]')
        end = text.index('[specification]')
        text = text[:start] + text[end:]
        text = text.replace('type = "csv"', 'type = "fhir_file"').replace('granularity = "distinct"', 'granularity = "individual"')
        text = text.replace(str(ROOT / 'examples/laboratory.csv'), str(ROOT / 'examples/laboratory.bundle.json'))
        self.config.write_text(text)
        result = run_batch(self.config)
        self.assertEqual((result.input_records, result.distinct_tuples), (84, 7))

    def test_changed_csv_is_detected_by_audit(self):
        result = run_batch(self.config)
        with result.csv.open('a') as handle:
            handle.write('changed\n')
        with self.assertRaises(ValueError):
            verify_audit(result.log)

    def test_published_mii_profile_imports_real_rules(self):
        policy = import_structure_definition(ROOT / 'examples/profiles/ObservationLab-2026.0.3.json')
        self.assertIn('medizininformatik-initiative', policy['canonical'])
        self.assertEqual(policy['guide_version'], '2026.0.3')

    def test_local_http_only_is_accepted_for_the_model(self):
        self.assertTrue(allowed_endpoint('http://127.0.0.1:11434/v1'))
        self.assertFalse(allowed_endpoint('http://external.example/v1'))
