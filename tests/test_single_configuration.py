"""The documented source switches must work with the one laboratory template."""

import csv
import importlib.util
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from semantic_plausibility import research_plan, run_plausibility_check
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.cli import log_configuration

ROOT = Path(__file__).resolve().parents[1]


class SingleConfigurationTests(unittest.TestCase):
    def test_documented_sources_preserve_profile_lookups_and_grouped_research(self):
        manual = (ROOT / "examples/lab/README.md").read_text()
        inputs = [block for block in re.findall(r"```toml\n(.*?)```", manual, re.S)
                  if block.startswith("[input]")]
        self.assertEqual(len(inputs), 6)
        template = (ROOT / "pipeline.example.toml").read_text()
        template = template.replace('path = "catalog.csv"', 'path = "' + str(ROOT / "catalog.csv") + '"')
        template = template.replace('"local/profiles/', '"' + str(ROOT / "local/profiles") + '/')
        # Replace the existing input tables exactly as the operator instructions say.
        remainder = re.sub(r"(?ms)^\[input(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)", "", template)
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            shutil.copytree(ROOT / "examples", folder / "examples")
            spec = importlib.util.spec_from_file_location("prepare_example", ROOT / "tools/prepare-lab-examples.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.prepare_database(folder)
            for index, block in enumerate(inputs):
                with self.subTest(index=index):
                    # SQLite/PostgreSQL recipes deliberately reuse a documented column map.
                    if 'type = "sqlite"' in block:
                        block += "\n[input.columns]\n" + inputs[0].split("[input.columns]\n")[1]
                    if 'type = "postgres"' in block:
                        block += "\n[input.columns]\n" + inputs[1].split("[input.columns]\n")[1]
                    config = folder / "pipeline.toml"
                    config.write_text(remainder.replace('mode = "lookups"', 'mode = "offline"') + "\n" + block)
                    selected = BatchConfiguration(config)
                    self.assertEqual(selected.kind, "Observation")
                    self.assertEqual(selected.profile, "mii-kds-lab")
                    self.assertEqual(selected.output["mode"], "distinct")
                    self.assertTrue(selected.data["lookups"]["loinc"]["enabled"])
                    if selected.input_type == "postgres":
                        # Real DB connections are a separate opt-in test; do not simulate replies.
                        self.assertEqual(selected.database["DB_TABLE"], "laboratory_summary")
                        continue
                    result = run_plausibility_check(config, output=folder / f"report-{index}.csv")
                    expected = (3, 1) if index in (0, 4) else (5, 4) if index == 1 else (1, 1)
                    self.assertEqual((result.input_records, result.distinct_tuples), expected)
                    plan = research_plan(config, result.csv, scope="studies")
                    self.assertEqual(plan["checked_groups"], expected[1])
                    if index in (0, 4):
                        with result.csv.open(encoding="utf-8-sig", newline="") as stream:
                            row = next(csv.DictReader(stream))
                        self.assertEqual((float(row["value_mean"]), float(row["value_sd"])), (14, 2))

    def test_log_identifies_selected_file_and_configuration_without_secret_values(self):
        config = BatchConfiguration(ROOT / "pipeline.example.toml")
        config.data["lookups"]["loinc"]["password_env"] = "PRIVATE_SECRET_VARIABLE"
        with self.assertLogs("semantic_plausibility", level="INFO") as captured:
            log_configuration(config)
        text = "\n".join(captured.output)
        self.assertIn(str(config.path), text)
        self.assertIn("csv; resource: Observation", text)
        self.assertIn("distinct -> output: distinct", text)
        self.assertIn("Terminology ucum", text)
        self.assertNotIn("PRIVATE_SECRET_VARIABLE", text)
