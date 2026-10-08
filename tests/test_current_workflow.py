"""Current TOML/CLI workflow: equivalent source semantics and secret isolation."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from semantic_plausibility import run_batch, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.synthetic_study import config_text, create_examples

ROOT = Path(__file__).resolve().parents[1]


class CurrentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        create_examples(self.data, ROOT / "examples/studies/reference-pack.synthetic.json")
        self.config = self.root / "pipeline.toml"
        self.config.write_text(config_text(self.data, output=self.root / "review.csv"))

    def test_default_summaries_and_explicit_point_tuples_preserve_frequencies(self):
        for exact, expected in ((False, 7), (True, 8)):
            output = self.root / f"group-{exact}.csv"
            text = config_text(self.data, output=output)
            grouping = 'group_by=["code","code_system","unit","unit_system","reference_range"]'
            text = text.replace(grouping, grouping[:-1] + ',"value"]' if exact else "")
            self.config.write_text(text)
            result = run_batch(self.config)
            self.assertEqual(result.distinct_tuples, expected)
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary["weighted_readiness"]["denominator"], 84)

    def test_mapped_postgres_export_has_same_findings_and_no_serialized_dsn(self):
        self.config.write_text(
            self.config.read_text().replace('type="csv"', 'type="postgres"', 1)
            + '\n[database]\nDB_SCHEMA="audit"\nDB_TABLE="lab"\nDB_DSN_ENV="SQC_TEST_DB_DSN"\n'
        )
        with (self.data / "lis.distinct.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        # The PostgreSQL SELECT aliases the source specimen column to its semantic role.
        for row in rows:
            row["loinc_system"] = row.pop("specimen")
        secret = "PRIVATE_PASSWORD_DO_NOT_EXPORT"
        dsn = "postgresql://test:" + secret + "@localhost/test"
        with (
            patch.dict(os.environ, {"SQC_TEST_DB_DSN": dsn}),
            patch("semantic_plausibility.batch.scan", return_value=iter(rows)) as scan,
        ):
            result = run_batch(self.config)
            self.assertEqual(scan.call_args.args[1], dsn)
        self.assertEqual(result.distinct_tuples, 7)
        self.assertEqual(result.statuses, {"passed": 2, "suggestion": 1, "unclear": 3, "error": 1})
        for file in self.root.glob("review.*"):
            self.assertNotIn(secret, file.read_text())
        self.assertNotIn("SQC_CFG_DB_DSN", os.environ)
        self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_command_runs_outside_project_and_protects_existing_report(self):
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        args = [sys.executable, "-m", "semantic_plausibility", "batch", "--config", str(self.config)]
        first = subprocess.run(args, cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("8 input rows -> 7", first.stdout)
        contents = (self.root / "review.csv").read_bytes()
        second = subprocess.run(args, cwd=self.root, env=environment, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)
        self.assertEqual((self.root / "review.csv").read_bytes(), contents)
        verification = subprocess.run(
            [sys.executable, "-m", "semantic_plausibility", "verify", str(self.root / "review.audit.jsonl")],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(verification.returncode, 0, verification.stderr)
        self.assertEqual(json.loads(verification.stdout)["status"], "verified")

    def test_retired_configurations_and_nonlaboratory_types_fail_before_output(self):
        original = self.config.read_text()
        for text, message in (
            (original.replace("config_version=3", "config_version=2"), "config_version=3"),
            (
                original.replace('RESOURCE_TYPE="Observation"', 'RESOURCE_TYPE="Medication"'),
                "laboratory Observation",
            ),
            (original.replace('type="csv"', 'type="fhir_server"'), "TORCH/CQL"),
        ):
            with self.subTest(message=message):
                self.config.write_text(text)
                with self.assertRaisesRegex(ValueError, message):
                    BatchConfiguration(self.config)
                self.assertFalse((self.root / "review.csv").exists())
