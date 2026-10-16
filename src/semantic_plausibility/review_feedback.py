"""Versioned clinician decisions, scoped reuse and independently checked targets.

This is a local mapping registry, not model training or a patient-resource writer.
Rejected/deferred/revoked decisions never produce an executable mapping proposal.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .audit import encoded
from .fhir_quality_pipeline import FhirQualityPipeline, number, serial
from .profile_lookups import selected_profile
from .study_compare import verify_audit

REVIEW_FIELDS = ["review_decision", "reviewer", "reviewed_at", "approved_code", "approved_code_system",
    "approved_code_version", "approved_name", "approved_unit", "approved_unit_system", "approved_factor", "review_basis",
    "review_evidence"]
DECISIONS = {"approved", "rejected", "deferred", "revoked"}


def approved_mapping_ready(approval, payload, stats):
    """Readiness needs a currently reverified clinician decision, never a model vote."""
    return bool(approval.get("state") == "reused_clinician_approval"
        and approval.get("review_required") is False and not stats.get("invalid")
        and (stats.get("minimum") is not None or stats.get("maximum") is not None)
        and not payload.get("modifier_extensions") and not payload.get("quantity", {}).get("comparator"))


def profile_fingerprint(profile):
    return hashlib.sha256(encoded(asdict(profile)).encode()).hexdigest()


def verify_report_profile(config, summary):
    profile = selected_profile(config)
    if summary.get("profile_rules_sha256") != profile_fingerprint(profile):
        raise ValueError("Profile rules differ from the original check; create a new checked report")
    return profile


def mapping_scope(payload, namespace, profile, catalogue_fingerprint=()):
    if not namespace:
        raise ValueError("Set input.source_namespace before importing reusable clinician decisions")
    guard = deepcopy(payload)
    for key in ("input_issues",):
        guard.pop(key, None)
    guard.get("quantity", {}).pop("value", None)
    scope = {"source_namespace": namespace, "profile": profile.canonical or "http://hl7.org/fhir/StructureDefinition/Observation",
             "profile_version": profile.guide_version or "4.0.1", "semantic_set": guard,
             "profile_rules_sha256": profile_fingerprint(profile),
             "catalogue_fingerprint": list(catalogue_fingerprint)}
    return scope, hashlib.sha256(encoded(scope).encode()).hexdigest()


def report_details(report, *, hydrate=False):
    report = Path(report).resolve()
    verify_audit(report.with_suffix(".audit.jsonl"))
    rows = read_csv(report)
    details = {}
    for line in report.with_suffix(".evidence.jsonl").read_text().splitlines():
        item = json.loads(line)
        if item["tuple_id"] in details:
            raise ValueError("Duplicate tuple identity in report evidence")
        details[item["tuple_id"]] = item
    if len({row["tuple_id"] for row in rows}) != len(rows) or {row["tuple_id"] for row in rows} != set(details):
        raise ValueError("CSV and evidence tuple identities differ")
    if hydrate:
        rows = [{**details[row["tuple_id"]].get("report_row", row),
                 "evidence_json": encoded({key: val for key, val in details[row["tuple_id"]].items()
                                           if key != "report_row"})} for row in rows]
    return rows, details


def read_csv(path):
    from .deployment import environment
    with environment({}):
        previous = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous, 64 * 1024 * 1024))
            with Path(path).open(encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        finally:
            csv.field_size_limit(previous)


def verify_target(entry, checker, profile):
    target = entry["target"]
    code, version = target["code"], target["code_version"]
    if target["code_system"] != "http://loinc.org" or target["unit_system"] != "http://unitsofmeasure.org":
        raise ValueError("The current laboratory mapping loop targets LOINC and UCUM")
    term = checker.loinc.lookup(code, version) if version else checker.loinc.lookup(code)
    if not term or not checker.ucum.is_valid(target["unit"]):
        raise ValueError("Approved target code/version or unit could not be independently verified")
    if term.status and term.status.upper() != "ACTIVE":
        raise ValueError("An inactive target code needs a separate historical mapping policy")
    if term.scale and term.scale.casefold() not in {"qn", "quantitative"}:
        raise ValueError("A quantitative target cannot use a resolved non-quantitative LOINC scale")
    from .laboratory_suggestions import LaboratorySuggestions
    names = LaboratorySuggestions._names(term, checker.terminology)
    chosen_name = target.get("name") or term.display
    if chosen_name not in {row["name"] for row in names}:
        raise ValueError("Approved Coding display must be a sourced full name of the target code")
    pipeline = FhirQualityPipeline(checker.ucum, checker.loinc, profile=profile, terminology=checker.terminology)
    payload = entry["scope"]["semantic_set"]
    quantity = payload["quantity"]
    source_unit = quantity.get("code") or quantity.get("unit") or ""
    factor = number(target["factor"])
    if factor is None or factor <= 0:
        raise ValueError("Clinician-approved factors must be positive finite numbers")
    basis = entry["review_basis"]
    if basis not in {"identity", "normalization", "linear_conversion", "semantic_reinterpretation"}:
        raise ValueError("Choose identity, normalization, linear_conversion or semantic_reinterpretation review_basis")
    if basis in {"identity", "normalization"} and factor != 1:
        raise ValueError("Identity/normalization approval must use factor 1")
    mathematical = None
    if quantity.get("system") == "http://unitsofmeasure.org":
        try:
            if checker.ucum.is_valid(source_unit):
                mathematical = checker.ucum.factor(source_unit, target["unit"])
        except Exception:
            pass
    if mathematical is not None and mathematical != factor and basis != "semantic_reinterpretation":
        raise ValueError("The declared factor contradicts the independently verified UCUM factor")
    if mathematical is None and basis != "semantic_reinterpretation":
        raise ValueError("A local/malformed/ambiguous source unit needs explicit semantic_reinterpretation review")
    operations = [{"path": "valueQuantity", "factor": serial(factor), "target_unit": target["unit"],
                   "basis": "verified_ucum_factor" if mathematical == factor and basis != "semantic_reinterpretation" else "clinician_declared_interpretation"}]
    reference_issues = []
    for index, interval in enumerate(payload.get("reference_ranges", [])):
        converted = {}
        for side in ("low", "high"):
            bound = interval.get(side)
            if not isinstance(bound, dict):
                continue
            unit = bound.get("code") or bound.get("unit") or source_unit
            system = bound.get("system") or quantity.get("system")
            scale = factor if unit == source_unit and system == quantity.get("system") else None
            if scale is None and system == "http://unitsofmeasure.org":
                try:
                    if checker.ucum.is_valid(unit):
                        scale = checker.ucum.factor(unit, target["unit"])
                except Exception:
                    pass
            value = number(bound.get("value"))
            path = f"referenceRange[{index}].{side}"
            if scale is None or value is None:
                reference_issues.append(path)
            operations.append({"path": path, "factor": serial(scale), "target_unit": target["unit"],
                               "converted_bound": serial(value * scale) if value is not None and scale is not None else None})
            converted[side] = value * scale if value is not None and scale is not None else None
            if bound.get("comparator"):
                reference_issues.append(path + ".comparator")
        if converted.get("low") is not None and converted.get("high") is not None and converted["low"] > converted["high"]:
            reference_issues.append(f"referenceRange[{index}].unordered_bounds")
    from .profile_config import assess_coding, assess_quantity
    prefix = "Observation" if payload.get("path", "Observation") == "Observation" else "Observation.component"
    coding = assess_coding(profile, prefix + ".code", target["code_system"], code)
    unit_policy = assess_quantity(profile, prefix + ".valueQuantity",
        {"system": target["unit_system"], "code": target["unit"], "unit": target["unit"]})
    if coding["state"] != "compatible" or unit_policy["state"] != "compatible":
        raise ValueError("Approved target violates the selected profile's semantic rules")
    if coding["binding_strength"] in {"required", "extensible"}:
        binding = checker.terminology.check_binding(coding["binding"], target["code_system"], code, term.version,
            coding["binding_version"] or "") if checker.terminology else {"state": "unknown"}
        if binding["state"] != "member":
            raise ValueError("Required/extensible target ValueSet membership needs verified evidence")
    context = payload.get("context", {})
    fit = pipeline._mapping(target["unit"], True, term, context.get("source_test", ""),
        context.get("loinc_system", ""), context.get("method", ""))
    context_conflicts = [name for name, supplied, resolved in (
        ("specimen", context.get("loinc_system"), term.system), ("method", context.get("method"), term.method))
        if supplied and resolved and supplied.casefold() != resolved.casefold()]
    axes = {"component": term.component, "property": term.property, "time": term.time_aspect, "scale": term.scale}
    context_conflicts += [name for name, supplied in context.get("source_axes", {}).items()
                         if supplied and axes.get(name) and supplied.casefold() != axes[name].casefold()]
    return {"code_verified": True, "unit_verified": True, "code_version": term.version,
        "name": chosen_name,
        "code_source": term.source, "unit_source": checker.ucum.source, "unit_fit": fit,
        "factor": serial(factor), "factor_basis": operations[0]["basis"], "operations": operations,
        "reference_units_resolved": not reference_issues, "unresolved_reference_paths": reference_issues,
        "needs_context_review": bool(context_conflicts or fit["class"] in {"needs_refinement", "unresolved"}),
        "context_conflicts": context_conflicts,
        "semantic_correctness_authority": "clinician", "formal_fhir_validation": "external"}


class ReviewRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.document = json.loads(self.path.read_text()) if self.path.exists() else {"format_version": 1, "decisions": []}
        if self.document.get("format_version") != 1 or not isinstance(self.document.get("decisions"), list):
            raise ValueError("Unsupported review registry")
        self.latest = {}
        for entry in self.document["decisions"]:
            content = {key: value for key, value in entry.items() if key != "decision_id"}
            if entry.get("decision_id") != hashlib.sha256(encoded(content).encode()).hexdigest():
                raise ValueError("Review registry decision integrity failed")
            scope_hash = hashlib.sha256(encoded(entry["scope"]).encode()).hexdigest()
            if scope_hash != entry["scope_sha256"] or entry["decision"] not in DECISIONS:
                raise ValueError("Invalid review scope or decision")
            self.latest[scope_hash] = entry

    def proposal(self, payload, namespace, profile, checker):
        _, key = mapping_scope(payload, namespace, profile, getattr(checker, "review_catalogue_fingerprint", ()))
        entry = self.latest.get(key)
        if not entry:
            return {"state": "no_matching_review", "review_required": True}
        if entry["decision"] != "approved":
            return {"state": entry["decision"], "decision_id": entry["decision_id"], "review_required": True}
        try:
            verified = verify_target(entry, checker, profile)
        except (OSError, ValueError, RuntimeError) as exc:
            return {"state": "approval_not_currently_verified", "decision_id": entry["decision_id"],
                    "error_type": type(exc).__name__, "review_required": True}
        return {"state": "reused_clinician_approval", "decision_id": entry["decision_id"],
                "reviewer": entry["reviewer"], "reviewed_at": entry["reviewed_at"], "evidence": entry["evidence"],
                "target": entry["target"], "verification": verified,
                "review_required": not verified["reference_units_resolved"] or verified["needs_context_review"], "source_unchanged": True}


def import_reviews(report, reviewed, registry, config_path):
    from .batch import BatchConfiguration
    from .export_review import configured_checker
    config = BatchConfiguration(config_path, research=False)
    if not config.input.get("source_namespace"):
        raise ValueError("Set input.source_namespace for the source/site before collecting reusable reviews")
    profile = selected_profile(config)
    original, details = report_details(report)
    original_summary = json.loads(Path(report).with_suffix(".summary.json").read_text())
    verify_report_profile(config, original_summary)
    original_catalogues = [row for row in original_summary.get("reference_artifacts", [])
                          if row["role"].startswith(("lookup:", "value_set:")) or row["role"] == "reference_pack"]
    originals = {row["tuple_id"]: row for row in original}
    rows = read_csv(reviewed)
    if len({row.get("tuple_id") for row in rows}) != len(rows):
        raise ValueError("Duplicate clinician review tuple IDs")
    store = ReviewRegistry(registry)
    additions = []
    with config.deployment.prepare(research=False, source_access=False) as prepared:
        checker = configured_checker(config, prepared, profile=profile, research=False)
        try:
            if checker.review_catalogue_fingerprint != original_catalogues:
                raise ValueError("Source terminology artifacts differ from those used for the reviewed report")
            for row in rows:
                if not row.get("review_decision"):
                    continue
                decision = row["review_decision"].strip()
                source = originals.get(row.get("tuple_id"))
                if not source or decision not in DECISIONS:
                    raise ValueError("Unknown tuple or review_decision")
                mutable = set(REVIEW_FIELDS) | {"review_status", "review_complete", "review_correct", "review_notes"}
                if any(key not in mutable and key in source and value != source[key] for key, value in row.items()):
                    raise ValueError("Clinician review changed protected source/check columns")
                if not row.get("reviewer", "").strip() or not row.get("review_evidence", "").strip():
                    raise ValueError("Every decision needs reviewer and review_evidence")
                timestamp = datetime.fromisoformat(row.get("reviewed_at", "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    raise ValueError("reviewed_at needs an ISO timestamp with timezone")
                detail = details[row["tuple_id"]]
                if source["source_namespace"] != config.input["source_namespace"]:
                    raise ValueError("Review source namespace differs from the configured source/site")
                expected_profile = source.get("ig_canonical") or "http://hl7.org/fhir/StructureDefinition/Observation"
                if expected_profile != (profile.canonical or "http://hl7.org/fhir/StructureDefinition/Observation") or source.get("ig_version") not in {"", profile.guide_version}:
                    raise ValueError("Review configuration profile differs from the original report")
                if detail["report"]["profile"]["guide_version"] != (profile.guide_version or "4.0.1"):
                    raise ValueError("Review profile edition differs from the checked edition")
                scope, scope_hash = mapping_scope(detail["metadata"], source["source_namespace"], profile, original_catalogues)
                latest = store.latest.get(scope_hash, {})
                review_hash = hashlib.sha256(Path(reviewed).read_bytes()).hexdigest()
                if latest.get("review_file_sha256") == review_hash and latest.get("decision") == decision:
                    continue
                if latest and timestamp <= datetime.fromisoformat(latest["reviewed_at"]):
                    raise ValueError("A superseding review must be later than the latest decision for this scope")
                entry = {"decision": decision, "scope": scope, "scope_sha256": scope_hash,
                    "reviewer": row["reviewer"].strip(), "reviewed_at": timestamp.isoformat(),
                    "evidence": row["review_evidence"].strip(), "review_basis": row.get("review_basis", ""),
                    "original_report_sha256": hashlib.sha256(Path(report).read_bytes()).hexdigest(),
                    "review_file_sha256": review_hash,
                    "supersedes": store.latest.get(scope_hash, {}).get("decision_id")}
                if decision == "approved":
                    entry["target"] = {"code": row.get("approved_code") or source["code"],
                        "code_system": row.get("approved_code_system") or source["code_system"],
                        "code_version": row.get("approved_code_version") or (source["code_version"]
                            if (row.get("approved_code") or source["code"]) == source["code"]
                            and (row.get("approved_code_system") or source["code_system"]) == source["code_system"] else ""),
                        "unit": row.get("approved_unit") or source["unit_code"],
                        "unit_system": row.get("approved_unit_system") or source["unit_system"],
                        "name": row.get("approved_name", ""),
                        "factor": row.get("approved_factor", "")}
                    verified = verify_target(entry, checker, profile)
                    entry["target"]["code_version"] = verified["code_version"]
                    entry["target"]["name"] = verified["name"]
                    entry["verification_at_import"] = verified
                entry["decision_id"] = hashlib.sha256(encoded(entry).encode()).hexdigest()
                additions.append(entry)
        finally:
            if checker.terminology:
                checker.terminology.close()
    path = Path(registry).resolve()
    if path in {Path(report).resolve(), Path(reviewed).resolve(), config.path}:
        raise ValueError("Registry output must not replace source/configuration")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temporary = None
    try:
        os.close(descriptor)
        current = ReviewRegistry(path)
        if current.document != store.document:
            raise ValueError("Registry changed during review import; retry")
        known = {entry["decision_id"] for entry in current.document["decisions"]}
        current.document["decisions"] += [entry for entry in additions if entry["decision_id"] not in known]
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
            json.dump(current.document, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    return {"registry": str(path), "decisions_imported": len(additions),
            "model_training": False, "source_rewrite": False, "recheck_targets_each_run": True}
