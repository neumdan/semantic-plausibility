"""Public laboratory export workflow; local by default, review proposals only."""

from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import csv
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from threading import RLock
from urllib.parse import urlsplit
import uuid

from .audit import Checker, encoded, offline_services, payload_records
from .batch import BatchResult, records, selected_elements
from .deployment import environment
from .export_grouping import ExportGroups
from .export_report import (
    FIELDS,
    review_row,
    AuditLog,
    READINESS_DEFINITION,
    grade,
    ratio,
    semantic_signature,
    versions,
    write_html,
)
from .support import _csv_text, _input_hash, read_contexts
from .observation_quality import LoincTerm
from .terminology_router import TerminologyRouter, artifact_files, load_sources
from .review_feedback import REVIEW_FIELDS, ReviewRegistry
from .report_sources import SET_FIELDS, review_set_fields

_CSV_LOCK = RLock()


def csv_rows(path, *, delimiter=",", required=()):
    """Bounded CSV parsing shared by preflight and the full-resource reader."""
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise InputContractError("CSV delimiter must be one character")
    # The csv module's limit is process-wide; restore it even on malformed input.
    with _CSV_LOCK:
        previous = csv.field_size_limit(32 * 1024 * 1024)
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, delimiter=delimiter, strict=True)
                header = next(reader, [])
                if not header or len(header) != len(set(header)) or any(not name for name in header):
                    raise InputContractError("CSV headers must be unique and nonempty")
                missing = set(required) - set(header)
                if missing:
                    raise InputContractError("Missing configured CSV columns: " + ", ".join(sorted(missing)))
                for line, values in enumerate(reader, 2):
                    if not values:
                        continue
                    if len(values) != len(header):
                        raise InputContractError(f"CSV row {line} has a different number of fields than the header")
                    yield line, dict(zip(header, values))
        except csv.Error:
            raise InputContractError("Malformed CSV or field exceeds the 32 MiB parsing limit") from None
        finally:
            csv.field_size_limit(previous)

EXTRA_FIELDS = [
    *SET_FIELDS,
    "fhir_validation_state",
    "fhir_validation_scope",
    "semantic_signature",
    "source_namespace",
    "source_id",
    "source_ref",
    "measurement_path",
    "n_numeric",
    "n_missing",
    "n_invalid",
    "n_censored",
    "statistics_availability",
    "value_mean",
    "value_sd",
    "value_se",
    "value_median",
    "value_q1",
    "value_q3",
    "value_iqr",
    "value_distribution_json",
    "value_distribution_sha256",
    "lookup_credibility",
    "code_verified",
    "unit_verified",
    "value_numeric_ready",
    "reference_numeric_ready",
    "mii_numeric_set_ready",
    "clinical_value_evidence",
    "clinical_reference_evidence",
    "findings_json",
    "suggested_code",
    "suggested_code_system",
    "coding_proposal_json",
    "fhir_mapping_proposal_json",
    "analyte_name",
    "name_check",
    "unit_label_check",
    "name_evidence_json",
    "suggested_name",
    "canonical_name",
    "suggestion_state",
    "laboratory_suggestions_json",
    "reference_candidates_json",
    "study_evidence_json",
    "reported_measurements_json",
    "source_code_check",
    "source_unit_check",
    "source_code_unit_fit",
    "source_reference_check_json",
    "mapping_review_state",
    "approved_mapping_ready",
    "clinician_mapping_json",
    *REVIEW_FIELDS,
]


class InputContractError(ValueError):
    """Safe diagnostics authored by this runner, without source values or credentials."""


def reject_json_constant(value):
    raise InputContractError("FHIR JSON must not contain non-finite number literals")


def encoding_issues(resource):
    from decimal import Decimal

    issues = []
    for item in [resource, *resource.get("component", [])]:
        quantity = item.get("valueQuantity", {})
        if not isinstance(quantity, dict):
            raise InputContractError("Observation.valueQuantity must be an object")
        if "value" in quantity and (
            isinstance(quantity["value"], bool) or not isinstance(quantity["value"], (int, float, Decimal))
        ):
            issues.append("FHIR_Quantity_value_not_JSON_number")
        ranges = item.get("referenceRange", [])
        if not isinstance(ranges, list):
            raise InputContractError("Observation.referenceRange must be an array")
        for interval in ranges:
            if not isinstance(interval, dict):
                raise InputContractError("Reference intervals must be objects")
            for name in ("low", "high"):
                bound = interval.get(name, {})
                if not isinstance(bound, dict):
                    raise InputContractError("Reference bounds must be Quantity objects")
                if "value" in bound and (
                    isinstance(bound["value"], bool) or not isinstance(bound["value"], (int, float, Decimal))
                ):
                    issues.append("FHIR_reference_bound_not_JSON_number")
    if resource.get("status") and resource["status"] not in {
        "registered",
        "preliminary",
        "final",
        "amended",
        "corrected",
        "cancelled",
        "entered-in-error",
        "unknown",
    }:
        issues.append("FHIR_Observation_status_invalid")
    return sorted(set(issues))


class UnknownUcum:
    source = "no_local_unit_evidence"

    def is_valid(self, unit):
        return None

    def factor(self, source, target):
        return None


class UnknownLoinc:
    def lookup(self, code, version=""):
        return None


