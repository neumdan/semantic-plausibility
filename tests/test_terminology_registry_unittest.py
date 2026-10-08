"""All registered URIs plus real adapter edge cases; no network dependency."""

import csv
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


from semantic_plausibility.observation_services import NlmUcumClient
from semantic_plausibility.terminologies.checks import (
    LiveChecks,
    first_concept,
    load_environment,
    load_registry,
    offline_checks,
    unit_cases,
)
from semantic_plausibility.terminology_router import FhirCodeSource, TerminologyRouter
from semantic_plausibility.terminology_validation import FhirTerminologyClient


REPO = Path(__file__).resolve().parents[1]


class JsonResponse(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class XmlResponse:
    status_code = 200

    def __init__(self, quantity, target):
        self.text = (
            "<UCUMWebServiceResponse><Response>"
            f"<ResultQuantity>{quantity}</ResultQuantity><TargetUnit>{target}</TargetUnit>"
            "</Response></UCUMWebServiceResponse>"
        )

    def raise_for_status(self):
        pass


class ConversionSession:
    def __init__(self, transform):
        self.transform = transform
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        quantity = Decimal(url.split("/ucumtransform/", 1)[1].split("/", 1)[0])
        return XmlResponse(self.transform(quantity), url.rsplit("/to/", 1)[1])


class RegistryTests(unittest.TestCase):
    def test_every_inventory_service_profile_and_configured_system_is_covered(self):
        registry = load_registry()
        with (REPO / "src/semantic_plausibility/terminologies/generated/systems.csv").open() as handle:
            expected = {row["system"] for row in csv.DictReader(handle)}
        self.assertEqual({entry.system for entry in registry}, expected)
        self.assertEqual(len(registry), 196)
        extra = "https://example.org/CodeSystem/local-unit"
        extended = load_registry(source_entries=[{"systems": [extra]}])
        self.assertEqual(len(extended), len(registry) + 1)
        self.assertEqual(next(e for e in registry if e.system == "http://unitsofmeasure.org").usage, "unit")
        self.assertEqual(
            next(e for e in registry if e.system == "http://fhir.de/sid/gkv/kvid-10").usage,
            "identifier_namespace",
        )
        self.assertEqual(
            next(e for e in registry if e.system == "http://www.bbmri-eric.eu/").usage, "identifier_namespace"
        )

    def test_csv_and_sqlite_isolate_all_196_systems_and_versions(self):
        rows = offline_checks(load_registry())
        for row in rows:
            with self.subTest(system=row["system"]):
                self.assertEqual(row["offline_contract"], "passed", row["synthetic_checks"])
                self.assertEqual(row["live_status"], "not_checked")
        self.assertEqual(sum(len(row["synthetic_checks"]) for row in rows), 196 * 12)

    def test_fhir_requests_use_canonical_url_for_validate_and_system_for_lookup(self):
        calls = []

        def respond(request, timeout):
            parsed = urlparse(request.full_url)
            params = parse_qs(parsed.query)
            calls.append((parsed.path, params))
            if parsed.path.endswith("$validate-code"):
                self.assertIn("url", params)
                self.assertNotIn("system", params)
                payload = {
                    "resourceType": "Parameters",
                    "parameter": [{"name": "result", "valueBoolean": params["code"] == ["POSITIVE"]}],
                }
            else:
                self.assertIn("system", params)
                payload = {
                    "resourceType": "Parameters",
                    "parameter": [{"name": "display", "valueString": "Synthetic concept"}],
                }
            return JsonResponse(json.dumps(payload).encode())

        with patch("semantic_plausibility.terminology_validation.urlopen", side_effect=respond):
            client = FhirTerminologyClient("https://tx.example.org/fhir")
            for entry in load_registry():
                with self.subTest(system=entry.system):
                    self.assertTrue(client.validate_code(entry.system, "POSITIVE", "v1"))
                    self.assertFalse(client.validate_code(entry.system, "INVALID", "v1"))
                    self.assertEqual(
                        client.lookup_code(entry.system, "POSITIVE", "v1")["display"], "Synthetic concept"
                    )
                    self.assertEqual(calls[-3][1]["url"], [entry.system])
                    self.assertEqual(calls[-1][1]["system"], [entry.system])
                    self.assertTrue(all(params["version"] == ["v1"] for _, params in calls[-3:]))
            count = len(calls)
            client.validate_code(entry.system, "POSITIVE", "v1")
            self.assertEqual(len(calls), count)

    def test_unsupported_server_malformed_boolean_and_outages_never_pass(self):
        source = FhirCodeSource("https://tx.example.org/fhir")
        with patch.object(source.client, "validate_code", return_value=None):
            self.assertEqual(source.check("http://loinc.org", "718-7")["state"], "unknown")
        for payload in (
            {"resourceType": "OperationOutcome"},
            {"resourceType": "Parameters", "parameter": [{"name": "result", "valueBoolean": "true"}]},
            {"resourceType": "Parameters", "parameter": []},
        ):
            with (
                self.subTest(payload=payload),
                patch(
                    "semantic_plausibility.terminology_validation.urlopen",
                    return_value=JsonResponse(json.dumps(payload).encode()),
                ),
            ):
                with self.assertRaises(ValueError):
                    FhirTerminologyClient("https://tx.example.org/fhir").validate_code(
                        "http://loinc.org", "718-7"
                    )
        router = TerminologyRouter(
            [{"kind": "fhir", "base_url": "https://tx.example.org/fhir", "systems": ["http://loinc.org"]}]
        )
        with patch("semantic_plausibility.terminology_validation.urlopen", side_effect=TimeoutError()):
            result = router.check("http://loinc.org", "718-7")
        self.assertEqual(result["state"], "unknown")
        self.assertTrue(result["errors"])

    def test_affine_temperature_conversion_is_not_a_factor(self):
        for offset, target in ((Decimal("273.15"), "K"), (Decimal("-273.15"), "Cel")):
            with self.subTest(target=target):
                session = ConversionSession(lambda value: value + offset)
                service = NlmUcumClient(session=session)
                self.assertIsNone(service.factor("Cel" if target == "K" else "K", target))

    def test_linear_unit_conversion_checks_zero_and_caches_factor(self):
        session = ConversionSession(lambda value: value * 10)
        service = NlmUcumClient(session=session)
        self.assertEqual(service.factor("g/dL", "g/L"), Decimal("10"))
        self.assertTrue(any("/ucumtransform/0/" in url for url in session.calls))
        count = len(session.calls)
        self.assertEqual(service.factor("g/dL", "g/L"), Decimal("10"))
        self.assertEqual(len(session.calls), count)

    def test_server_true_with_unknown_fragment_warning_remains_unknown(self):
        fragment = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "result", "valueBoolean": True},
                {
                    "name": "issues",
                    "resource": {
                        "resourceType": "OperationOutcome",
                        "issue": [
                            {
                                "severity": "warning",
                                "code": "code-invalid",
                                "extension": [
                                    {
                                        "url": "http://hl7.org/fhir/StructureDefinition/operationoutcome-message-id",
                                        "valueString": "UNKNOWN_CODE_IN_FRAGMENT",
                                    }
                                ],
                            }
                        ],
                    },
                },
            ],
        }
        with patch(
            "semantic_plausibility.terminology_validation.urlopen",
            return_value=JsonResponse(json.dumps(fragment).encode()),
        ):
            source = FhirCodeSource("https://tx.example.org/fhir")
            result = source.check("https://example.org/CodeSystem/fragment", "MISSING")
        self.assertEqual(result["state"], "unknown")
        self.assertIs(result["validation"]["server_result"], True)
        self.assertEqual(result["validation"]["issues"][0]["code"], "code-invalid")
        for message, expected in (
            ("Unknown code system 'https://missing.example'", None),
            (
                "A definition for CodeSystem 'https://missing.example' could not be found, so the code cannot be validated",
                None,
            ),
            ("Unknown code 'BAD' in the CodeSystem 'https://known.example' version 'v1'", False),
        ):
            payload = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "result", "valueBoolean": False},
                    {"name": "message", "valueString": message},
                ],
            }
            self.assertIs(FhirTerminologyClient._result(payload)[0], expected)

    def test_missing_system_false_is_unknown_but_invalid_code_false_is_invalid(self):
        for expression, expected in (("system", None), ("code", False)):
            payload = {
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "result", "valueBoolean": False},
                    {
                        "name": "issues",
                        "resource": {
                            "resourceType": "OperationOutcome",
                            "issue": [{"severity": "error", "code": "not-found", "expression": [expression]}],
                        },
                    },
                ],
            }
            self.assertIs(FhirTerminologyClient._result(payload)[0], expected)

    def test_unknown_valueset_is_not_reported_as_nonmembership(self):
        source = FhirCodeSource("https://tx.example.org/fhir")
        with patch.object(source.client, "validate_value_set", return_value=None):
            result = source.check_binding("https://example.org/ValueSet/missing", "http://loinc.org", "718-7")
        self.assertEqual(result["state"], "unknown")

    def test_router_retains_unverifiable_evidence_and_tries_later_binding_source(self):
        router = TerminologyRouter(
            [
                {
                    "kind": "fhir",
                    "base_url": "https://public.example.org/fhir",
                    "systems": ["http://loinc.org"],
                },
                {
                    "kind": "fhir",
                    "base_url": "https://internal.example.org/fhir",
                    "systems": ["http://loinc.org"],
                },
            ]
        )
        first, second = router.sources[0][1], router.sources[1][1]
        unknown = {
            "state": "unknown",
            "source": "https://public.example.org/fhir",
            "validation": {"server_result": True, "issues": [{"code": "code-invalid"}]},
        }
        with (
            patch.object(first, "check", return_value=unknown),
            patch.object(second, "check", return_value={"state": "unknown"}),
        ):
            result = router.check("http://loinc.org", "718-7")
        self.assertEqual(result["validation"], unknown["validation"])
        self.assertEqual(result["source"], unknown["source"])
        with (
            patch.object(first, "check_binding", return_value=unknown),
            patch.object(second, "check_binding", return_value={"state": "member", "source": "internal"}),
        ):
            result = router.check_binding("https://example.org/ValueSet/lab", "http://loinc.org", "718-7")
        self.assertEqual(result["state"], "member")
        self.assertEqual(result["source"], "internal")

    def test_nlm_error_text_is_an_unsupported_conversion_not_a_factor(self):
        response = XmlResponse("1", "s")
        response.text = "<UCUMWebServiceResponse><Response>ERROR: Sorry. g/dL cannot be converted to s.</Response></UCUMWebServiceResponse>"
        session = type("Session", (), {"get": lambda *args, **kwargs: response})()
        self.assertIsNone(NlmUcumClient(session=session).factor("g/dL", "s"))

    def test_complete_ucum_caret_notation_uses_verified_mapping_without_approval(self):
        from semantic_plausibility.fhir_quality_pipeline import FhirQualityPipeline
        from semantic_plausibility.observation_quality import LoincTerm

        class Ucum:
            def is_valid(self, unit):
                return unit in {"10^6/uL", "10*6/uL", "10^6"}

            def factor(self, source, target):
                return Decimal("1") if source == "10^6/uL" and target == "10*6/uL" else None

        class Loinc:
            def lookup(self, code):
                return LoincTerm(
                    code, "Synthetic cell count", system="Bld", scale="Qn", example_ucum_units=("10*6/uL",)
                )

        pipeline = FhirQualityPipeline(Ucum(), Loinc())
        for unit, expected in (("10^6/uL", "conversion_verified"), ("10^6", "needs_refinement")):
            observation = {
                "resourceType": "Observation",
                "code": {"coding": [{"system": "http://loinc.org", "code": "789-8"}]},
                "valueQuantity": {"unit": unit, "code": unit, "system": "http://unitsofmeasure.org"},
            }
            mapping = pipeline.check(observation, loinc_system="Bld")["measurements"][0]["mapping"]
            self.assertEqual(mapping["class"], expected)
            self.assertFalse(mapping["approved"])

    def test_input_units_keep_raw_spelling_without_invented_expectations(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "units.csv"
            csv_path.write_text('raw_unit\n"µg/ml"\n"µg/ml"\n" ml "\n', encoding="utf-8")
            units, conversions = unit_cases(csv_path, "raw_unit")
        raw = [row for row in units if "input_csv" in row["origins"]]
        self.assertEqual({row["unit"] for row in raw}, {"µg/ml", " ml "})
        self.assertTrue(all(row["expected_valid"] is None for row in raw))
        self.assertEqual(next(row for row in raw if row["unit"] == "µg/ml")["input_row_count"], 2)
        self.assertTrue(any(row["source_unit"] == "Cel" for row in conversions))

    def test_identifier_and_supplement_are_not_membership_tests(self):
        registry = load_registry()
        checker = LiveChecks(registry, [])
        try:
            row = {"system": "http://fhir.de/sid/gkv/kvid-10", "usage": "identifier_namespace"}
            self.assertEqual(checker.system(row)["live_status"], "not_applicable")
            row = {"system": "http://example.org/supplement", "usage": "coded_element"}
            with patch.object(checker, "discover", return_value=({"content": "supplement"}, [])):
                self.assertEqual(checker.system(row)["live_status"], "not_applicable")
        finally:
            checker.close()
        self.assertEqual(
            first_concept(
                {
                    "concept": [
                        {
                            "code": "abstract",
                            "property": [{"code": "notSelectable", "valueBoolean": True}],
                            "concept": [{"code": "usable"}],
                        }
                    ]
                }
            ),
            "usable",
        )

    def test_private_environment_never_evaluates_shell_and_preserves_existing_config(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"TX_FHIR_BASE_URL": "https://existing.example"}, clear=True),
        ):
            path = Path(directory) / "private.env"
            path.write_text(
                'TX_FHIR_BASE_URL=https://other.example\nLOINC_FHIR_USERNAME="$(touch /tmp/never-create)"\nUNRELATED_SECRET=private\n'
            )
            load_environment(path)
            import os

            self.assertEqual(os.environ["TX_FHIR_BASE_URL"], "https://existing.example")
            self.assertEqual(os.environ["LOINC_FHIR_USERNAME"], "$(touch /tmp/never-create)")
            self.assertNotIn("UNRELATED_SECRET", os.environ)

    def test_cli_exports_all_systems_and_never_claims_an_offline_live_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "registry"
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "semantic_plausibility.terminologies.checks",
                    "--output-prefix",
                    str(prefix),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            summary = json.loads(run.stdout)
            self.assertEqual(summary["offline_contracts"], {"passed": 196})
            self.assertEqual(summary["live_systems"], {"not_checked": 196})
            self.assertEqual(summary["mode"], "offline_synthetic")
            with Path(f"{prefix}.systems.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 196)
            self.assertTrue(all(row["positive_state"] == "not_checked" for row in rows))


if __name__ == "__main__":
    unittest.main()
