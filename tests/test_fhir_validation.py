"""Validation/provenance contracts; live outcomes always come from a real validator."""

from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.fhir_validation import FhirValidator, fhir_json, initialise_validator, local_endpoint, target_profile, validation_copy
from semantic_plausibility.report_sources import source_html

ROOT = Path(__file__).resolve().parents[1]


def config_file(folder, endpoint=None):
    text = (ROOT / "pipeline.fhir.example.toml").read_text().replace('"examples/', '"' + str(ROOT / "examples") + '/')
    if endpoint:
        text = text.replace('http://127.0.0.1:8081/matchboxv3/fhir', endpoint)
    path = folder / "pipeline.toml"
    path.write_text(text)
    return path


class ValidationContracts(unittest.TestCase):
    def test_submitted_fhir_keeps_decimal_precision_and_numeric_encoding(self):
        value = Decimal("1.234567890123456789")
        encoded = fhir_json({"valueQuantity": {"value": value}})
        self.assertEqual(json.loads(encoded, parse_float=Decimal)["valueQuantity"]["value"], value)
        self.assertNotIn('"1.234', encoded)

    def test_selected_profile_is_requested_without_changing_original_bundle(self):
        original = json.loads((ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text())
        before = deepcopy(original)
        with tempfile.TemporaryDirectory() as name:
            config = BatchConfiguration(config_file(Path(name)))
            profile = target_profile(config)
            copy = validation_copy(original, profile)
            self.assertEqual(original, before)
            self.assertIn(profile, copy["entry"][2]["resource"]["meta"]["profile"])
            self.assertNotIn("meta", original["entry"][2]["resource"])

    def test_package_setup_uses_real_pinned_guide_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = config_file(folder)
            result = initialise_validator(path)
            self.assertIn("de.medizininformatikinitiative.kerndatensatz.laborbefund#2026.0.3", result["packages"])
            application = json.loads((folder / "local/validation/matchbox/application.yaml").read_text())
            self.assertTrue(application["matchbox"]["fhir"]["context"]["httpReadOnly"])
            self.assertEqual(initialise_validator(path), result)
            modified = folder / "local/validation/matchbox/application.yaml"
            modified.write_text("changed configuration")
            with self.assertRaisesRegex(ValueError, "--overwrite"):
                initialise_validator(path)

    def test_public_validator_addresses_and_embedded_credentials_are_rejected(self):
        for endpoint in ("https://8.8.8.8/fhir", "http://user:password@localhost/fhir", "file:///tmp/validator", "http://169.254.169.254"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                local_endpoint(endpoint, resolve=False)

    def test_real_connection_failure_is_unavailable_and_not_passed(self):
        with tempfile.TemporaryDirectory() as name:
            path = config_file(Path(name), "http://127.0.0.1:9/fhir")
            path.write_text(path.read_text().replace("timeout_seconds = 180", "timeout_seconds = 1"))
            config = BatchConfiguration(path)
            validator = FhirValidator(config)
            resource = json.loads((ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text())
            result = validator.validate(resource)
            self.assertEqual(result["state"], "unavailable")
            self.assertNotIn("operation_outcome", result)

    def test_citations_are_escaped_and_only_http_links_are_active(self):
        source = "https://terminologien.bfarm.de/CodeSystem-ucum-common-units-translation-de-de-1-5-0.html"
        rendered = source_html(source, title='BfArM <script>alert(1)</script>', excerpt='<img src=x onerror=alert(1)>')
        self.assertIn('href="' + source + '"', rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("href=", source_html("javascript:alert(1)"))


@unittest.skipUnless(os.getenv("SQC_LIVE_FHIR_VALIDATION_TESTS") == "1", "Requires a running local Matchbox with the real MII IG")
class LiveFhirValidation(unittest.TestCase):
    def test_real_validator_distinguishes_required_mii_identifier(self):
        endpoint = os.getenv("SQC_TEST_VALIDATOR_URL", "http://127.0.0.1:8081/matchboxv3/fhir")
        with tempfile.TemporaryDirectory() as name:
            config = BatchConfiguration(config_file(Path(name), endpoint))
            validator = FhirValidator(config)
            valid = json.loads((ROOT / "examples/fhir/laboratory-valid.bundle.json").read_text())
            invalid = json.loads((ROOT / "examples/fhir/laboratory-invalid.bundle.json").read_text())
            accepted, rejected = validator.validate(valid), validator.validate(invalid)
            self.assertIn(accepted["state"], {"passed", "passed_with_warnings"}, accepted)
            self.assertEqual(rejected["state"], "invalid", rejected)
            self.assertGreater(rejected["counts"]["error"], 0)
            self.assertIn("2026.0.3", rejected["requested_profile"])


if __name__ == "__main__":
    unittest.main()
