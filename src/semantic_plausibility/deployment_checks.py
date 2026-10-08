"""Explicit connection probes and idempotent Ollama setup; no clinical queries."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import Request

from .model_config import select_model
from .batch_quality import CsvMapping
from .db_source import load_mapping
from .profile_config import load_profile
from .observation_services import NlmUcumClient, LoincFhirClient, _NoModelRedirects
from urllib.request import build_opener


def load_deployment(path):
    from .batch import BatchConfiguration
    from .export_review import local_model_only

    config = BatchConfiguration(path)
    local_model_only(config)
    return config.deployment


def http_json(url, *, data=None, token="", timeout=15):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    if body:
        headers["Content-Type"] = "application/json"
    request = Request(url, body, headers)
    with build_opener(_NoModelRedirects()).open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError("Unexpected probe response")
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Probe response exceeds the size budget")
        return json.loads(raw)


def check_configuration(path, *, source_alias=None, connections=False, research=None, model_alias=None):
    import tomllib

    raw_config = tomllib.loads(Path(path).read_text())
    offline = raw_config.get("network", {}).get("mode", "offline") == "offline"
    active_lookups = {
        row.get("type") for row in raw_config.get("lookups", {}).values() if row.get("enabled", False)
    }
    deployment = load_deployment(path)
    report = {
        "configuration_sha256": deployment.sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_aliases": list(deployment.data["sources"]),
        "model_aliases": list(deployment.data.get("models", {})),
        "checks": [],
        "scope": "Configuration and connection readiness, not a clinical audit",
    }
    with deployment.prepare(
        source_alias=source_alias,
        research=research,
        model_alias=model_alias,
        action="connections" if connections else "validate",
    ) as prepared:
        report["selected_source"] = prepared.source_alias
        report["checks"].append({"component": "configuration", "state": "passed"})

        def probe(name, function):
            try:
                if function() is not True:
                    raise ValueError("Probe result not verified")
                report["checks"].append({"component": name, "state": "passed"})
            except Exception as exc:
                # No raw exception strings: providers can include DSNs, auth and input.
                report["checks"].append(
                    {"component": name, "state": "unavailable", "error_type": type(exc).__name__}
                )

        source = prepared.source
        if source["kind"] == "file":
            probe("input_file", lambda: bool(source["path"] and source["path"].exists()))
            if source["path"] and source["path"].suffix.lower() == ".csv":
                probe(
                    "csv_columns", lambda: bool(source.get("columns") and CsvMapping.read(source["columns"]))
                )
        if source["kind"] == "postgres":
            probe("database_mapping", lambda: bool(source.get("mapping") and load_mapping(source["mapping"])))
        if prepared.audit.profile != "auto" or prepared.audit.profile_config:
            probe(
                "audit_policy",
                lambda: bool(load_profile(prepared.audit.profile, prepared.audit.profile_config)),
            )
        if prepared.audit.unit_catalog:
            probe("unit_catalog", lambda: Path(prepared.audit.unit_catalog).is_file())
        if connections:
            if source["kind"] == "postgres":

                def db_probe():
                    import psycopg

                    with psycopg.connect(
                        os.environ["SQC_CFG_DB_DSN"],
                        connect_timeout=5,
                        options="-c default_transaction_read_only=on -c statement_timeout=5000",
                    ) as connection:
                        return connection.execute(
                            "SELECT current_setting('transaction_read_only'), 1"
                        ).fetchone() == ("on", 1)

                probe("postgres_read_only", db_probe)
            if not offline and not prepared.audit.snapshot:
                if "ucum" in active_lookups:
                    probe(
                        "ucum",
                        lambda: (
                            NlmUcumClient(base_url=os.environ["UCUM_NLM_BASE_URL"]).is_valid("g/dL") is True
                        ),
                    )
                if "loinc" in active_lookups:
                    probe(
                        "loinc",
                        lambda: (
                            LoincFhirClient(
                                base_url=os.environ["LOINC_FHIR_BASE_URL"],
                                username=os.getenv("LOINC_FHIR_USERNAME", ""),
                                password=os.getenv("LOINC_FHIR_PASSWORD", ""),
                            ).lookup("718-7")
                            is not None
                        ),
                    )
                if os.getenv("TX_FHIR_BASE_URL"):
                    probe(
                        "terminology_fhir_metadata",
                        lambda: (
                            http_json(
                                os.environ["TX_FHIR_BASE_URL"].rstrip("/") + "/metadata",
                                token=os.getenv("TX_FHIR_BEARER_TOKEN", ""),
                            ).get("resourceType")
                            == "CapabilityStatement"
                        ),
                    )
            if prepared.models_file and not offline:
                endpoint, key = select_model(prepared.model_alias, prepared.models_file)

                def model_probe():
                    models = http_json(endpoint.base_url.rstrip("/") + "/models", token=key).get("data", [])
                    return any(
                        row.get("id") in {endpoint.model, endpoint.model + ":latest"} for row in models
                    )

                probe("selected_model", model_probe)
    report["passed"] = all(c["state"] == "passed" for c in report["checks"])
    return report


def initialise_model(path, *, model_alias=None, attempts=30, sleep_seconds=1, progress=None):
    deployment = load_deployment(path)
    with deployment.prepare(model_alias=model_alias, action="model-init") as prepared:
        row = deployment.data["models"][prepared.model_alias]
        if row.get("runtime") != "ollama":
            raise ValueError("model-init is for Ollama; provision external models with their provider")
        endpoint, key = select_model(prepared.model_alias, prepared.models_file)
        if endpoint.model.endswith("-cloud"):
            raise ValueError("The shipped local setup requires a local Ollama model, not a cloud alias")
        root = endpoint.base_url.rstrip("/").removesuffix("/v1")
        if progress:
            progress("Waiting for the configured Ollama service...")
        ready = False
        for _ in range(attempts):
            try:
                version = http_json(root + "/api/version", token=key, timeout=3)
                if not isinstance(version, dict) or not isinstance(version.get("version"), str):
                    raise ValueError("Endpoint is not a ready Ollama service")
                ready = True
                break
            except OSError, ValueError:
                time.sleep(sleep_seconds)
        if not ready:
            raise ValueError("Ollama did not become ready within the startup budget")

        def installed():
            return [
                item
                for item in http_json(root + "/api/tags", token=key).get("models", [])
                if item.get("name") in {endpoint.model, endpoint.model + ":latest"}
            ]

        existing = installed()
        downloaded = not bool(existing)
        if downloaded:
            if progress:
                progress(
                    f"Downloading {endpoint.model}; weights are cached by Ollama. This can take some time."
                )
            http_json(
                root + "/api/pull",
                token=key,
                data={"model": endpoint.model, "stream": False},
                timeout=row.get("pull_timeout_seconds", 7200),
            )
            existing = installed()
        if not existing:
            raise ValueError("Ollama model is missing after setup")
        digest = existing[0].get("digest", "")
        expected = row.get("expected_digest", "")
        if expected and digest.removeprefix("sha256:") != expected.removeprefix("sha256:"):
            raise ValueError("Installed Ollama model digest differs from expected_digest")
        return {
            "model_alias": prepared.model_alias,
            "model": endpoint.model,
            "digest": digest,
            "downloaded": downloaded,
            "state": "ready",
            "configuration_sha256": deployment.sha256,
        }


def model_contract_test(path, *, model_alias=None, output=None):
    from .model_smoke import model_test, write_model_report

    deployment = load_deployment(path)
    with deployment.prepare(model_alias=model_alias, action="model-test") as prepared:
        destination = Path(output) if output else prepared.output / "model-test"
        report = model_test(prepared.models_file, prepared.model_alias, destination)
        report["configuration_sha256"] = deployment.sha256
        write_model_report(report, destination)
        return report


def sync_catalogue(path):
    from .unit_catalog import sync_units

    deployment = load_deployment(path)
    settings = deployment.data.get("units_catalog", {})
    if not settings and deployment.data.get("research", {}).get("unit_catalog"):
        settings = {"path": deployment.data["research"]["unit_catalog"]}
    if not settings.get("path"):
        raise ValueError("units_catalog.path must be configured")
    return sync_units(
        deployment.path_value(settings["path"]),
        source_file=deployment.path_value(settings.get("source_file")),
        source_url=settings.get("source_url", ""),
    )
