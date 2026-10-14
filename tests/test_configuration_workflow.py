"""Setup contracts, logging and real downloaded catalogues; no invented lookup responses."""

from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.deployment_checks import check_configuration
from semantic_plausibility.local_terminology import LoincCsvSource, ddd_references
from semantic_plausibility.progress import configure, LOGGER
from semantic_plausibility.terminology_router import TerminologyRouter

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationWorkflowTests(unittest.TestCase):
    def test_routing_without_sources_abstains_and_never_guesses_system(self):
        for strategy in ("fallback", "all"):
            router = TerminologyRouter([], strategy=strategy)
            self.assertEqual(
                router.check("http://loinc.org", "718-7")["reason"], "no_enabled_source_for_system"
            )
            self.assertEqual(router.check("", "718-7")["reason"], "missing_code_or_system")
            self.assertEqual(router.check("http://loinc.org", "718-7")["state"], "unknown")
            router.close()

    def test_invalid_local_table_is_a_visible_preflight_failure(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            bad = folder / "bad.csv"
            bad.write_text("unrelated_column\n")
            text = (ROOT / "pipeline.example.toml").read_text()
            text = text.replace('"examples/', '"' + str(ROOT / "examples") + "/")
            text = text.replace('mode = "lookups"', 'mode = "offline"')
            text += '\n[lookups.local_loinc]\nenabled=true\ntype="loinc_csv"\nsystems=["http://loinc.org"]\nversion="2.83"\npath="bad.csv"\n'
            config = folder / "pipeline.toml"
            config.write_text(text)
            report = check_configuration(config)
            self.assertFalse(report["passed"])
            check = next(c for c in report["checks"] if c["component"] == "local_terminology:local_loinc")
            self.assertEqual(check["error_type"], "ValueError")
            self.assertEqual(report["selected_input"]["columns"]["code"], "code")
            self.assertEqual(report["target_specification"]["FHIR_VERSION"], "4.0.1")

    def test_logging_controls_and_config_validation(self):
        with redirect_stderr(io.StringIO()) as out:
            configure("WARNING")
            LOGGER.info("hidden")
            LOGGER.warning("visible")
            self.assertNotIn("hidden", out.getvalue())
            self.assertIn("visible", out.getvalue())
        configure("ERROR")
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "pipeline.toml"
            for original, bad in [
                ('level = "INFO"', 'level="VERBOSE"'),
                ('strategy = "fallback"', 'strategy="internet-everywhere"'),
            ]:
                path.write_text((ROOT / "pipeline.example.toml").read_text().replace(original, bad))
                with self.assertRaises(ValueError):
                    BatchConfiguration(path)

    def test_sqlite_unknown_column_cannot_become_a_literal_value(self):
        from semantic_plausibility.db_source import sqlite_columns_present

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE lab (test_code TEXT)")
            self.assertTrue(
                sqlite_columns_present(connection, {"relation": "lab", "fields": {"code": "test_code"}})
            )
            with self.assertRaisesRegex(ValueError, "mapped columns"):
                sqlite_columns_present(connection, {"relation": "lab", "fields": {"code": "typo_column"}})
        finally:
            connection.close()


@unittest.skipUnless(
    os.getenv("SQC_TEST_LOINC_CSV"), "Supply the official full Loinc.csv and SQC_TEST_LOINC_VERSION"
)
class OfficialLocalLoincTests(unittest.TestCase):
    def test_real_csv_identity_axes_and_version(self):
        path = Path(os.environ["SQC_TEST_LOINC_CSV"])
        version = os.environ["SQC_TEST_LOINC_VERSION"]
        source = LoincCsvSource(path, version=version, complete=True)
        result = source.check("http://loinc.org", "718-7", version)
        self.assertEqual(result["state"], "valid")
        self.assertTrue(result["metadata"]["loinc_term"]["component"])
        self.assertTrue(result["metadata"]["loinc_term"]["example_ucum_units"])
        self.assertEqual(source.check("http://loinc.org", "718-7", "not-this-edition")["state"], "unknown")
        self.assertEqual(source.check("http://loinc.org", "not-a-code", version)["state"], "invalid")
        self.assertEqual(len(result["source_sha256"]), 64)


@unittest.skipUnless(os.getenv("SQC_TEST_ATC_DIR"), "Supply an already downloaded real BfArM ATC/DDD package")
class OfficialLocalAtcTests(unittest.TestCase):
    def test_official_ddd_is_retained_with_source(self):
        system = "http://fhir.de/CodeSystem/bfarm/atc"
        router = TerminologyRouter(
            [
                {
                    "kind": "fhir_artifacts",
                    "path": os.environ["SQC_TEST_ATC_DIR"],
                    "alias": "bfarm_atc",
                    "systems": [system],
                }
            ]
        )
        try:
            result = router.check(system, "N02BE01")
            self.assertEqual(result["state"], "valid")
            references = ddd_references({**result, "system": system, "code": "N02BE01"})
            self.assertTrue(references)
            self.assertTrue(all(r["text"] and r["source"] and r["version"] for r in references))
            self.assertTrue(all("patient dose" in r["scope"] for r in references))
            self.assertEqual(router.check("http://www.whocc.no/atc", "N02BE01")["state"], "unknown")
        finally:
            router.close()