class ReliableLookup:
    """Cache failed requests too; stop a failing source after three consecutive failures."""

    def __init__(self, client, *, circuit_breaker=True):
        self.client, self.cache, self.failures = client, {}, 0
        self.circuit_breaker = circuit_breaker
        self.source = getattr(client, "source", getattr(client, "base_url", "configured_lookup"))
        self.errors = []

    def __getattr__(self, name):
        if name not in {"is_valid", "factor", "lookup"}:
            return getattr(self.client, name)

        def call(*args, **kwargs):
            key = (name, args, tuple(sorted(kwargs.items())))
            if key in self.cache:
                return self.cache[key]
            if self.circuit_breaker and self.failures >= 3:
                return None
            try:
                result = getattr(self.client, name)(*args, **kwargs)
                self.failures = 0
            except Exception as exc:
                self.failures += 1
                self.errors.append({"operation": name, "error_type": type(exc).__name__})
                result = None
            self.cache[key] = result
            return result

        return call


def _file_payloads(path, *, resource_column="", delimiter=","):
    if resource_column:
        from decimal import Decimal
        if path.is_symlink() or not path.is_file():
            raise ValueError("FHIR CSV exports must be regular files")
        for line, row in csv_rows(path, delimiter=delimiter, required=[resource_column]):
            if len(row[resource_column].encode("utf-8")) > 32 * 1024 * 1024:
                raise InputContractError(f"FHIR JSON in CSV row {line} exceeds 32 MiB")
            try:
                payload = json.loads(row[resource_column], parse_float=Decimal, parse_constant=reject_json_constant)
            except (ValueError, RecursionError):
                raise InputContractError(f"Invalid FHIR JSON in CSV row {line}") from None
            yield f"file:{path.name}:row:{line}", payload
        return
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in {".ndjson", ".jsonl"})
        if not files:
            raise ValueError("FHIR export directory contains no supported files")
    else:
        files = [path]
    for source in files:
        if source.is_symlink() or not source.is_file():
            raise ValueError("FHIR exports must be regular files")
        with source.open(encoding="utf-8-sig") as handle:
            if source.suffix.lower() in {".ndjson", ".jsonl"}:
                for line, text in enumerate(handle, 1):
                    if text.strip():
                        from decimal import Decimal

                        yield (
                            f"file:{source.name}:line:{line}",
                            json.loads(text, parse_float=Decimal, parse_constant=reject_json_constant),
                        )
            else:
                from decimal import Decimal

                yield (
                    f"file:{source.name}",
                    json.load(handle, parse_float=Decimal, parse_constant=reject_json_constant),
                )


def _resources(payload, location):
    if isinstance(payload, list):
        for index, resource in enumerate(payload):
            yield from _resources(resource, f"{location}:item:{index}")
    elif isinstance(payload, dict) and payload.get("resourceType") == "Bundle":
        entries = payload.get("entry", [])
        if not isinstance(entries, list):
            raise ValueError("Bundle.entry must be an array")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError("Bundle entries must be objects")
            if entry.get("resource"):
                yield f"{location}:entry:{index}", entry["resource"], entry.get("fullUrl", "")
    elif isinstance(payload, dict) and isinstance(payload.get("resourceType"), str):
        yield location, payload, ""
    else:
        raise ValueError("FHIR input needs resources, Bundles or a resource array")


def fhir_records(path, context, folder, **reader_options):
    import sqlite3

    # Index only exported specimen metadata. Patient resources/identifiers are not copied.
    with closing(sqlite3.connect(folder / "references.sqlite")) as refs:
        refs.execute("CREATE TABLE refs (reference TEXT PRIMARY KEY, payload TEXT)")
        refs.execute("CREATE TABLE observation_ids (id TEXT PRIMARY KEY)")
        for location, payload in _file_payloads(path, **reader_options):
            for _, resource, full_url in _resources(payload, location):
                if resource.get("resourceType") == "Specimen":
                    names = [full_url, "Specimen/" + str(resource.get("id", ""))]
                    metadata = encoded(
                        {
                            "type": resource.get("type", {}),
                            "collection_method": resource.get("collection", {}).get("method", {}),
                        }
                    )
                    for name in names:
                        if name:
                            previous = refs.execute(
                                "SELECT payload FROM refs WHERE reference=?", (name,)
                            ).fetchone()
                            if previous and previous[0] != metadata:
                                raise ValueError(
                                    "Export contains conflicting specimen versions; select one version"
                                )
                            refs.execute("INSERT OR IGNORE INTO refs VALUES (?,?)", (name, metadata))
        for location, payload in _file_payloads(path, **reader_options):
            for source_ref, resource, _ in _resources(payload, location):
                if resource.get("resourceType") != "Observation":
                    yield None
                    continue
                resource = deepcopy(resource)
                resource["_plausibility_input_issues"] = encoding_issues(resource)
                identifier = resource.get("id")
                if identifier:
                    if refs.execute("SELECT 1 FROM observation_ids WHERE id=?", (identifier,)).fetchone():
                        raise ValueError(
                            "FHIR export repeats an Observation ID; select one resource version upstream"
                        )
                    refs.execute("INSERT INTO observation_ids VALUES (?)", (identifier,))
                local = dict(context)
                reference = resource.get("specimen", {}).get("reference", "")
                if reference.startswith("#"):
                    specimen = next(
                        (
                            r
                            for r in resource.get("contained", [])
                            if r.get("id") == reference[1:] and r.get("resourceType") == "Specimen"
                        ),
                        None,
                    )
                    metadata = encoded({"type": specimen.get("type", {})}) if specimen else ""
                else:
                    found = refs.execute(
                        "SELECT payload FROM refs WHERE reference=?", (reference,)
                    ).fetchone()
                    metadata = found[0] if found else ""
                # Native coding is preserved, not equated with a LOINC system-axis token.
                if metadata:
                    local["specimen_metadata"] = metadata
                for record in payload_records(resource, context=local, source_ref=source_ref):
                    record.input_granularity = "individual"
                    yield record


