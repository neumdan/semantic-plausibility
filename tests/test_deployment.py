"""Shipped TOML configuration, source adapters and local model setup."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from semantic_plausibility import research_prompts as prompts
from semantic_plausibility.cli import main
from semantic_plausibility.deployment import environment
from semantic_plausibility.deployment_checks import initialise_model
from semantic_plausibility.observation_services import OpenAICompatibleJsonClient

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def toml_value(value):
    if isinstance(value, dict):
        return (
            "{" + ", ".join(json.dumps(key) + " = " + toml_value(item) for key, item in value.items()) + "}"
        )
    return json.dumps(value)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()

    def config(self, extra="", *, model_alias="local"):
        from semantic_plausibility.synthetic_study import config_text, create_examples

        folder = self.root / "data"
        create_examples(folder, EXAMPLES / "studies/reference-pack.synthetic.json")
        path = self.root / "pipeline.toml"
        content = config_text(folder, output=self.root / "review.csv")
        content = content.replace("[general]\n", '[general]\nMODEL_ALIAS="' + model_alias + '"\n')
        path.write_text(
            content
            + '\n[models.local]\nruntime="ollama"\nbase_url="http://127.0.0.1:11434/v1"\nmodel = "gpt-oss:20b"\n'
            + extra
        )
        return path

    def test_model_init_is_idempotent_and_pulls_only_missing_model(self):
        path = self.config()
        for present in (True, False):
            state, calls = {"present": present}, []

            def http(url, **kwargs):
                calls.append((url, kwargs))
                if url.endswith("/api/tags"):
                    return {
                        "models": [{"name": "gpt-oss:20b", "digest": "a" * 64}] if state["present"] else []
                    }
                if url.endswith("/api/pull"):
                    state["present"] = True
                return {"version": "0.34.1", "status": "success"}

            with patch("semantic_plausibility.deployment_checks.http_json", side_effect=http):
                report = initialise_model(path, attempts=1, sleep_seconds=0)
            self.assertEqual(report["downloaded"], not present)
            self.assertEqual(sum(url.endswith("/api/pull") for url, _ in calls), int(not present))
            self.assertEqual(report["digest"], "a" * 64)

    def test_model_digest_mismatch_and_cloud_alias_are_rejected(self):
        path = self.config()
        path.write_text(
            path.read_text().replace(
                'model = "gpt-oss:20b"', 'model = "gpt-oss:20b"\nexpected_digest = "' + "b" * 64 + '"'
            )
        )
        with patch(
            "semantic_plausibility.deployment_checks.http_json",
            return_value={"version": "0.34.1", "models": [{"name": "gpt-oss:20b", "digest": "a" * 64}]},
        ):
            with self.assertRaisesRegex(ValueError, "digest"):
                initialise_model(path, attempts=1, sleep_seconds=0)
        path.write_text(path.read_text().replace("gpt-oss:20b", "gpt-oss:20b-cloud"))
        with patch("semantic_plausibility.deployment_checks.http_json") as http:
            with self.assertRaisesRegex(ValueError, "cloud"):
                initialise_model(path)
        http.assert_not_called()

    def test_model_unavailability_has_a_bounded_startup_failure(self):
        with patch(
            "semantic_plausibility.deployment_checks.http_json", side_effect=OSError("offline")
        ) as http:
            with self.assertRaisesRegex(ValueError, "startup budget"):
                initialise_model(self.config(), attempts=2, sleep_seconds=0)
        self.assertEqual(http.call_count, 2)

    def test_cli_model_test_respects_the_toml_default_alias(self):
        path = self.config(model_alias="selected")
        report = {"model": "test", "passed": 0, "total": 0, "failed": 0, "cases": []}
        with (
            patch(
                "semantic_plausibility.deployment_checks.model_contract_test", return_value=report
            ) as model,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(["model-test", "--config", str(path)]), 0)
        self.assertIsNone(model.call_args.kwargs["model_alias"])

    def test_json_object_provider_mode_preserves_grounding_and_supports_kolibri_template_flag(self):
        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"units":[{"unit":"mg/dL","excerpt":"mg/dL"}]}'},
                        }
                    ]
                }

        class Session:
            def post(self, url, **kwargs):
                self.request = kwargs
                return Response()

        session = Session()
        client = OpenAICompatibleJsonClient(
            session=session,
            response_mode="json_object",
            enable_thinking=False,
            reasoning_effort="low",
            timeout=180,
        )
        with self.assertRaises(ValueError):
            client.json(prompts.LOINC_UNITS, {"page_excerpt": "Example units: g/dL."})
        payload = session.request["json"]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(session.request["timeout"], 180)

    def test_environment_context_restores_preexisting_values_and_absence(self):
        with patch.dict(os.environ, {"EXISTING_SETTING": "old"}):
            with environment({"EXISTING_SETTING": "new", "TEMPORARY_SETTING": "value"}):
                self.assertEqual(os.environ["EXISTING_SETTING"], "new")
            self.assertEqual(os.environ["EXISTING_SETTING"], "old")
            self.assertNotIn("TEMPORARY_SETTING", os.environ)
