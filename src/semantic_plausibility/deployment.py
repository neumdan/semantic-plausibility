"""Internal configuration preparation for the laboratory export workflow.

The existing audit kernel remains unchanged. Temporary JSON bridges reuse its
mappings and model registry. Secrets are resolved only for the action being
run and never written to the bridges or reports.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
import tomllib
from urllib.parse import quote, urlencode, urlparse

from .support import CheckSettings
from .model_config import ModelEndpoint, allowed_endpoint

_LOCK = RLock()
_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def table(value, label, fields):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a TOML table")
    unknown = set(value) - set(fields)
    if unknown:
        raise ValueError(f"Unsupported {label} setting: " + ", ".join(sorted(unknown)))
    return value


@contextmanager
def environment(updates):
    # CLI execution is one process per run. Restore even on source/model errors.
    with _LOCK:
        previous = {key: os.environ.get(key) for key in updates}
        try:
            for key, value in updates.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@dataclass
class Prepared:
    audit: CheckSettings
    source: dict
    source_alias: str
    output: Path
    models_file: Path | None
    model_alias: str
    scope: str


class Deployment:
    def __init__(self, path: str | Path, *, data: dict | None = None):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        raw = self.path.read_bytes()
        if data is None:
            raise ValueError("Internal settings must come from BatchConfiguration")
        self.data = data
        self.sha256 = hashlib.sha256(raw).hexdigest()
        table(
            self.data,
            "deployment",
            {
                "version",
                "run",
                "audit",
                "sources",
                "models",
                "network",
                "postgres",
                "terminology",
                "research",
                "units_catalog",
            },
        )
        if type(self.data.get("version")) is not int or self.data["version"] != 1:
            raise ValueError("Deployment version must be 1")
        run = table(self.data.get("run", {}), "run", {"source", "output", "research", "model_alias"})
        for key in ("source", "model_alias"):
            if key in run and (not isinstance(run[key], str) or not _ALIAS.fullmatch(run[key])):
                raise ValueError(f"Invalid run.{key} alias")
        if type(run.get("research", False)) is not bool:
            raise ValueError("run.research must be true or false")
        table(
            self.data.get("audit", {}),
            "audit",
            {
                "profile",
                "profile_config",
                "delimiter",
                "unit_catalog",
                "approvals",
                "context_file",
                "context",
            },
        )
        table(
            self.data.get("research", {}),
            "research",
            {"scope", "unit_catalog", "observation_definitions", "pubmed", "loinc_website_base_url"},
        )
        table(self.data.get("network", {}), "network", {"allowed_http_hosts"})
        units = table(
            self.data.get("units_catalog", {}), "units_catalog", {"path", "source_file", "source_url"}
        )
        if units.get("source_file") and units.get("source_url"):
            raise ValueError("Choose one unit catalogue input")
        sources = self.data.get("sources")
        if not isinstance(sources, dict) or not sources:
            raise ValueError("Define at least one named source")
        for alias, row in sources.items():
            if not _ALIAS.fullmatch(alias):
                raise ValueError("Invalid source alias")
            table(
                row,
                "source",
                {"kind", "path", "columns", "snapshot", "mapping", "since", "profile", "terminology_sources"},
            )
            if row.get("kind") not in {"file", "postgres"}:
                raise ValueError("Internal source kind must be file or postgres")
            common = {"kind", "profile", "terminology_sources", "snapshot"}
            fields = {"file": {"path", "columns"}, "postgres": {"mapping", "since"}}[row["kind"]]
            if set(row) - common - fields:
                raise ValueError("Source settings do not match its kind")
        models = self.data.get("models", {})
        if not isinstance(models, dict):
            raise ValueError("models must be a TOML table of named endpoints")
        for alias, row in models.items():
            if not _ALIAS.fullmatch(alias):
                raise ValueError("Invalid model alias")
            table(
                row,
                "model",
                {
                    "runtime",
                    "base_url",
                    "base_url_env",
                    "model",
                    "api_key_env",
                    "api_key_file",
                    "temperature",
                    "max_tokens",
                    "timeout_seconds",
                    "reasoning_effort",
                    "response_mode",
                    "expected_digest",
                    "enable_thinking",
                    "pull_timeout_seconds",
                },
            )
            if row.get("runtime", "external") not in {"ollama", "external"}:
                raise ValueError("Model runtime must be ollama or external")
            if bool(row.get("base_url")) == bool(row.get("base_url_env")):
                raise ValueError("Model needs exactly one base_url or base_url_env")
            if not isinstance(row.get("model"), str) or not row["model"].strip():
                raise ValueError("Model endpoint needs a nonempty model identifier")
            if row.get("expected_digest") and (
                not isinstance(row["expected_digest"], str)
                or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", row["expected_digest"])
            ):
                raise ValueError("expected_digest must be a full sha256 model digest")
            pull_timeout = row.get("pull_timeout_seconds", 7200)
            if type(pull_timeout) is not int or not 60 <= pull_timeout <= 86400:
                raise ValueError("pull_timeout_seconds must be 60..86400")
        table(
            self.data.get("postgres", {}),
            "postgres",
            {
                "dsn_env",
                "dsn_file",
                "host",
                "port",
                "database",
                "username_env",
                "username_file",
                "password_env",
                "password_file",
                "sslmode",
                "connect_timeout",
            },
        )
        terminology = table(
            self.data.get("terminology", {}), "terminology", {"ucum", "loinc", "fhir", "sources"}
        )
        for name, fields in {
            "ucum": {"base_url", "base_url_env"},
            "loinc": {
                "base_url",
                "base_url_env",
                "username_env",
                "username_file",
                "password_env",
                "password_file",
            },
            "fhir": {"base_url", "base_url_env", "token_env", "token_file"},
        }.items():
            table(terminology.get(name, {}), "terminology." + name, fields)
        pubmed = self.data.get("research", {}).get("pubmed", {})
        table(
            pubmed,
            "research.pubmed",
            {"api_key_env", "api_key_file", "email", "tool", "max_results", "base_url"},
        )
        maximum = pubmed.get("max_results", 3)
        if type(maximum) is not int or not 1 <= maximum <= 10:
            raise ValueError("PubMed max_results must be 1..10")
        self.validate_references(self.data)

    @staticmethod
    def validate_references(value):
        """Validate references without opening unused secret files."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_env"):
                    if not isinstance(item, str) or not _ENV.fullmatch(item):
                        raise ValueError(f"Invalid {key} environment variable name")
                    if value.get(key.removesuffix("_env") + "_file"):
                        raise ValueError(f"Choose {key} or a secret file")
                if key.endswith("_file") and not isinstance(item, str):
                    raise ValueError(f"{key} must be a path string")
                Deployment.validate_references(item)
        elif isinstance(value, list):
            for item in value:
                Deployment.validate_references(item)

    def path_value(self, value):
        if not value:
            return None
        if not isinstance(value, str):
            raise ValueError("Configured path must be a string")
        path = Path(value)
        return path if path.is_absolute() else (self.root / path).resolve()

    def secret(self, section, key, *, required=False):
        env, file = section.get(key + "_env"), section.get(key + "_file")
        if env and file:
            raise ValueError(f"Choose {key}_env or {key}_file")
        if env:
            if not isinstance(env, str) or not _ENV.fullmatch(env):
                raise ValueError(f"Invalid {key}_env name")
            value = os.getenv(env, "")
        elif file:
            path = self.path_value(file)
            if not path.is_file() or path.stat().st_size > 32768:
                raise ValueError(f"Missing or oversized {key} secret file")
            value = path.read_text().rstrip("\r\n")
        else:
            value = ""
        if required and not value:
            raise ValueError(f"Missing {key}; set the configured environment variable or secret file")
        return value

    def url(self, section, *, default="", key="base_url"):
        if section.get(key) and section.get(key + "_env"):
            raise ValueError(f"Choose {key} or {key}_env")
        value = section.get(key) or self.secret(section, key) or default
        if section.get(key + "_env") and not self.secret(section, key):
            raise ValueError(f"Missing configured {key}_env variable")
        if not value:
            return ""
        parsed = urlparse(value)
        if not allowed_endpoint(value) or parsed.query or parsed.fragment:
            raise ValueError(f"Invalid {key}; use HTTPS or an explicitly approved internal HTTP host")
        return value

    def database_dsn(self):
        section = self.data.get("postgres", {})
        if section.get("dsn_env") or section.get("dsn_file"):
            if set(section) - {"dsn_env", "dsn_file"}:
                raise ValueError("Choose a DSN reference or PostgreSQL connection fields")
            return self.secret(section, "dsn", required=True)
        host, database = section.get("host"), section.get("database")
        port, timeout = section.get("port", 5432), section.get("connect_timeout", 10)
        if (
            not isinstance(host, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", host)
            or not isinstance(database, str)
            or not database
        ):
            raise ValueError("PostgreSQL requires host and database or a DSN reference")
        if (
            type(port) is not int
            or not 1 <= port <= 65535
            or type(timeout) is not int
            or not 1 <= timeout <= 60
        ):
            raise ValueError("Invalid PostgreSQL port or connect_timeout")
        sslmode = section.get("sslmode", "require")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("Invalid PostgreSQL sslmode")
        user, password = (
            self.secret(section, "username", required=True),
            self.secret(section, "password", required=True),
        )
        authority = f"[{host}]" if ":" in host else host
        query = urlencode({"sslmode": sslmode, "connect_timeout": timeout})
        return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{authority}:{port}/{quote(database, safe='')}?{query}"

    @contextmanager
    def prepare(self, *, source_alias=None, research=None, model_alias=None, action="execute"):
        run = self.data.get("run", {})
        alias = source_alias or run.get("source") or next(iter(self.data["sources"]))
        if alias not in self.data["sources"]:
            raise ValueError("Unknown configured source alias")
        source = dict(self.data["sources"][alias])
        audit_action = action in {"execute", "validate", "connections"}
        enabled = (run.get("research", False) if research is None else research) if audit_action else False
        if type(enabled) is not bool:
            raise ValueError("run.research must be true or false")
        selected_model = model_alias or run.get("model_alias", "local")
        scope = self.data.get("research", {}).get("scope", "lookup")
        if scope not in {"lookup", "studies"}:
            raise ValueError("Research scope must be lookup or studies")
        hosts = self.data.get("network", {}).get("allowed_http_hosts", [])
        if not isinstance(hosts, list) or any(
            not isinstance(h, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", h) for h in hosts
        ):
            raise ValueError("allowed_http_hosts must be a hostname list")
        with tempfile.TemporaryDirectory(prefix="plausibility-config-") as temporary:
            folder = Path(temporary)

            def bridge(name, value):
                if isinstance(value, str):
                    path = self.path_value(value)
                    if path.suffix.lower() != ".toml":
                        return path
                    try:
                        value = tomllib.loads(path.read_text())
                    except tomllib.TOMLDecodeError:
                        raise ValueError(f"Invalid {name} TOML configuration") from None
                if not isinstance(value, dict):
                    raise ValueError(f"{name} must be a path or inline TOML table")
                path = folder / (name + ".json")
                path.write_text(json.dumps(value, ensure_ascii=False))
                return path

            managed = {
                "OLLAMA_BASE_URL",
                "OLLAMA_MODEL",
                "LMSTUDIO_BASE_URL",
                "LMSTUDIO_MODEL",
                "SQC_MODELS_FILE",
                "SQC_TERMINOLOGY_SOURCES",
                "UCUM_NLM_BASE_URL",
                "LOINC_FHIR_BASE_URL",
                "LOINC_FHIR_USERNAME",
                "LOINC_FHIR_PASSWORD",
                "TX_FHIR_BASE_URL",
                "TX_FHIR_BEARER_TOKEN",
                "NCBI_API_KEY",
                "PUBMED_API_KEY",
                "PUBMED_EMAIL",
                "PUBMED_TOOL",
                "SQC_OBSERVATION_DEFINITIONS",
                "SQC_PUBMED_RETMAX",
                "SQC_CFG_DB_DSN",
            }
            updates = dict.fromkeys(managed)
            updates["SQC_ALLOWED_HTTP_HOSTS"] = ",".join(hosts)
            live = audit_action and not source.get("snapshot")
            credentials_needed = action in {"execute", "connections"}
            # Resolve references before clearing ambient variables with those names.
            terminology = self.data.get("terminology", {})
            for section_name, prefix, fallback in (
                ("ucum", "UCUM_NLM", "https://ucum.nlm.nih.gov/ucum-service/v1"),
                ("loinc", "LOINC_FHIR", "https://fhir.loinc.org"),
                ("fhir", "TX_FHIR", ""),
            ):
                section = terminology.get(section_name, {})
                if live:
                    with environment({"SQC_ALLOWED_HTTP_HOSTS": updates["SQC_ALLOWED_HTTP_HOSTS"]}):
                        updates[prefix + "_BASE_URL"] = self.url(section, default=fallback)
                if live and credentials_needed and section_name == "loinc":
                    updates["LOINC_FHIR_USERNAME"] = self.secret(section, "username")
                    updates["LOINC_FHIR_PASSWORD"] = self.secret(section, "password")
                elif live and credentials_needed and section_name == "fhir":
                    updates["TX_FHIR_BEARER_TOKEN"] = self.secret(section, "token")
            if source["kind"] == "postgres" and action in {"execute", "connections"}:
                updates["SQC_CFG_DB_DSN"] = self.database_dsn()
            models_file = None
            if enabled or action == "model-init":
                if selected_model not in self.data.get("models", {}):
                    raise ValueError("Unknown configured model alias")
                row = dict(self.data["models"][selected_model])
                with environment({"SQC_ALLOWED_HTTP_HOSTS": updates["SQC_ALLOWED_HTTP_HOSTS"]}):
                    url = self.url(row)
                if not url:
                    raise ValueError("Selected model endpoint is not configured")
                fields = {
                    key: value
                    for key, value in row.items()
                    if key in ModelEndpoint.__dataclass_fields__ and key != "api_key_env"
                }
                fields.update({"alias": selected_model, "base_url": url})
                if row.get("api_key_env") or row.get("api_key_file"):
                    if action != "validate":
                        updates["SQC_CFG_MODEL_KEY"] = self.secret(row, "api_key", required=True)
                    fields["api_key_env"] = "SQC_CFG_MODEL_KEY"
                ModelEndpoint(**fields)
                models_file = bridge("models", {"models": [fields]})
                updates["SQC_MODELS_FILE"] = str(models_file)
            settings = self.data.get("research", {})
            pubmed = settings.get("pubmed", {})
            if enabled:
                updates.update(
                    {
                        "PUBMED_API_KEY": self.secret(pubmed, "api_key"),
                        "PUBMED_EMAIL": pubmed.get("email", ""),
                        "PUBMED_TOOL": pubmed.get("tool", "semantic-plausibility"),
                        "SQC_PUBMED_RETMAX": pubmed.get("max_results", 3),
                        "SQC_OBSERVATION_DEFINITIONS": str(
                            self.path_value(settings["observation_definitions"])
                        )
                        if settings.get("observation_definitions")
                        else "",
                    }
                )
                updates.update(
                    {
                        "LOINC_WEBSITE_BASE_URL": settings.get("loinc_website_base_url", "https://loinc.org"),
                        "PUBMED_EUTILS_BASE_URL": pubmed.get(
                            "base_url", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
                        ),
                    }
                )
            entries = (
                source.get("terminology_sources", terminology.get("sources", [])) if audit_action else []
            )
            if not isinstance(entries, list):
                raise ValueError("terminology.sources must be an array of tables")
            transformed = []
            for index, entry in enumerate(entries):
                row = dict(
                    table(
                        entry,
                        "terminology source",
                        {
                            "kind",
                            "systems",
                            "path",
                            "complete",
                            "base_url",
                            "token_env",
                            "token_file",
                            "url_template",
                            "keywords",
                            "client_cert",
                            "client_key",
                            "ontology",
                            "limit",
                            "release",
                            "language",
                            "token_endpoint",
                            "client_id_env",
                            "client_id_file",
                            "client_secret_env",
                            "client_secret_file",
                        },
                    )
                )
                for key in ("path", "client_cert", "client_key"):
                    if row.get(key):
                        row[key] = str(self.path_value(row[key]))
                if row.get("token_file") and live and credentials_needed:
                    variable = f"SQC_CFG_TX_KEY_{index}"
                    updates[variable] = self.secret(row, "token", required=True)
                    row.pop("token_file")
                    row["token_env"] = variable
                else:
                    row.pop("token_file", None)
                for key in ("client_id", "client_secret"):
                    if row.get(key + "_file") and live and credentials_needed:
                        variable = f"SQC_CFG_TX_{key.upper()}_{index}"
                        updates[variable] = self.secret(row, key, required=True)
                        row[key + "_env"] = variable
                    row.pop(key + "_file", None)
                transformed.append(row)
            sources_file = bridge("terminology", {"sources": transformed}) if transformed else None
            audit = dict(self.data.get("audit", {}))
            if not audit_action:
                audit = {}
            if audit.get("profile_config"):
                audit["profile_config"] = bridge("policy", audit["profile_config"])
            for key in ("profile_config", "unit_catalog", "approvals", "context_file"):
                if audit.get(key) and not isinstance(audit[key], Path):
                    audit[key] = self.path_value(audit[key])
                elif key in audit and not audit[key]:
                    audit.pop(key)
            if enabled and settings.get("unit_catalog"):
                audit["unit_catalog"] = self.path_value(settings["unit_catalog"])
            audit.update(
                {
                    "snapshot": self.path_value(source.get("snapshot")),
                    "terminology_sources": sources_file,
                    "deployment_sha256": self.sha256,
                    "source_alias": alias,
                }
            )
            if source.get("profile"):
                audit["profile"] = source["profile"]
            if audit["snapshot"] and enabled:
                raise ValueError("Offline snapshot source cannot use research; choose a live source")
            for key in ("columns", "mapping"):
                if audit_action and source.get(key):
                    source[key] = bridge(key, source[key])
            source["path"] = self.path_value(source.get("path"))
            with environment(updates):
                yield Prepared(
                    CheckSettings(**audit),
                    source,
                    alias,
                    self.path_value(run.get("output", "output")),
                    models_file,
                    selected_model,
                    scope,
                )