def validate_csv(config, path):
    for _ in csv_rows(path, delimiter=config.input.get("delimiter", ","), required=config.mapping["fields"].values()):
        pass
    return True


def local_model_only(config):
    for entry in config.data.get("models", {}).values():
        endpoint = entry.get("base_url") or os.getenv(entry.get("base_url_env", ""), "")
        host = urlsplit(endpoint).hostname
        if entry.get("model", "").endswith("-cloud") or entry.get("model", "").endswith(":cloud"):
            raise ValueError("V3 requires locally hosted model weights, not a cloud model alias")
        approved = {"localhost", "ollama", "host.containers.internal", "host.docker.internal"}
        try:
            private = ipaddress.ip_address(host or "").is_private
        except ValueError:
            private = False
        if host and host not in approved and not private:
            raise ValueError("V3 models must use a local/LAN endpoint, not a public cloud model")


def source_proposals(config, checker, payload):
    path = config.data.get("quality", {}).get("concept_map")
    if not path:
        return []
    document = json.loads(config.path_value(path).read_text())
    if document.get("resourceType") != "ConceptMap":
        raise ValueError("quality.concept_map must be a FHIR R4 ConceptMap")
    proposals = []
    for coding in payload.get("code", {}).get("coding", []):
        for group in document.get("group", []):
            if group.get("source") != coding.get("system"):
                continue
            if group.get("sourceVersion") and group["sourceVersion"] != coding.get("version"):
                continue
            for element in group.get("element", []):
                if element.get("code") != coding.get("code"):
                    continue
                for target in element.get("target", []):
                    if target.get("equivalence") not in {"equal", "equivalent"}:
                        continue
                    system, code = group.get("target", ""), target.get("code", "")
                    version = group.get("targetVersion", "")
                    term = checker.loinc.lookup(code, version) if system == "http://loinc.org" else None
                    valid = (
                        bool(term)
                        if system == "http://loinc.org"
                        else checker.terminology.check(system, code, version).get("state") == "valid"
                        if checker.terminology
                        else False
                    )
                    proposals.append(
                        {
                            "source_system": coding.get("system"),
                            "source_code": coding.get("code"),
                            "target_system": system,
                            "target_code": code,
                            "target_version": version,
                            "equivalence": target["equivalence"],
                            "target_verified": valid,
                            "conceptmap_url": document.get("url"),
                            "conceptmap_version": document.get("version"),
                            "review_required": True,
                            "transformation": None,
                        }
                    )
    return proposals


def mapping_proposal(row, evidence, proposals):
    return {
        "kind": "mapping_plan_not_a_patient_Observation",
        "resourceType": "Observation",
        "target_profile": row["ig_canonical"] or "http://hl7.org/fhir/StructureDefinition/Observation",
        "source_code": {"system": row["code_system"], "code": row["code"], "version": row["code_version"]},
        "coding_candidates": proposals,
        "unit_candidate": row["suggested_unit"] or row["unit_code"],
        "unit_system": "http://unitsofmeasure.org" if row["suggested_unit"] else row["unit_system"],
        "quantity_transformation": evidence.get("recommendation", {}).get("transformation"),
        "review_required": True,
        "missing_patient_resource_fields": ["subject", "effective[x]", "identifier", "status_as_applicable"],
        "aggregate_statistics_are_not_patient_measurements": True,
    }


def reference_artifacts(config):
    paths = {
        "reference_pack": config.input.get("snapshot"),
        "context": config.input.get("context_file"),
        "profile": config.data.get("specification", {}).get("STRUCTURE_DEFINITION")
        or config.data.get("specification", {}).get("PROFILE_FILE"),
        "concept_map": config.data.get("quality", {}).get("concept_map"),
        "review_registry": config.data.get("quality", {}).get("review_registry"),
        "unit_catalog": config.data.get("research", {}).get("unit_catalog")
        if config.data.get("research", {}).get("enabled")
        else None,
    }
    paths.update(
        {
            "lookup:" + name: entry.get("path")
            for name, entry in config.data.get("lookups", {}).items()
            if entry.get("enabled") and entry.get("type") in {"csv", "sqlite", "fhir_artifacts"}
        }
    )
    paths.update({"value_set:" + str(index): name for index, name in enumerate(config.data.get("specification", {}).get("VALUE_SETS", []))})
    artifacts = []
    for role, value in paths.items():
        if not value:
            continue
        path = config.path_value(value)
        if role == "review_registry" and not path.exists():
            artifacts.append({"role": role, "state": "not_yet_created"})
            continue
        files = artifact_files(path) if path.is_dir() else [path]
        if path.is_dir() and (path / "installation.json").exists():
            files.append(path / "installation.json")
        digest = hashlib.sha256()
        for file in files:
            if file.is_symlink() or not file.is_file():
                raise ValueError("Local reference artifacts must be regular files")
            digest.update(encoded([str(file.relative_to(path)) if path.is_dir() else file.name, _input_hash(file)]).encode())
        artifacts.append({"role": role, "sha256": digest.hexdigest(), "files": len(files)})
    return artifacts


