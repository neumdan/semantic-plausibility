"""Input/scope contracts; no invented terminology, model or validator replies."""

import csv
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from semantic_plausibility import research_plan, run_plausibility_check, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.cli import run_main
from semantic_plausibility.export_review import _file_payloads
from semantic_plausibility.fhir_validation import validate_input
from semantic_plausibility.observation_quality import LoincTerm
from semantic_plausibility.observation_services import PublicEvidenceClient
from test_batch import ROOT, configuration


def fhir_csv_config(folder, *, validate=False):
    text = (ROOT / "examples/lab/fhir-csv.toml").read_text().replace('"../../', '"' + str(ROOT) + '/')
    text = text.replace('path = "' + str(ROOT / 'output/laboratory-fhir-review.csv') + '"',
                        'path = "' + str(folder / 'fhir.csv') + '"')
    text = text.replace('mode = "lookups"', 'mode = "offline"')
    if not validate:
        text = text.replace('enabled = true\nbackend =', 'enabled = false\nbackend =')
    path = folder / "pipeline.toml"
    path.write_text(text)
    return path


class RunModeTests(unittest.TestCase):
    def test_lookup_scope_short_circuits_study_retrieval(self):
        client = PublicEvidenceClient(scope="lookup")
        self.assertEqual(client.search_studies(LoincTerm("718-7", "")), [])
        self.assertEqual(client.query_log, [])
        self.assertEqual(client.source_records, {})

    def test_output_override_keeps_toml_and_prior_report_and_is_audited(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = configuration(folder)
            before = config.read_bytes()
            first = run_plausibility_check(config)
            original = first.csv.read_bytes()
            second = run_plausibility_check(config, output="second.csv")
            self.assertEqual(second.csv, (folder / "second.csv").resolve())
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(first.csv.read_bytes(), original)
            summary = json.loads(second.summary.read_text())
            self.assertEqual(summary["output_path"], str(second.csv))
            self.assertEqual(summary["configuration_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(verify_audit(second.log)["status"], "verified")

    def test_lookup_plan_excludes_study_queries_and_prompts(self):
        with tempfile.TemporaryDirectory() as name:
            config = configuration(Path(name))
            checked = run_plausibility_check(config)
            before = config.read_bytes()
            plan = research_plan(config, checked.csv, scope="lookup")
            self.assertEqual(plan["scope"], "lookup")
            self.assertEqual(plan["study_sources"], [])
            self.assertEqual(plan["max_articles_per_loinc"], 0)
            self.assertTrue(all(not row["study_queries"] for row in plan["targets"]))
            self.assertFalse(any(key.startswith("STUDY_") for key in plan["prompts"]))
            studies = research_plan(config, checked.csv, scope="studies")
            self.assertIn("europe_pmc", studies["study_sources"])
            self.assertIn("STUDY_MEASUREMENTS", studies["prompts"])
            self.assertEqual(config.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "scope"):
                research_plan(config, checked.csv, scope="misspelled")

    def test_resource_csv_matches_native_bundle_and_retains_item_provenance(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = fhir_csv_config(folder)
            path.write_text(path.read_text().replace('mode = "distinct"', 'mode = "items"'))
            original = (ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text()
            documents = list(_file_payloads(ROOT / "examples/laboratory-fhir.csv", resource_column="resource_json"))
            self.assertEqual(documents[0][1], json.loads(original))
            result = run_plausibility_check(path)
            self.assertEqual((result.input_records, result.distinct_tuples, result.skipped_resources), (1, 1, 2))
            self.assertEqual(result.fhir_validation_state, "disabled")
            with result.csv.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertTrue(row["source_id"])
            self.assertIn("laboratory-fhir.csv:row:2:entry:", row["source_ref"])
            self.assertFalse(row["code_verified"] == "True")  # Offline, no captured lookup evidence.
            self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_resource_csv_rejects_aggregate_and_mixed_scalar_contracts(self):
        with tempfile.TemporaryDirectory() as name:
            path = fhir_csv_config(Path(name))
            text = path.read_text()
            for edited in (text.replace('granularity = "individual"', 'granularity = "distinct"'),
                           text.replace('[input.columns]', '[input.columns]\nvalue = "value"'),
                           text.replace('[input.columns]', '[input.defaults]\nunit="g/dL"\n[input.columns]')):
                path.write_text(edited)
                with self.assertRaises(ValueError):
                    BatchConfiguration(path)

    def test_resource_arrays_and_duplicate_ids_use_native_guards(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = fhir_csv_config(folder)
            source = folder / "resources.csv"
            original = json.loads((ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text())
            resources = [entry["resource"] for entry in original["entry"]]
            config.write_text(config.read_text().replace(str(ROOT / 'examples/laboratory-fhir.csv'), str(source)))
            with source.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["resource_json"])
                writer.writerow([json.dumps(resources)])
            self.assertEqual(run_plausibility_check(config).input_records, 1)
            with source.open("a", newline="") as handle:
                csv.writer(handle).writerow([json.dumps(resources[-1])])
            with self.assertRaisesRegex(ValueError, "standardization_and_grouping"):
                run_plausibility_check(config, output="duplicate.csv")

    def test_malformed_resource_cell_fails_without_disclosing_payload(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "bad.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["resource_json"])
                writer.writerow(['PRIVATE-SOURCE-DATA'])
            with self.assertRaisesRegex(ValueError, "Invalid FHIR JSON in CSV row 2") as failure:
                list(_file_payloads(path, resource_column="resource_json"))
            self.assertNotIn("PRIVATE-SOURCE-DATA", str(failure.exception))

    def test_csv_validation_unavailable_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = fhir_csv_config(folder, validate=True)
            path.write_text(path.read_text().replace('http://127.0.0.1:8081/matchboxv3/fhir', 'http://127.0.0.1:9/fhir')
                            .replace('timeout_seconds = 180', 'timeout_seconds = 1'))
            result = run_main(["check", "--config", str(path)])
            self.assertEqual(result, 1)
            summary = json.loads((folder / "fhir.summary.json").read_text())
            self.assertEqual(summary["fhir_validation"]["state"], "unavailable")
            self.assertEqual(summary["fhir_validation"]["documents"]["unavailable"], 1)
            self.assertEqual(verify_audit(folder / "fhir.audit.jsonl")["status"], "verified")

    def test_unsupported_domains_cannot_enable_the_laboratory_adapter(self):
        with tempfile.TemporaryDirectory() as name:
            config = configuration(Path(name))
            original = config.read_text()
            for kind in ("Condition", "Procedure"):
                config.write_text(original.replace('RESOURCE_TYPE = "Observation"', f'RESOURCE_TYPE = "{kind}"'))
                with self.assertRaisesRegex(ValueError, "RESOURCE_TYPE must be"):
                    BatchConfiguration(config)

    def test_scalar_database_export_preserves_csv_grouping_and_frequency(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = configuration(folder)
            database = folder / "lab.sqlite"
            with (ROOT / "examples/laboratory.csv").open(newline="") as handle:
                reader = csv.reader(handle)
                fields, rows = next(reader), list(reader)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE lab (' + ','.join('"' + key + '" TEXT' for key in fields) + ')')
                db.executemany('INSERT INTO lab VALUES (' + ','.join('?' for _ in fields) + ')', rows)
            config.write_text(config.read_text().replace('type = "csv"', 'type = "sqlite"')
                              .replace(str(ROOT / "examples/laboratory.csv"), str(database)) + '\n[database]\nDB_TABLE="lab"\n')
            result = run_plausibility_check(config)
            self.assertEqual((result.input_records, result.distinct_tuples), (8, 7))
            self.assertEqual(json.loads(result.summary.read_text())["weighted_readiness"]["denominator"], 84)

    def test_database_resource_json_preserves_fhir_numeric_encoding_errors(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = fhir_csv_config(folder)
            database = folder / "lab.sqlite"
            resource = json.loads((ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text())["entry"][-1]["resource"]
            resource["valueQuantity"]["value"] = "14"  # Invalid FHIR number encoding, not terminology evidence.
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE lab (resource_json TEXT)')
                db.execute('INSERT INTO lab VALUES (?)', [json.dumps(resource)])
            config.write_text(config.read_text().replace('type = "csv"', 'type = "sqlite"')
                              .replace(str(ROOT / "examples/laboratory-fhir.csv"), str(database)) + '\n[database]\nDB_TABLE="lab"\n')
            result = run_plausibility_check(config)
            self.assertEqual(result.statuses, {"error": 1})

    @unittest.skipUnless(os.getenv("SQC_LIVE_FHIR_VALIDATION_TESTS") == "1", "Requires actual local Matchbox/MII guide")
    def test_resource_csv_reaches_real_validator_with_unchanged_document(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = fhir_csv_config(folder, validate=True)
            endpoint = os.getenv("SQC_TEST_VALIDATOR_URL", "http://127.0.0.1:8081/matchboxv3/fhir")
            path.write_text(path.read_text().replace("http://127.0.0.1:8081/matchboxv3/fhir", endpoint))
            config = BatchConfiguration(path)
            for suffix, expected in (("", {"passed", "passed_with_warnings"}), ("-invalid", {"invalid"})):
                source = ROOT / f"examples/laboratory-fhir{suffix}.csv"
                result = validate_input(config, source, folder / f"outcomes{suffix}.jsonl", **config.fhir_reader_options)
                self.assertIn(result["state"], expected, result)
                self.assertEqual(sum(result["documents"].values()), 1)


if __name__ == "__main__":
    unittest.main()
