"""Explicit connection probes and idempotent Ollama setup; no clinical queries."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import Request

from .model_config import select_model
from .db_source import load_mapping
from .profile_config import load_profile
from .observation_services import NlmUcumClient, LoincFhirClient, _NoModelRedirects
from .deployment import environment
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
    from .batch import BatchConfiguration
    from .profile_lookups import terminology_plan
    configuration = BatchConfiguration(path)
    report = {
        "configuration_sha256": deployment.sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_aliases": list(deployment.data["sources"]),
        "model_aliases": list(deployment.data.get("models", {})),
        "checks": [],
        "scope": "Configuration and connection readiness, not a clinical audit",
        "connections_probed": connections,
        "selected_input": {"type": raw_config.get("input", {}).get("type"),
                           "path": raw_config.get("input", {}).get("path"),
                           "resource": configuration.kind,
                           "granularity": configuration.input.get('granularity', 'individual'),
                           "columns": configuration.input.get('columns', {}),
                           "table": configuration.database.get('DB_TABLE'),
                           "schema": configuration.database.get('DB_SCHEMA')},
        "target_specification": raw_config.get('specification', {}),
        "output": configuration.output,
        "terminology_strategy": configuration.lookup_strategy,
        "selected_lookups": [{"alias": alias, "type": row.get("type"),
                              "location": row.get("path") or row.get("endpoint") or "configured endpoint reference"}
                             for alias, row in raw_config.get("lookups", {}).items() if row.get("enabled")],
        "research_enabled": raw_config.get("research", {}).get("enabled", False),
        "terminology_plan": terminology_plan(configuration),
        "fhir_validation": {"enabled": configuration.validation.get("enabled", False),
                            "backend": configuration.validation.get("backend", "matchbox"),
                            "applies_to": "Actual FHIR input documents, before aggregation"},
    }
    updates = {"SQC_BATCH_CDS_DSN": configuration.toolchain_database()} if connections and configuration.input_type == 'postgres' and configuration.database.get('PATH_TO_DB_CONFIG_TOML') else {}
    with environment(updates), deployment.prepare(
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
            if configuration.input_type == "csv":
                from .export_review import validate_csv
                from .batch_quality import CsvMapping
                def csv_preflight():
                    validate_csv(configuration, source["path"])
                    return configuration.fhir_csv or configuration.kind != "Observation" or bool(CsvMapping.read(source["columns"]))
                probe("csv_columns", csv_preflight)
        if source["kind"] == "postgres":
            probe("database_mapping", lambda: bool(source.get("mapping") and load_mapping(source["mapping"])))
        if configuration.input_type == 'sqlite':
            def sqlite_columns():
                from contextlib import closing
                import sqlite3
                from urllib.parse import quote
                from .db_source import sqlite_columns_present
                with closing(sqlite3.connect('file:' + quote(str(source['path']), safe='/') + '?mode=ro', uri=True)) as connection:
                    return sqlite_columns_present(connection, configuration.mapping)
            probe('sqlite_table_and_columns', sqlite_columns)
        if configuration.kind == "Observation" and (prepared.audit.profile != "auto" or prepared.audit.profile_config):
            probe(
                "audit_policy",
                lambda: bool(load_profile(prepared.audit.profile, prepared.audit.profile_config)),
            )
        if prepared.audit.unit_catalog:
            probe("unit_catalog", lambda: Path(prepared.audit.unit_catalog).is_file())
        from .terminology_router import make_source, load_sources
        for entry in load_sources(prepared.audit.terminology_sources) if prepared.audit.terminology_sources else []:
            if entry['kind'] in {'csv', 'sqlite', 'fhir_artifacts', 'loinc_csv'}:
                def local_artifacts(entry=entry):
                    local = make_source(entry)
                    try:
                        return bool(local.systems or local.supplements or local.value_sets) if entry['kind'] == 'fhir_artifacts' else True
                    finally:
                        if hasattr(local, 'close'):
                            local.close()
                probe('local_terminology:' + entry.get('alias', entry['kind']), local_artifacts)
        if configuration.validation.get("enabled"):
            from .fhir_validation import target_profile
            probe("fhir_validation_profile", lambda: bool(target_profile(configuration)))
        if connections:
            if source["kind"] == "postgres":

                def db_probe():
                    import psycopg

                    with psycopg.connect(
                        os.environ["SQC_CFG_DB_DSN"],
                        connect_timeout=5,
                        options="-c default_transaction_read_only=on -c statement_timeout=5000",
                    ) as connection:
                        read_only = connection.execute(
                            "SELECT current_setting('transaction_read_only'), 1"
                        ).fetchone() == ("on", 1)
                        from psycopg import sql
                        from .db_source import build_query
                        query, params = build_query(configuration.mapping)
                        connection.execute(sql.SQL('SELECT * FROM ({}) AS selected_input LIMIT 0').format(query), params)
                        return read_only

                probe("postgres_read_only_table_and_columns", db_probe)
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
                for alias, entry in raw_config.get('lookups', {}).items():
                    if entry.get('enabled') and entry.get('type') == 'fhir':
                        def fhir_probe(entry=entry):
                            token = deployment.secret(entry, 'token')
                            return http_json(entry['endpoint'].rstrip('/') + '/metadata', token=token).get('resourceType') == 'CapabilityStatement'
                        probe('terminology_fhir_metadata:' + alias, fhir_probe)
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