def reference_pack(checker, original_kind):
    ucum, loinc = checker.ucum, checker.loinc
    verdicts = {
        args[0]: value
        for (name, args, _), value in getattr(ucum, "cache", {}).items()
        if name == "is_valid" and type(value) is bool
    }
    terms = {}
    for (name, args, _), value in getattr(loinc, "cache", {}).items():
        if name == "lookup" and isinstance(value, LoincTerm):
            data = asdict(value)
            terms[(data["code"], data.get("version", ""))] = data
    conversions = [
        {"source": args[0], "target": args[1], "factor": str(value)}
        for (name, args, _), value in getattr(ucum, "cache", {}).items()
        if name == "factor" and value is not None
    ]
    return {
        "format_version": 1,
        "evidence_kind": original_kind,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": "queried_codes_units_and_verified_linear_factors_only",
        "ucum": verdicts,
        "loinc": list(terms.values()),
        "conversions": conversions,
        "provenance": {"ucum_source": ucum.source, "loinc_source": loinc.source, "software": versions()},
    }


def review_export(config):
    target = config.path_value(config.output.get("path", "output/laboratory-review.csv"))
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(".run.lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError(
            "Another run owns this output prefix; choose a different path or resolve its stale run lock"
        ) from None
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        if config.kind != "Observation":
            from .medication_report import review_medication
            return review_medication(config)
        return _review_export(config)
    finally:
        lock.unlink(missing_ok=True)


def configured_checker(config, prepared, *, profile=None, research=None, options=None):
    options = options or prepared.audit.checker_options()
    enabled = config.data.get("research", {}).get("enabled", False) if research is None else research
    if config.network_mode == "offline":
        clients = (
            offline_services(prepared.audit.snapshot)
            if prepared.audit.snapshot
            else (UnknownUcum(), UnknownLoinc())
        )
        entries = (
            load_sources(prepared.audit.terminology_sources)
            if prepared.audit.terminology_sources
            else []
        )
        checker = Checker(
            **{**options, "snapshot": None},
            ucum=ReliableLookup(clients[0], circuit_breaker=False),
            loinc=ReliableLookup(clients[1], circuit_breaker=False),
            terminology=TerminologyRouter(entries),
        )
    else:
        if prepared.audit.snapshot:
            raise ValueError("Choose offline reference pack OR connected lookups, not both")
        checker = Checker(
            **{
                **options,
                "research": enabled,
                "models_file": prepared.models_file,
                "model_alias": prepared.model_alias,
                "research_scope": prepared.scope,
            }
        )
        active_types = {
            entry.get("type")
            for entry in config.data.get("lookups", {}).values()
            if entry.get("enabled", False)
        }
        if "ucum" not in active_types:
            checker.ucum = UnknownUcum()
        if "loinc" not in active_types:
            checker.loinc = UnknownLoinc()
        checker.ucum, checker.loinc = ReliableLookup(checker.ucum), ReliableLookup(checker.loinc)
        if checker.spelling:
            checker.spelling.ucum = checker.ucum
    if profile is not None:
        checker.custom = profile
    checker.review_catalogue_fingerprint = [row for row in reference_artifacts(config)
        if row["role"].startswith(("lookup:", "value_set:")) or row["role"] == "reference_pack"]
    return checker


