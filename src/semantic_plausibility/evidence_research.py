"""A bounded evidence dossier from an audited check report, without another DB scan."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .audit import encoded
from .batch import BatchConfiguration
from .export_report import AuditLog, versions, write_html
from .export_review import configured_checker, local_model_only
from .profile_lookups import terminology_plan
from .review_feedback import ReviewRegistry, report_details, verify_report_profile
from . import research_prompts as prompts
from .model_schemas import response_schema
from .observation_services import PublicEvidenceClient


def research_plan(config_path, report=None):
    config = BatchConfiguration(config_path, research=False)
    report = config.path_value(report or config.output.get("path", "output/laboratory-review.csv"))
    rows, details = report_details(report)
    verify_report_profile(config, json.loads(report.with_suffix(".summary.json").read_text()))
    targets = {}
    for row in rows:
        detail = details[row["tuple_id"]]
        item = detail["report"]["measurements"][0]
        code = item.get("loinc_code")
        key = (code or row.get("analyte_name", ""), item.get("loinc_version") or "")
        if key in targets:
            continue
        label = row.get("canonical_name") or row.get("analyte_name", "")
        from .observation_quality import LoincTerm
        term = LoincTerm(code or "", label, **{k: item.get("loinc_axes", {}).get(k, "")
            for k in ("component", "system")}) if item.get("loinc_axes") else LoincTerm(code or "", label)
        targets[key] = {"code": code, "version": key[1], "analyte_name": label,
            "name_lookup": row.get("analyte_name", ""), "unit": row["unit_code"],
            "study_queries": PublicEvidenceClient.study_queries(term) if code else "After exact verification of retrieved candidates"}
    return {"goal": "Evidence of reported practice supports clinician semantic adjudication",
        "terminology_plan": terminology_plan(config), "targets": list(targets.values()),
        "study_sources": config.data.get("research", {}).get("sources", ["pubmed"]),
        "max_articles_per_loinc": config.data.get("research", {}).get("max_articles_per_loinc", 3),
        "system_prompt": prompts.SYSTEM,
        "prompts": {name: {"instruction": getattr(prompts, name), "response_schema": response_schema(getattr(prompts, name))}
            for name in ("NAME_QUERIES", "LOINC_REVIEW", "UNIT_SPELLING", "UNIT_CANDIDATES", "LOINC_UNITS", "LOINC_RANGES", "STUDY_BOUNDS", "STUDY_MEASUREMENTS", "TERMINOLOGY_CLAIMS")},
        "source_values_and_reference_bounds": "Compared locally; not included in public search/model prompts",
        "execution": "plan_only_no_network_or_model_call"}


def research_evidence(config_path, report=None, output=None):
    config = BatchConfiguration(config_path, research=True)
    if config.network_mode != "lookups":
        raise ValueError("Evidence retrieval needs explicitly enabled connected lookups")
    local_model_only(config)
    source = config.path_value(report or config.output.get("path", "output/laboratory-review.csv"))
    original, details = report_details(source)
    target = config.path_value(output) if output else source.with_name(source.stem + ".research.csv")
    paths = {name: target.with_suffix(suffix) for name, suffix in {
        "csv": ".csv", "summary": ".summary.json", "evidence": ".evidence.jsonl",
        "trace": ".trace.jsonl", "html": ".html", "audit": ".audit.jsonl"}.items()}
    if any(path.exists() for path in paths.values()):
        raise ValueError("Research output already exists; choose a fresh --out prefix")
    if config.path in paths.values() or source in paths.values():
        raise ValueError("Research output must not replace configuration or the original check")
    protected = [config.input.get("path"), config.input.get("snapshot"),
                 config.data.get("quality", {}).get("review_registry")]
    protected += [row.get("path") for row in config.data.get("lookups", {}).values()]
    if any(config.path_value(name) in paths.values() for name in protected if name):
        raise ValueError("Research output must not replace source data, lookups or clinician decisions")
    profile = verify_report_profile(config, json.loads(source.with_suffix(".summary.json").read_text()))
    for detail in details.values():
        checked = detail["report"]["profile"]
        if checked["canonical"] != (profile.canonical or None) or checked["guide_version"] != (profile.guide_version or "4.0.1"):
            raise ValueError("Research and original check must use the same profile/edition")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(".run.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    registry_path = config.data.get("quality", {}).get("review_registry")
    run_id = str(uuid.uuid4())
    log = None
    try:
        registry = ReviewRegistry(config.path_value(registry_path)) if registry_path else None
        with tempfile.TemporaryDirectory(prefix=".evidence-", dir=target.parent) as name:
            folder = Path(name)
            log = AuditLog(folder / paths["audit"].name, run_id)
            try:
                log.event("started", operation="research-evidence", source_report_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    configuration_sha256=config.deployment.sha256, software=versions())
                reviewed = []
                with config.deployment.prepare(research=True, source_access=False) as prepared:
                    checker = configured_checker(config, prepared, profile=profile, research=True)
                    try:
                        with (folder / paths["evidence"].name).open("w") as evidence_file:
                            for original_row in original:
                                detail = details[original_row["tuple_id"]]
                                payload = detail["metadata"]
                                _, assessment = checker.assess(payload, detail["observed_values"])
                                item = assessment["report"]["measurements"][0]
                                row = dict(original_row)
                                proposals = item.get("laboratory_suggestions") or {}
                                approval = registry.proposal(payload, row.get("source_namespace", ""), profile, checker) if registry else {"state": "not_configured"}
                                # Approved targets can add research without altering the source set.
                                if approval["state"] == "reused_clinician_approval":
                                    target_code = approval["target"]
                                    term = checker.loinc.lookup(target_code["code"], target_code["code_version"])
                                    item["study_candidates"] += [vars(candidate) for candidate in checker.evidence.search_studies(term)]
                                # Also research exactly verified alternative codes for unmapped LIS sets.
                                for candidate in proposals.get("coding_candidates", []):
                                    if candidate["code"] != item.get("loinc_code") and candidate["selectable"]:
                                        term = checker.loinc.lookup(candidate["code"], candidate["version"])
                                        found = [vars(c) | {"candidate_loinc": term.code, "candidate_version": term.version}
                                                 for c in checker.evidence.search_studies(term)]
                                        item["study_candidates"] += found
                                row.update(suggested_code=proposals.get("selected_code") or "",
                                    suggested_name=proposals.get("selected_name") or "", suggestion_state=proposals.get("state", "unresolved"),
                                    laboratory_suggestions_json=encoded(proposals),
                                    reference_candidates_json=encoded([candidate for candidate in item["study_candidates"]
                                        if candidate["evidence_type"] in {"reference_interval", "analytical_interval", "documented_extreme"}]),
                                    study_evidence_json=encoded(item["study_candidates"]),
                                    reported_measurements_json=encoded([candidate for candidate in item["study_candidates"]
                                        if candidate["evidence_type"] == "reported_measurement"]),
                                    clinician_mapping_json=encoded(approval), evidence_json=encoded(assessment))
                                reviewed.append(row)
                                evidence_file.write(encoded({"tuple_id": row["tuple_id"], "metadata": payload,
                                    "observed_values": detail["observed_values"], **assessment}) + "\n")
                        evidence = checker.evidence
                        llm = getattr(evidence, "llm", None)
                        trace = [{"kind": "query", **row} for row in getattr(evidence, "query_log", [])]
                        trace += [{"kind": "model", **row} for row in getattr(llm, "trace", [])]
                        (folder / paths["trace"].name).write_text("".join(encoded(row) + "\n" for row in trace))
                        summary = json.loads(source.with_suffix(".summary.json").read_text())
                        summary.update(run_id=run_id, operation="research-evidence", software=versions(),
                            source_report_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            configuration_sha256=config.deployment.sha256, evidence_kind="retrieved_evidence_candidates",
                            terminology_plan=terminology_plan(config, profile),
                            research={"sources": list(getattr(evidence, "source_records", {}).values()),
                                "queries": len(getattr(evidence, "query_log", [])), "accepted_model_calls": getattr(llm, "audit_records", []),
                                "max_articles_per_loinc": evidence.max_articles_per_loinc,
                                "semantic_correctness": "clinician_adjudication_required", "hospital_database_requeried": False})
                    finally:
                        if checker.terminology:
                            checker.terminology.close()
                fields = list(dict.fromkeys(key for row in reviewed for key in row))
                from .support import _csv_text
                with (folder / paths["csv"].name).open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows({key: _csv_text(value) if isinstance(value, str) else value for key, value in row.items()} for row in reviewed)
                write_html(folder / paths["html"].name, reviewed[:2000], summary, paths["csv"].name, paths["audit"].name)
                summary["output_hashes"] = {paths[key].name: hashlib.sha256((folder / paths[key].name).read_bytes()).hexdigest()
                                           for key in ("csv", "evidence", "trace", "html")}
                (folder / paths["summary"].name).write_text(json.dumps(summary, indent=2) + "\n")
                hashes = {path.name: hashlib.sha256((folder / path.name).read_bytes()).hexdigest()
                          for key, path in paths.items() if key != "audit"}
                log.event("completed", output_hashes=hashes, rows=len(reviewed))
                log.close()
                log = None
                for key in [key for key in paths if key != "summary"] + ["summary"]:
                    source_file = folder / paths[key].name
                    os.chmod(source_file, 0o600)
                    os.replace(source_file, paths[key])
            except Exception as exc:
                if log:
                    log.event("failed", error_type=type(exc).__name__, diagnostic="redacted_external_or_data_exception")
                    log.close()
                    log = None
                    failed = target.with_name(target.stem + ".failed." + run_id + ".audit.jsonl")
                    os.replace(folder / paths["audit"].name, failed)
                raise ValueError("Evidence research failed (" + type(exc).__name__ + "); incomplete artifacts were not published") from None
        return {"csv": str(paths["csv"]), "html": str(paths["html"]), "trace": str(paths["trace"]),
                "audit": str(paths["audit"]), "groups": len(original), "source_changed": False}
    finally:
        if log:
            log.close()
        lock.unlink(missing_ok=True)
