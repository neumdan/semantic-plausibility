"""Local full-resource validation through Matchbox (HL7) or the MII validator."""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .observation_services import _NoModelRedirects

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9.-]*#[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?")
LOCAL_HOSTS = {"localhost", "matchbox", "mii-validator", "blaze", "host.containers.internal", "host.docker.internal"}


def fhir_json(value):
    """Preserve parsed decimal precision and JSON number types in submitted FHIR."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("FHIR JSON cannot contain non-finite numbers")
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("FHIR JSON object keys must be strings")
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + fhir_json(item) for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(fhir_json(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def validation_settings(data):
    from .deployment import table
    row = table(data.get("validation", {}), "validation", {"enabled", "backend", "endpoint", "container_endpoint",
        "timeout_seconds", "token_env", "token_file", "fail_on_error", "terminology_server", "terminology_endpoint", "additional_packages", "setup_directory"})
    for key in ("enabled", "fail_on_error"):
        if type(row.get(key, False)) is not bool:
            raise ValueError(f"validation.{key} must be true or false")
    if row.get("backend", "matchbox") not in {"matchbox", "mii"}:
        raise ValueError("validation.backend must be matchbox or mii")
    timeout = row.get("timeout_seconds", 180)
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("validation.timeout_seconds must be 1..600")
    if row.get("token_env") and row.get("token_file"):
        raise ValueError("Choose validation.token_env OR token_file")
    for key in ("endpoint", "container_endpoint", "terminology_server", "terminology_endpoint", "token_env", "token_file", "setup_directory"):
        if key in row and not isinstance(row[key], str):
            raise ValueError(f"validation.{key} must be a string")
    for key in ("endpoint", "container_endpoint", "terminology_server", "terminology_endpoint"):
        if row.get(key):
            local_endpoint(row[key], resolve=False)
    extra = row.get("additional_packages", [])
    if not isinstance(extra, list) or any(not isinstance(p, str) or not PACKAGE.fullmatch(p) for p in extra):
        raise ValueError("validation.additional_packages needs pinned package#semantic-version identifiers")
    if row.get("enabled"):
        packages_for(data)
    return row


def local_endpoint(value, *, resolve=True):
    """Full resources must stay on loopback/LAN; never follow redirects or proxies."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError("Invalid local validator URL") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Validator URLs need HTTP(S), a host, and no credentials/query/fragment")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (not (address.is_private or address.is_loopback) or address.is_link_local or address.is_unspecified):
        raise ValueError("Full FHIR resources may only be sent to a local/LAN validator")
    if resolve and not address:
        addresses = [ipaddress.ip_address(result[4][0]) for result in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        if not addresses or any(not (ip.is_private or ip.is_loopback) or ip.is_link_local or ip.is_unspecified for ip in addresses):
            raise ValueError("Full FHIR resources may only be sent to a local/LAN validator")
    if not resolve and not address and host not in LOCAL_HOSTS and "." not in host:
        raise ValueError("Use a known local service name or a local/LAN validator hostname")
    return value.rstrip("/")


def packages_for(data):
    spec = data.get("specification", {})
    package, version = spec.get("IG_PACKAGE", ""), spec.get("IG_VERSION", "")
    packages = ["hl7.fhir.r4.core#4.0.1"]
    if package or version:
        identifier = f"{package}#{version}"
        if not PACKAGE.fullmatch(identifier):
            raise ValueError("Full validation needs pinned specification.IG_PACKAGE and IG_VERSION")
        packages.append(identifier)
    packages += data.get("validation", {}).get("additional_packages", [])
    return list(dict.fromkeys(packages))


def target_profile(config):
    spec = config.data.get("specification", {})
    canonical = spec.get("IG_CANONICAL", "")
    version = spec.get("PROFILE_VERSION", "")
    if spec.get("STRUCTURE_DEFINITION"):
        resource = json.loads(config.path_value(spec["STRUCTURE_DEFINITION"]).read_text())
        if resource.get("resourceType") != "StructureDefinition" or resource.get("type") != config.kind:
            raise ValueError("StructureDefinition type must match general.RESOURCE_TYPE")
        if canonical and canonical != resource.get("url") or version and version != resource.get("version"):
            raise ValueError("Configured profile canonical/edition differs from the StructureDefinition")
        canonical, version = resource.get("url", ""), resource.get("version", "")
    if not canonical:
        return f"http://hl7.org/fhir/StructureDefinition/{config.kind}|4.0.1"
    version = version or spec.get("IG_VERSION", "")
    if not canonical.startswith(("https://", "http://")) or not version:
        raise ValueError("Full validation needs the profile canonical and business version")
    return canonical + "|" + version


def validation_copy(resource, profile, kind="Observation"):
    """Request the selected resource profile without changing the source data."""
    copy = deepcopy(resource)
    def visit(item):
        if not isinstance(item, dict):
            return
        if item.get("resourceType") == kind:
            meta = item.setdefault("meta", {})
            if isinstance(meta, dict):
                profiles = meta.setdefault("profile", [])
                if isinstance(profiles, list) and profile not in profiles:
                    profiles.append(profile)
        if item.get("resourceType") == "Bundle":
            entries = item.get("entry", [])
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and "resource" in entry:
                    visit(entry["resource"])
        contained_resources = item.get("contained", [])
        for contained in contained_resources if isinstance(contained_resources, list) else []:
            visit(contained)
    visit(copy)
    return copy


def outcome_issues(response):
    if isinstance(response, dict) and response.get("resourceType") == "OperationOutcome":
        issues = response.get("issue", [])
    elif isinstance(response, list) and response:
        issues = [issue for outcome in response for issue in outcome_issues(outcome)]
    else:
        raise ValueError("Validator returned no FHIR OperationOutcome")
    if not isinstance(issues, list) or not issues or any(not isinstance(issue, dict) or issue.get("severity") not in {"fatal", "error", "warning", "information"}
            or not isinstance(issue.get("code"), str) or not issue.get("code") for issue in issues):
        raise ValueError("Validator returned an invalid OperationOutcome.issue")
    return issues


class FhirValidator:
    def __init__(self, config):
        self.kind = config.kind
        self.config = config
        self.settings = validation_settings(config.data)
        self.backend = self.settings.get("backend", "matchbox")
        endpoint = self.settings.get("container_endpoint") if os.getenv("SQC_IN_CONTAINER") == "1" else None
        defaults = {"matchbox": "http://127.0.0.1:8081/matchboxv3/fhir", "mii": "http://127.0.0.1:8083"}
        self.endpoint = local_endpoint(endpoint or self.settings.get("endpoint") or defaults[self.backend], resolve=False)
        self.profile = target_profile(config)
        self.packages = packages_for(config.data)
        self.token = config.deployment.secret(self.settings, "token")
        self.opener = build_opener(ProxyHandler({}), _NoModelRedirects())

    def validate(self, resource):
        submitted = validation_copy(resource, self.profile, self.kind)
        body = fhir_json(submitted).encode()
        original = fhir_json(resource).encode()
        provenance = {"backend": self.backend, "endpoint": self.endpoint, "requested_profile": self.profile,
            "requested_packages": self.packages, "resource_type": resource.get("resourceType"),
            "source_resource_sha256": hashlib.sha256(original).hexdigest(),
            "submitted_resource_sha256": hashlib.sha256(body).hexdigest(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(), "source_unchanged": True}
        try:
            if len(body) > MAX_DOCUMENT_BYTES:
                raise ValueError("FHIR validation document exceeds the 32 MiB request budget")
            local_endpoint(self.endpoint)
            if self.backend == "matchbox":
                root_profile = self.profile if resource.get("resourceType") == "Observation" else "http://hl7.org/fhir/StructureDefinition/" + str(resource.get("resourceType", "")) + "|4.0.1"
                ig = next((package for package in self.packages if not package.startswith("hl7.fhir.r4.core#")), self.packages[0])
                url = self.endpoint + "/$validate?" + urlencode({"profile": root_profile, "ig": ig})
            else:
                url = self.endpoint + "/validateResource"
            headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
            if self.token:
                headers["Authorization"] = "Bearer " + self.token
            request = Request(url, body, headers, method="POST")
            try:
                response = self.opener.open(request, timeout=self.settings.get("timeout_seconds", 180))
            except HTTPError as exc:
                if exc.code not in {400, 422}:
                    raise
                response = exc
            with response:
                raw = response.read(MAX_DOCUMENT_BYTES + 1)
                if len(raw) > MAX_DOCUMENT_BYTES:
                    raise ValueError("FHIR validation response exceeds its size budget")
            outcome = json.loads(raw)
            issues = outcome_issues(outcome)
            counts = {severity: sum(issue["severity"] == severity for issue in issues) for severity in ("fatal", "error", "warning", "information")}
            unresolved = any(issue["severity"] in {"warning", "error", "fatal"} and re.search(
                r"(?:unable|cannot|could not|can't|failed) to (?:resolve|find|load|validate|check)|"
                r"(?:profile|code.?system|value.?set).*(?:not found|not known|unknown|not available|could not be resolved)|"
                r"terminology.*(?:unavailable|not available|not supported)",
                str(issue.get("diagnostics", "")) + " " + str((issue.get("details") or {}).get("text", "")), re.I)
                for issue in issues)
            state = "invalid" if counts["fatal"] or counts["error"] else "incomplete" if unresolved else "passed_with_warnings" if counts["warning"] else "passed"
            return {**provenance, "state": state, "counts": counts, "operation_outcome": outcome,
                    "unresolved_validation_checks": unresolved,
                    "response_sha256": hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            return {**provenance, "state": "unavailable", "error_type": type(exc).__name__}


def validate_input(config, path, output, **reader_options):
    """Validate whole Bundles before aggregation; NDJSON documents retain their scope."""
    from .export_review import _file_payloads
    validator = FhirValidator(config)
    counts = {"passed": 0, "passed_with_warnings": 0, "invalid": 0, "incomplete": 0, "unavailable": 0}
    issue_counts = {key: 0 for key in ("fatal", "error", "warning", "information")}
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        def documents():
            for location, resource in _file_payloads(Path(path), **reader_options):
                if isinstance(resource, list):
                    for index, item in enumerate(resource):
                        yield location + ":item:" + str(index), item
                else:
                    yield location, resource
        for location, resource in documents():
            if not isinstance(resource, dict) or not resource.get("resourceType"):
                raise ValueError("Validation input must contain actual FHIR resources")
            result = validator.validate(resource)
            counts[result["state"]] += 1
            for severity, count in result.get("counts", {}).items():
                issue_counts[severity] += count
            handle.write(json.dumps({"document": location, **result}, ensure_ascii=False) + "\n")
    if not sum(counts.values()):
        raise ValueError("FHIR input contains no validation documents")
    state = next((state for state in ("unavailable", "invalid", "incomplete", "passed_with_warnings") if counts[state]), "passed")
    return {"state": state, "backend": validator.backend, "endpoint": validator.endpoint,
        "profile": validator.profile, "packages": validator.packages, "documents": counts, "issues": issue_counts,
        "scope": "Full documents before element selection/aggregation; Bundles stay intact, array/NDJSON resources are validated independently. CSV resource_json cells retain the same scope.",
        "operation_outcomes": Path(output).name, "source_unchanged": True}


def initialise_validator(config_path, *, overwrite=False):
    from .batch import BatchConfiguration
    config = BatchConfiguration(config_path, research=False)
    settings = validation_settings(config.data)
    packages = packages_for(config.data)
    target_profile(config)
    tx = settings.get("terminology_server", "http://blaze:8080/fhir")
    guides = {"ig" + str(index): {"name": identifier.split("#")[0], "version": identifier.split("#")[1]} for index, identifier in enumerate(packages)}
    # JSON is valid YAML, avoiding another runtime dependency or YAML interpolation.
    application = {"server": {"servlet": {"context-path": "/matchboxv3"}},
        "spring": {"jpa": {"properties": {"hibernate.hbm2ddl.auto": "none"}}},
        "hapi": {"fhir": {"implementationguides": guides}},
        "matchbox": {"fhir": {"context": {"fhirVersion": "4.0.1", "txServer": tx,
            "httpReadOnly": True, "txUseEcosystem": False}, "validation": {"save-statistics": False, "analyzeErrorsWithLlm": False}}}}
    folder = config.path_value(settings.get("setup_directory", "local/validation"))
    build_application = deepcopy(application)
    build_application["spring"]["jpa"]["properties"] = {"hibernate.hbm2ddl.auto": "create",
        "jakarta.persistence.sql-load-script-source": "file:/config/matchbox-schema.sql"}
    outputs = {folder / "matchbox/application.yaml": json.dumps(application, indent=2) + "\n",
        folder / "matchbox/build-application.yaml": json.dumps(build_application, indent=2) + "\n",
        folder / "matchbox/Dockerfile": Path(__file__).with_name("matchbox.Dockerfile").read_text(),
        # Matchbox 4.1.21's inventory column is shorter than real MII datatype URIs.
        # Only the ephemeral validator inventory is widened; IG resources are unchanged.
        folder / "matchbox/matchbox-schema.sql": "ALTER TABLE mb_installed_struct_def ALTER COLUMN type VARCHAR(2048);\n",
        folder / "matchbox/fhir-settings.json": json.dumps({"ssrfProtectionEnabled": True, "servers": [
            {"url": tx, "type": "fhir", "authenticationType": "none", "allowHttp": tx.startswith("http://"),
             "allowPrivateNetwork": True}]}, indent=2) + "\n",
        folder / "mii.env": 'FHIR_VERSION=4.0.1\nFHIR_CACHE_SEED=true\nTX_LOG=\nJAVA_OPTS=-Xmx2g\nTX_SERVER=' + tx + '\nIG_PARAMS="' + " ".join("-ig " + identifier for identifier in packages) + '"\n'}
    for path, content in outputs.items():
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("Validator setup paths must not contain symlinks")
        if path.exists() and path.read_text() != content and not overwrite:
            raise ValueError("Validator configuration changed; rerun validator-init with --overwrite and rebuild")
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o600)
    return {"state": "prepared", "profile": target_profile(config), "packages": packages,
        "files": [str(path) for path in outputs], "next": "Build/start the selected local validation service; no resources have been validated yet"}


def validate_fhir(config_path, input_path=None, output=None):
    from .batch import BatchConfiguration
    config = BatchConfiguration(config_path, research=False)
    path = config.path_value(input_path or config.input.get("path"))
    if input_path is None and not config.full_fhir_input:
        raise ValueError("CSV/DB tuples are not full FHIR resources; give validate-fhir --input a FHIR JSON/NDJSON file")
    target = config.path_value(output or "output/fhir-validation.jsonl")
    summary = target.with_suffix(".summary.json")
    if target.exists() or summary.exists() or target == path or target == config.path or summary == path or summary == config.path or path.is_dir() and target.is_relative_to(path):
        raise ValueError("Choose a fresh validation output outside the input")
    target.parent.mkdir(parents=True, exist_ok=True)
    options = config.fhir_reader_options if config.fhir_csv and path == config.path_value(config.input["path"]) else {}
    result = validate_input(config, path, target, **options)
    target.chmod(0o600)
    summary.write_text(json.dumps({**result, "configuration_sha256": config.deployment.sha256,
        "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}, indent=2) + "\n")
    summary.chmod(0o600)
    return {**result, "output": str(target), "summary": str(summary)}


def initialise_terminology(config_path):
    """Install the explicitly supplied real ValueSets/complete CodeSystems in local TX."""
    from .batch import BatchConfiguration
    from .terminology_router import artifact_files
    config = BatchConfiguration(config_path, research=False)
    settings = validation_settings(config.data)
    endpoint = settings.get("terminology_server", "http://blaze:8080/fhir") if os.getenv("SQC_IN_CONTAINER") == "1" else settings.get("terminology_endpoint", "http://127.0.0.1:8082/fhir")
    endpoint = local_endpoint(endpoint)
    paths = [config.path_value(value) for value in config.data.get("specification", {}).get("VALUE_SETS", [])]
    paths += [config.path_value(row["path"]) for row in config.data.get("lookups", {}).values()
              if row.get("enabled") and row.get("type") == "fhir_artifacts"]
    files = list(dict.fromkeys(file for path in paths for file in artifact_files(path)))
    opener = build_opener(ProxyHandler({}), _NoModelRedirects())
    installed = []
    for path in files:
        raw = path.read_bytes()
        resource = json.loads(raw)
        kind = resource.get("resourceType")
        if kind not in {"ValueSet", "CodeSystem"} or kind == "CodeSystem" and resource.get("content") != "complete":
            continue
        canonical, version = resource.get("url"), resource.get("version", "")
        if not canonical:
            raise ValueError("Terminology resources need their actual canonical URL")
        identifier = "sqc-" + hashlib.sha256((canonical + "|" + version).encode()).hexdigest()[:48]
        resource["id"] = identifier
        body = fhir_json(resource).encode()
        request = Request(endpoint + "/" + kind + "/" + identifier, body,
            {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}, method="PUT")
        with opener.open(request, timeout=settings.get("timeout_seconds", 180)) as response:
            if response.status not in {200, 201}:
                raise ValueError("Terminology installation was not confirmed")
        installed.append({"resource_type": kind, "canonical": canonical, "version": version,
            "source": str(path), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "submitted_sha256": hashlib.sha256(body).hexdigest(), "id": identifier})
    if not installed:
        raise ValueError("No real ValueSets or complete CodeSystems were explicitly supplied")
    receipt = config.path_value(settings.get("setup_directory", "local/validation")) / "terminology-installation.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = {"state": "installed", "endpoint": endpoint, "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "configuration_sha256": config.deployment.sha256, "resources": installed,
        "scope": "Definition installation, not proof of membership or clinical correctness"}
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    receipt.chmod(0o600)
    return result
