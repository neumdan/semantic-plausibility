"""Acceptance guards for model replies, plus the shipped synthetic smoke cases."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from semantic_plausibility import research_prompts as prompts
from semantic_plausibility.cli import main
from semantic_plausibility.model_config import DEFAULT_MODEL, ModelEndpoint, select_model
from semantic_plausibility.model_schemas import response_schema, validate_response
from semantic_plausibility.model_smoke import cases, expectation_issues, run_model_smoke, write_model_report
from semantic_plausibility.observation_services import OpenAICompatibleJsonClient


class Response:
    def __init__(self, content, finish_reason="stop"):
        self.content, self.finish_reason = content, finish_reason

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"finish_reason": self.finish_reason, "message": {"content": self.content}}]}


class Session:
    def __init__(self, content, finish_reason="stop"):
        self.content, self.finish_reason = content, finish_reason
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.content, self.finish_reason)


def client_for(value, finish_reason="stop"):
    session = Session(value if isinstance(value, str) else json.dumps(value), finish_reason)
    return OpenAICompatibleJsonClient(session=session), session


class ModelContractTests(unittest.TestCase):
    def test_small_local_model_is_default_and_settings_are_deployment_owned(self):
        self.assertEqual(DEFAULT_MODEL.model, "ministral-3:3b")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "models.json"
            config.write_text(json.dumps({"models": [{
                "alias": "test", "base_url": "http://127.0.0.1:11434/v1",
                "model": "test-model", "temperature": 0.15, "max_tokens": 512,
            }]}))
            endpoint, _ = select_model("test", config)
            self.assertEqual((endpoint.temperature, endpoint.max_tokens), (0.15, 512))
        for settings in ({"temperature": float("nan")}, {"temperature": True}, {"max_tokens": 0}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                ModelEndpoint("test", "http://127.0.0.1/v1", "test", **settings)

    def test_schema_and_source_are_checked_on_a_valid_reply(self):
        client, session = client_for({"units": [{"unit": "g/dL", "excerpt": "g/dL"}]})
        answer = client.json(prompts.LOINC_UNITS, {"page_excerpt": "Example Units: g/dL."})
        self.assertEqual(answer["units"][0]["unit"], "g/dL")
        request = session.calls[0][1]["json"]
        self.assertEqual(request["response_format"]["type"], "json_schema")
        self.assertTrue(request["response_format"]["json_schema"]["strict"])
        self.assertEqual(request["max_tokens"], 1024)
        self.assertNotIn("tools", request)

    def test_extra_fields_wrong_types_duplicate_fields_and_prose_are_rejected(self):
        for value in (
            {"units": [], "conversion_factor": 1000}, {"units": "g/dL"},
            {"units": [{"unit": "g/dL"}]}, '{"units":[],"units":[]}',
            'Explanation: ```json\n{"units":[]}\n```', '{"units":[],"confidence":NaN}',
            '```json\n{"units":[]}\n```\n```json\n{"units":[]}\n```',
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client_for(value)[0].json(prompts.LOINC_UNITS, {"page_excerpt": "g/dL"})

    def test_one_json_envelope_is_normalized_but_schema_is_still_enforced(self):
        client, _ = client_for('```json\n{"units":[]}\n```')
        self.assertEqual(client.json(prompts.LOINC_UNITS, {"page_excerpt": "No units."}), {"units": []})
        with self.assertRaises(ValueError):
            client_for('```json\n{"units":[],"factor":1000}\n```')[0].json(
                prompts.LOINC_UNITS, {"page_excerpt": "No units."},
            )

    def test_server_cannot_bypass_source_guard_by_returning_valid_json(self):
        for row in (
            {"unit": "mg/dL", "excerpt": "mg/dL"},
            {"unit": "g/dL", "excerpt": "mg/dL"},
        ):
            with self.subTest(row=row), self.assertRaises(ValueError):
                client_for({"units": [row]})[0].json(prompts.LOINC_UNITS, {"page_excerpt": "Example Units: g/dL."})

    def test_truncated_and_tool_responses_are_not_accepted(self):
        for reason in ("length", "tool_calls", "content_filter"):
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                client_for({"units": []}, reason)[0].json(prompts.LOINC_UNITS, {"page_excerpt": "No units."})

    def test_unknown_task_or_oversized_source_causes_no_model_request(self):
        client, session = client_for({"units": []})
        for instruction, evidence in (("arbitrary task", {}), (prompts.LOINC_UNITS, {"page_excerpt": "x" * 16000})):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                client.json(instruction, evidence)
        self.assertEqual(session.calls, [])

    def test_numeric_context_and_order_guards_reject_grounding_errors(self):
        valid = {"low": "1.5", "high": "3.5", "unit": "mmol/L", "population": "adults", "specimen": "serum", "method": "Method-A", "excerpt": "Reference interval for adults in serum using Method-A: 1.5 to 3.5 mmol/L."}
        evidence = {"abstract": valid["excerpt"]}
        for change in ({"high": "99"}, {"low": "NaN"}, {"low": "3.5", "high": "1.5"}, {"unit": "mol/L"}, {"population": "children"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                client_for({**valid, **change})[0].json(prompts.STUDY_BOUNDS, evidence)
        self.assertEqual(client_for(valid)[0].json(prompts.STUDY_BOUNDS, evidence), valid)

    def test_candidate_unit_must_come_from_documented_list(self):
        with self.assertRaises(ValueError):
            client_for({"units": [{"unit": "mg/dL", "reason": "guess"}]})[0].json(
                prompts.UNIT_CANDIDATES, {"documented_examples": [{"unit": "g/dL"}]},
            )

    def test_terminology_claim_must_be_copied_from_quotation(self):
        with self.assertRaises(ValueError):
            client_for({"claims": [{"claim": "All units are valid", "excerpt": "DEMO-1 is active."}]})[0].json(
                prompts.TERMINOLOGY_CLAIMS, {"excerpts": ["DEMO-1 is active."]},
            )

    def test_unapproved_plain_http_and_secret_query_urls_are_rejected(self):
        for url in ("http://other.example/v1", "https://model.example/v1?token=secret", "https://user:secret@model.example/v1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OpenAICompatibleJsonClient(base_url=url)

    def test_all_shipped_fixtures_have_registered_schemas(self):
        self.assertEqual(len(cases()), 11)
        for case in cases():
            self.assertIn(case["task"], ("LOINC_UNITS", "LOINC_RANGES", "UNIT_CANDIDATES", "STUDY_BOUNDS", "TERMINOLOGY_CLAIMS"))
            response_schema(getattr(prompts, case["task"]))
        with self.assertRaises(ValueError):
            validate_response({"ranges": [{"evidence_type": "guessed"}]}, response_schema(prompts.LOINC_RANGES))

    def test_injected_source_allows_abstention_but_never_the_injected_unit(self):
        expected = next(case for case in cases() if case["id"] == "source-instructions")["expected"]
        self.assertEqual(expectation_issues(expected, {"units": []}), [])
        self.assertEqual(expectation_issues(expected, {"units": [{"unit": "g/dL"}]}), [])
        self.assertNotEqual(expectation_issues(expected, {"units": [{"unit": "mL/s"}]}), [])

    def test_smoke_failures_remain_failures_in_console_and_report(self):
        class BrokenClient:
            model, base_url, temperature, max_tokens = "test", "http://127.0.0.1/v1", 0, 1024

            def json(self, instruction, evidence):
                raise ValueError("schema rejected")

        report = run_model_smoke(BrokenClient())
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["failed"], 11)
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "model"
            write_model_report(report, prefix)
            self.assertIn("Prüfung erforderlich", Path(str(prefix) + ".csv").read_text())
            self.assertEqual(json.loads(Path(str(prefix) + ".json").read_text())["failed"], 11)
        with patch("semantic_plausibility.deployment_checks.model_contract_test", return_value=report), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["model-test"]), 1)


if __name__ == "__main__":
    unittest.main()
