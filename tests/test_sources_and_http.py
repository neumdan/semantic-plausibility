"""Local HTTP round trips and read-only database/FHIR integration contracts."""

from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
import unittest

from semantic_plausibility.db_source import build_query, load_mapping, resource_from_row
from semantic_plausibility.observation_services import LoincFhirClient, OpenAICompatibleJsonClient
from semantic_plausibility import research_prompts as prompts

EXAMPLES = Path(__file__).resolve().parents[1] / "tests/fixtures"


class SourceTests(unittest.TestCase):
    def test_postgresql_query_quotes_identifiers_binds_filters_and_preserves_decimal(self):
        mapping = load_mapping(EXAMPLES / "postgres.columns.json")
        mapping["since_column"] = "updated_at"
        query, params = build_query(mapping, since="2026-09-01", loinc_only=True)
        sql = query.as_string()
        self.assertIn('"audit"."lab_observations"', sql)
        self.assertNotIn("2026-09-01", sql)
        self.assertEqual(params, ["2026-09-01", "http://loinc.org"])
        resource, _ = resource_from_row(
            {
                "id": "one",
                "code": "718-7",
                "code_system": "http://loinc.org",
                "unit": "g/dL",
                "value": Decimal("14.0000000000000001"),
            },
            mapping,
        )
        self.assertEqual(resource["valueQuantity"]["value"], "14.0000000000000001")

    def test_jsonb_mapping_accepts_fhir_and_rejects_wrong_resource_type(self):
        mapping = load_mapping(EXAMPLES / "postgres.fhir-json.json")
        source = {"resourceType": "Observation", "id": "one", "valueQuantity": {"value": 14}}
        resource, _ = resource_from_row({"resource_json": source}, mapping)
        self.assertEqual(resource, source)
        with self.assertRaises(ValueError):
            resource_from_row({"resource_json": {"resourceType": "Patient"}}, mapping)

    def test_unsafe_database_identifiers_are_rejected(self):
        from semantic_plausibility.db_source import _identifier_parts

        for value in ("observation; DROP TABLE x", "foo.bar.baz", "value--"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _identifier_parts(value, relation=True)

    def test_loinc_version_request_is_checked_against_returned_version(self):
        class Response:
            status_code = 200
            url = "https://loinc.example/CodeSystem/$lookup"

            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "version", "valueString": "2.80"},
                        {"name": "display", "valueString": "Hemoglobin"},
                    ],
                }

        class Session:
            calls = []

            def get(self, url, **kwargs):
                self.calls.append(kwargs)
                return Response()

        session = Session()
        client = LoincFhirClient(session=session)
        self.assertEqual(client.lookup("718-7", version="2.80").version, "2.80")
        self.assertIsNone(client.lookup("718-7", version="2.79"))
        self.assertEqual(session.calls[0]["params"]["version"], "2.80")
        self.assertEqual(len(session.calls), 2)


class LocalHttpTests(unittest.TestCase):
    def test_openai_compatible_model_client_sends_extraction_json_and_reads_both_provider_shapes(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = json.dumps(
                    {"choices": [{"message": {"content": '{"units":[{"unit":"g/dL","excerpt":"g/dL"}]}'}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            client = OpenAICompatibleJsonClient(
                base_url=f"http://127.0.0.1:{server.server_port}/v1", model="test-model"
            )
            result = client.json(prompts.LOINC_UNITS, {"page_excerpt": "Example Units: g/dL"})
            self.assertEqual(result["units"][0]["unit"], "g/dL")
            self.assertEqual(requests[0][0], "/v1/chat/completions")
            self.assertEqual(requests[0][1]["model"], "test-model")
            self.assertEqual(requests[0][1]["temperature"], 0)
            schema = requests[0][1]["response_format"]["json_schema"]
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["schema"]["additionalProperties"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_model_redirect_does_not_forward_evidence_or_bearer_credentials(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append((self.path, self.headers.get("Authorization")))
                self.send_response(307)
                self.send_header("Location", "/other-provider")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            client = OpenAICompatibleJsonClient(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="SYNTHETIC-TOKEN",
            )
            with self.assertRaises(ValueError):
                client.json(prompts.LOINC_UNITS, {"page_excerpt": "Example Units: g/dL"})
            self.assertEqual(requests, [("/v1/chat/completions", "Bearer SYNTHETIC-TOKEN")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