def _review_export(config):
    local_model_only(config)
    target = config.path_value(config.output.get("path", "output/laboratory-review.csv"))
    registry_path = config.data.get("quality", {}).get("review_registry")
    registry = ReviewRegistry(config.path_value(registry_path)) if registry_path else None
    paths = {
        "csv": target,
        "summary": target.with_suffix(".summary.json"),
        "report": target.with_suffix(".html"),
        "log": target.with_suffix(".audit.jsonl"),
        "evidence": target.with_suffix(".evidence.jsonl"),
    }
    if config.validation.get("enabled") and config.full_fhir_input:
        paths["fhir_validation"] = target.with_suffix(".fhir-validation.jsonl")
    if config.output.get("lookup_pack"):
        paths["lookup_pack"] = config.path_value(config.output["lookup_pack"])
        if paths["lookup_pack"].parent != target.parent:
            raise ValueError(
                "The captured lookup pack must use the report directory for integrity verification"
            )
    if len(set(paths.values())) != len(paths):
        raise ValueError("Output paths must not collide")
    protected = {
        config.path,
        *[
            config.path_value(value)
            for value in (
                config.input.get("path"),
                config.input.get("snapshot"),
                config.input.get("manifest"),
                config.data.get("specification", {}).get("STRUCTURE_DEFINITION"),
                config.data.get("quality", {}).get("concept_map"),
                config.data.get("quality", {}).get("review_registry"),
            )
            if value
        ],
    }
    protected.update(
        config.path_value(entry["path"])
        for entry in config.data.get("lookups", {}).values()
        if entry.get("path")
    )
    protected.update(config.path_value(name) for name in config.data.get("specification", {}).get("VALUE_SETS", []))
    protected.update(
        config.path_value(value)
        for value in (
            config.input.get("context_file"),
            config.data.get("specification", {}).get("PROFILE_FILE"),
            config.data.get("research", {}).get("unit_catalog"),
        )
        if value
    )
    if set(paths.values()) & protected or any(
        source.is_dir() and path.is_relative_to(source) for source in protected for path in paths.values()
    ):
        raise ValueError("Outputs must not overwrite configuration, source data or lookup evidence")
    if not config.output.get("overwrite", False) and any(path.exists() for path in paths.values()):
        raise ValueError("Output already exists; choose a new path or set output.overwrite=true")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    updates = (
        {"SQC_BATCH_CDS_DSN": config.toolchain_database()}
        if config.input_type == "postgres" and config.database.get("PATH_TO_DB_CONFIG_TOML")
        else {}
    )
    with tempfile.TemporaryDirectory(prefix=".lab-review-", dir=target.parent) as name:
        folder = Path(name)
        log = AuditLog(folder / "audit.jsonl", run_id)
        phase, groups, checker = "configuration", None, None
        imported_policy = None
        try:
            log.event(
                "started",
                software=versions(),
                configuration_sha256=config.deployment.sha256,
                network_mode=config.network_mode,
                mode=config.output.get("mode", "distinct"),
                operation=config.operation,
                output_path=str(target),
            )
            with environment(updates), config.deployment.prepare() as prepared:
                options = prepared.audit.checker_options()
                if config.data.get("specification", {}).get("STRUCTURE_DEFINITION"):
                    from .profile_import import import_structure_definition

                    policy = import_structure_definition(
                        config.path_value(config.data["specification"]["STRUCTURE_DEFINITION"])
                    )
                    imported_policy = {
                        "canonical": policy["canonical"],
                        "version": policy["guide_version"],
                        **policy["import_provenance"],
                    }
                    policy_file = folder / "selected-policy.json"
                    policy_file.write_text(json.dumps(policy))
                    options["profile_config"] = policy_file
                enabled = config.data.get("research", {}).get("enabled", False)
                checker = configured_checker(config, prepared, options=options, research=enabled)
                # Load policies early, before an expensive source scan.
                selected_profile = checker.profile_for("Observation")
                declared_canonical = config.data.get("specification", {}).get("IG_CANONICAL")
                if (
                    declared_canonical
                    and selected_profile.canonical
                    and declared_canonical != selected_profile.canonical
                ):
                    raise ValueError("Declared IG canonical differs from the selected policy/profile")
                artifacts = reference_artifacts(config)
                from .profile_lookups import terminology_plan
                routes = terminology_plan(config, selected_profile)
                log.event("reference_artifacts", artifacts=artifacts, imported_profile=imported_policy)
                source = prepared.source
                input_hash = (
                    _input_hash(source["path"])
                    if config.input_type in {"csv", "fhir_file", "sqlite"} and source.get("path")
                    else None
                )
                snapshot = prepared.audit.snapshot
                kind = (
                    json.loads(snapshot.read_text()).get("evidence_kind", "local_snapshot")
                    if snapshot and snapshot.suffix == ".json"
                    else "local_snapshot"
                    if snapshot
                    else "no_lookup_evidence"
                    if config.network_mode == "offline"
                    else "configured_lookup_services"
                )
                phase = "input_preflight"
                fhir_validation = {"state": "not_applicable" if config.validation.get("enabled") else "disabled",
                    "scope": "Scalar CSV/database tuples are not full FHIR resources" if not config.full_fhir_input else "Full FHIR validation was not enabled"}
                if config.input_type == "csv":
                    validate_csv(config, source["path"])
                if "fhir_validation" in paths:
                    from .fhir_validation import validate_input
                    phase = "fhir_resource_validation"
                    fhir_validation = validate_input(config, source["path"], folder / "fhir-validation.jsonl", **config.fhir_reader_options)
                    fhir_validation["operation_outcomes"] = paths["fhir_validation"].name
                    log.event("fhir_resources_validated", **fhir_validation)
                manifest = (
                    json.loads(config.path_value(config.input["manifest"]).read_text())
                    if config.input.get("manifest")
                    else {}
                )
                if not isinstance(manifest, dict):
                    raise ValueError("Export manifest must be an object")
                allowed_manifest = {
                    "exported_rows",
                    "source_occurrences",
                    "description",
                    "exporter",
                    "exporter_version",
                    "selection_sha256",
                    "deduplication_policy",
                }
                if set(manifest) - allowed_manifest:
                    raise InputContractError(
                        "Export manifest contains unsupported fields; retain only the documented non-identifying metadata"
                    )
                log.event(
                    "input_preflight",
                    input_type=config.input_type,
                    granularity=config.input.get("granularity", "individual"),
                    input_sha256=input_hash,
                    field_count=len(config.mapping["fields"]),
                    evidence_kind=kind,
                )
                skipped = 0
                contexts = read_contexts(prepared.audit.context_file)

                def laboratory():
                    nonlocal skipped
                    iterator = (
                        fhir_records(source["path"], dict(prepared.audit.context), folder, **config.fhir_reader_options)
                        if config.full_fhir_input
                        else records(config, prepared)
                    )
                    for record in iterator:
                        if record is None or record.resource.get("resourceType") != "Observation":
                            skipped += 1
                            continue
                        record.context.update(contexts.get(str(record.resource.get("id") or ""), {}))
                        yield selected_elements(record, config)

                phase = "standardization_and_grouping"
                mode = config.output.get("mode", "distinct")
                groups = ExportGroups(folder, mode=mode, group_value="value" in config.group_by)
                accounting = groups.ingest(laboratory())
                expected = manifest.get("exported_rows")
                if expected is not None and (
                    type(expected) is not int
                    or expected != accounting["individual_records"] + accounting["distinct_records"]
                ):
                    raise ValueError(
                        "Export manifest row count does not match the selected Observation input"
                    )
                expected_occurrences = manifest.get("source_occurrences")
                if expected_occurrences is not None:
                    if type(expected_occurrences) is not int or expected_occurrences < 1:
                        raise InputContractError("Manifest source_occurrences must be a positive integer")
                    if (
                        not accounting["unknown_frequency_records"]
                        and expected_occurrences != accounting["known_source_occurrences"]
                    ):
                        raise ValueError("Export manifest frequency does not match known source occurrences")
                log.event("grouped", **accounting, skipped_resources=skipped)
                phase = "semantic_checks"
                all_fields = FIELDS + [
                    key
                    for key in EXTRA_FIELDS
                    if mode == "items"
                    or key not in {"source_id", "source_ref", "measurement_path"}
                ]
                preview, statuses, total, ready, weighted_ready, weighted_total, unknown_frequency = (
                    [],
                    {},
                    0,
                    0,
                    0,
                    0,
                    False,
                )
                counters = {
                    key: 0
                    for key in (
                        "code_verified",
                        "unit_verified",
                        "value_numeric_ready",
                        "reference_numeric_ready",
                    )
                }
                approved_count = 0
                csv_file, evidence_file = folder / "review.csv", folder / "evidence.jsonl"
                with (
                    csv_file.open("w", encoding="utf-8-sig", newline="") as output,
                    evidence_file.open("w", encoding="utf-8") as evidence_out,
                ):
                    writer = csv.DictWriter(output, fieldnames=all_fields)
                    writer.writeheader()
                    for key, payload, stats, metadata in groups.groups():
                        raw_row, detail = checker.assess(payload, stats)
                        detail = {
                            "run_id": run_id,
                            "tuple_id": key,
                            "metadata": payload,
                            "observed_values": stats,
                            **detail,
                        }
                        row = review_row(raw_row, detail, config)
                        row.update(fhir_validation_state=fhir_validation["state"],
                                   fhir_validation_scope=fhir_validation["scope"])
                        measurement = detail.get("report", {}).get("measurements", [{}])[0]
                        source_checks = measurement.get("source_code_checks", [])
                        names = measurement.get("name_checks", [])
                        unit_label = measurement.get("unit_label_check", {})
                        row.update(
                            analyte_name=payload["code"].get("text") or next((c.get("display") for c in payload["code"].get("coding", []) if c.get("display")), ""),
                            name_check="; ".join(check["state"] for check in names) or "not_provided",
                            unit_label_check=unit_label.get("state", "not_provided"),
                            name_evidence_json=encoded({"analyte": names, "unit": unit_label}),
                        )
                        row["profile"] = selected_profile.name
                        row.update(source_code_check=";".join(check["state"] for check in source_checks) or "unknown",
                                   source_unit_check=measurement.get("unit_check", {}).get("state", "unknown"),
                                   source_code_unit_fit=";".join(measurement.get("source_code_unit_fit", [])))
                        row["source_reference_check_json"] = encoded(measurement.get("source_reference_checks", []))
                        if not row["ig_canonical"]:
                            row["ig_canonical"] = selected_profile.canonical
                        if row["status"] == "passed" and (
                            not payload["reference_ranges"]
                            or stats["minimum"] is None
                            and stats["maximum"] is None
                        ):
                            row["status"] = "unclear"
                            row["reason"] = (
                                "The requested code-unit-value-reference set is incomplete; supplied checks are retained."
                            )
                            detail["check"]["status"] = "unclear"
                        row.update(
                            semantic_signature=semantic_signature(payload),
                            source_namespace=config.input.get(
                                "source_namespace", config.input.get("origin", "")
                            ),
                            **metadata,
                            n_numeric=stats["n_numeric"],
                            n_missing=stats["missing"],
                            n_invalid=stats["invalid"],
                            n_censored=stats["censored"],
                            statistics_availability=stats["statistics_availability"],
                            value_distribution_json=encoded(stats["distribution"])
                            if config.output.get("include_distribution", True)
                            else "",
                            value_distribution_sha256=stats["value_distribution_sha256"] or "",
                        )
                        for field in ("mean", "sd", "se", "median", "q1", "q3", "iqr"):
                            row["value_" + field] = stats[field] if stats[field] is not None else ""
                        row["measurement_path"] = metadata["item_path"]
                        row.pop("item_path", None)
                        row.update(grade(row, detail))
                        proposals = source_proposals(config, checker, payload)
                        suggestions = measurement.get("laboratory_suggestions") or {}
                        selected_code = suggestions.get("selected_code")
                        selected_candidate = next((candidate for candidate in suggestions.get("coding_candidates", []) if candidate["code"] == selected_code), None)
                        if selected_candidate and not row["suggested_unit"]:
                            row["suggested_unit"] = selected_candidate["unit_fit"].get("target") or ""
                        for candidate in suggestions.get("coding_candidates", []):
                            proposals.append({
                                "source_system": row["code_system"], "source_code": row["code"],
                                "target_system": candidate["system"], "target_code": candidate["code"],
                                "target_version": candidate["version"], "target_verified": True,
                                "equivalence": "not_established", "source": candidate["source"],
                                "method": "retrieved_candidate_exact_lookup",
                                "model_selected": candidate["code"] == selected_code,
                                "unit_fit": candidate["unit_fit"], "review_required": True,
                            })
                        unambiguous = next((p for p in proposals if p["target_code"] == selected_code), None) if selected_code else (
                            proposals[0] if len(proposals) == 1 and proposals[0].get("equivalence") in {"equal", "equivalent"} else None)
                        row.update(
                            findings_json=encoded(
                                [
                                    {"check": name, "state": row[name]}
                                    for name in (
                                        "code_check",
                                        "unit_check",
                                        "code_unit_fit",
                                        "reference_check",
                                        "value_reference_check",
                                        "name_check",
                                        "unit_label_check",
                                    )
                                ]
                            ),
                            coding_proposal_json=encoded(proposals),
                            suggested_code=unambiguous["target_code"] if unambiguous else "",
                            suggested_code_system=unambiguous["target_system"] if unambiguous else "",
                            suggested_name=suggestions.get("selected_name") or "",
                            canonical_name=next(((check["terminology"].get("metadata") or {}).get("display", "")
                                for check in measurement.get("code_checks", [])
                                if check.get("system") == "http://loinc.org" and check["terminology"]["state"] == "valid"), ""),
                            suggestion_state=suggestions.get("state", "disabled"),
                            laboratory_suggestions_json=encoded(suggestions),
                            reference_candidates_json=encoded([candidate for candidate in measurement.get("study_candidates", [])
                                if candidate["evidence_type"] in {"reference_interval", "analytical_interval", "documented_extreme"}]),
                            study_evidence_json=encoded(measurement.get("study_candidates", [])),
                            reported_measurements_json=encoded([candidate for candidate in measurement.get("study_candidates", [])
                                if candidate["evidence_type"] == "reported_measurement"]),
                            fhir_mapping_proposal_json=encoded(mapping_proposal(row, detail, proposals)),
                        )
                        approval = registry.proposal(payload, row["source_namespace"], selected_profile, checker) if registry else {"state": "not_configured", "review_required": True}
                        ready_mapping = (approval["state"] == "reused_clinician_approval" and not approval["review_required"]
                            and not stats.get("invalid") and (stats.get("minimum") is not None or stats.get("maximum") is not None)
                            and not payload.get("modifier_extensions") and not payload["quantity"].get("comparator"))
                        row.update(mapping_review_state=approval["state"], approved_mapping_ready=ready_mapping,
                                   clinician_mapping_json=encoded(approval))
                        approved_count += ready_mapping
                        detail["clinician_mapping"] = approval
                        if approval["state"] == "reused_clinician_approval":
                            approved = approval["target"]
                            row.update(suggested_code=approved["code"], suggested_code_system=approved["code_system"],
                                       suggested_unit=approved["unit"], suggested_factor=approved["factor"], suggested_name=approved["name"])
                            plan = mapping_proposal(row, detail, proposals)
                            plan.update(clinician_approved_target=approved, quantity_transformation=approval["verification"],
                                        review_required=approval["review_required"], approval_id=approval["decision_id"])
                            row["fhir_mapping_proposal_json"] = encoded(plan)
                        row.update(review_set_fields(row, detail))
                        selected = {
                            field: _csv_text(str(row[field]))
                            if isinstance(row.get(field), str)
                            else row.get(field, "")
                            for field in all_fields
                        }
                        writer.writerow(selected)
                        evidence_out.write(encoded(detail) + "\n")
                        if len(preview) < 2000:
                            preview.append(selected)
                        total += 1
                        ready += row["mii_numeric_set_ready"]
                        for field in counters:
                            counters[field] += row[field]
                        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
                        unknown_frequency |= stats["occurrences"] is None
                        if stats["occurrences"] is not None:
                            weighted_total += stats["occurrences"]
                            weighted_ready += stats["occurrences"] * row["mii_numeric_set_ready"]
                        if total % 100 == 0:
                            log.event("check_progress", assessed_groups=total, statuses=statuses)
                if not total:
                    raise ValueError("Input contains no selected laboratory measurements")
                phase = "report"
                from .review_feedback import profile_fingerprint
                summary = {
                    "format_version": 1,
                    "operation": config.operation,
                    "output_path": str(target),
                    "input_representation": "fhir_resources" if config.full_fhir_input else "mapped_columns",
                    "run_id": run_id,
                    "mode": mode,
                    "software": versions(),
                    "configuration_sha256": config.deployment.sha256,
                    "input_sha256": input_hash,
                    "reference_pack_sha256": _input_hash(snapshot) if snapshot else None,
                    "reference_artifacts": artifacts,
                    "profile_rules_sha256": profile_fingerprint(selected_profile),
                    "evidence_kind": kind,
                    "network_mode": config.network_mode,
                    "input_records": accounting["individual_records"] + accounting["distinct_records"],
                    "selected_items": accounting["semantic_items_seen"],
                    "output_rows": total,
                    "skipped_resources": skipped,
                    "accounting": accounting,
                    "statuses": statuses,
                    "metrics": {
                        "mii_numeric_set_ready": ratio(ready, total),
                        "approved_mapping_ready": ratio(approved_count, total),
                        **{key: ratio(value, total) for key, value in counters.items()},
                    },
                    "weighted_readiness": None
                    if unknown_frequency
                    else ratio(weighted_ready, weighted_total),
                    "readiness_definition": READINESS_DEFINITION,
                    "full_fhir_validation": fhir_validation["state"],
                    "fhir_validation": fhir_validation,
                    "clinical_value_and_reference_evidence": "not_established_by_numeric_checks",
                    "study": config.data.get("study", {}),
                    "export_manifest": manifest,
                    "specification": config.data.get("specification", {}),
                    "lookup_sources": {"ucum": checker.ucum.source, "loinc": checker.loinc.source},
                    "terminology_plan": routes,
                    "loinc_editions": sorted({
                        term.version for (operation, _, _), term in checker.loinc.cache.items()
                        if operation == "lookup" and isinstance(term, LoincTerm) and term.version
                    }),
                    "lookup_errors": checker.ucum.errors + checker.loinc.errors,
                    "research": {
                        "enabled": enabled, "scope": prepared.scope,
                        "sources": list(getattr(checker.evidence, "source_records", {}).values()),
                        "accepted_model_calls": getattr(getattr(checker.evidence, "llm", None), "audit_records", []),
                        "measured_values_sent_to_models": False,
                        "identifiers_sent_to_models": False,
                    },
                    "imported_profile": imported_policy,
                    "elapsed_seconds": round(time.monotonic() - started, 4),
                }
                summary["output_hashes"] = {
                    paths[alias].name: _input_hash(path)
                    for alias, path in (("csv", csv_file), ("evidence", evidence_file))
                }
                if "fhir_validation" in paths:
                    summary["output_hashes"][paths["fhir_validation"].name] = _input_hash(folder / "fhir-validation.jsonl")
                if "lookup_pack" in paths:
                    (folder / "lookup-pack.json").write_text(
                        json.dumps(
                            reference_pack(
                                checker, "captured_lookup_responses"
                            ),
                            indent=2,
                            default=str,
                        )
                        + "\n"
                    )
                    summary["output_hashes"][paths["lookup_pack"].name] = _input_hash(
                        folder / "lookup-pack.json"
                    )
                write_html(folder / "report.html", preview, summary, paths["csv"].name, paths["log"].name)
                (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
                log.event(
                    "completed",
                    output_rows=total,
                    statuses=statuses,
                    mii_numeric_set_ready=ratio(ready, total),
                    elapsed_seconds=summary["elapsed_seconds"],
                    output_hashes={
                        **summary["output_hashes"],
                        paths["summary"].name: _input_hash(folder / "summary.json"),
                        paths["report"].name: _input_hash(folder / "report.html"),
                    },
                )
                phase = "artifact_publication"
                local_files = {
                    "csv": csv_file,
                    "summary": folder / "summary.json",
                    "report": folder / "report.html",
                    "log": folder / "audit.jsonl",
                    "evidence": evidence_file,
                    "lookup_pack": folder / "lookup-pack.json",
                    "fhir_validation": folder / "fhir-validation.jsonl",
                }
                # The summary is the last published file and marks a complete run.
                for alias in [key for key in paths if key != "summary"] + ["summary"]:
                    destination = paths[alias]
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(local_files[alias], 0o600)
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
                        staging_path = Path(staged.name)
                        with local_files[alias].open("rb") as source_file:
                            shutil.copyfileobj(source_file, staged)
                    try:
                        os.replace(staging_path, destination)
                    finally:
                        staging_path.unlink(missing_ok=True)
                log.close()
                return BatchResult(
                    target,
                    summary["input_records"],
                    total,
                    statuses,
                    skipped,
                    paths["summary"],
                    paths["report"],
                    paths["log"],
                    fhir_validation["state"],
                )
        except Exception as exc:
            log.event(
                "failed",
                failed_phase=phase,
                error_type=type(exc).__name__,
                diagnostic=str(exc)
                if isinstance(exc, InputContractError)
                else "redacted_external_or_data_exception",
            )
            log.close()
            failed = target.with_name(target.stem + ".failed." + run_id + ".audit.jsonl")
            os.replace(folder / "audit.jsonl", failed)
            if isinstance(exc, InputContractError):
                raise ValueError(f"{exc}. Failure audit: {failed.name}") from None
            raise ValueError(
                f"Batch failed during {phase} ({type(exc).__name__}); see {failed.name}"
            ) from None
        finally:
            if groups:
                groups.close()
            if checker and checker.terminology:
                checker.terminology.close()
