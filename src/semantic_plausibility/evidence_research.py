"""A bounded evidence dossier from an audited check report, without another DB scan."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from .audit import encoded
from .batch import BatchConfiguration
from .export_report import AuditLog, versions, write_html, ratio
from .export_review import configured_checker, local_model_only
from .profile_lookups import terminology_plan
from .review_feedback import ReviewRegistry, report_details, verify_report_profile, approved_mapping_ready
from . import research_prompts as prompts
from .model_schemas import response_schema
from .observation_services import PublicEvidenceClient
from .report_sources import review_set_fields
from .research_summary import counts, write_studies
from .report_context import run_context, item_context, execution_position, execution_since
from .study_sources import provider_query
from .tabular_report import ColumnWriter, project


def require_grouped_report(summary):
    """Use the audited A1 grouping contract, even if the operator later edits TOML."""
    grouping = summary.get("run_context", {}).get("grouping", {})
    if summary.get("mode") != "distinct" or "value" in grouping.get("group_by", []):
        raise ValueError(
            'Prepare the evidence input with output.mode="distinct" and omit "value" '
            'from aggregation.group_by; run check again into a fresh report, then research-evidence. '
            'This combines individual measurements before researching each semantic set.'
        )


def research_plan(config_path, report=None, *, scope=None):
    config = BatchConfiguration(config_path, research=False, research_scope=scope)
    if config.kind != "Observation":
        raise ValueError("Medication AI recommendations and evidence research are not implemented; run the deterministic medication check")
    scope = config.data.get("research", {}).get("scope", "studies")
    report = config.path_value(report or config.output.get("path", "output/laboratory-review.csv"))
    rows, details = report_details(report)
    summary = json.loads(report.with_suffix(".summary.json").read_text())
    require_grouped_report(summary)
    verify_report_profile(config, summary)
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
        queries = PublicEvidenceClient.study_queries(term) if code and scope == "studies" else {}
        targets[key] = {"code": code, "version": key[1], "analyte_name": label,
            "name_lookup": row.get("analyte_name", ""), "unit": row["unit_code"],
            "study_queries": (queries if code else "After exact verification of retrieved candidates") if scope == "studies" else {},
            "provider_queries": {provider: {category: provider_query(query, provider) for category, query in queries.items()}
                for provider in config.data.get("research", {}).get("sources", ["pubmed"])} if queries else {}}
    return {"goal": "Evidence of reported practice supports clinician semantic adjudication",
        "scope": scope,
        "checked_groups": len(rows),
        "input_basis": "Aggregated semantic sets with local count, distribution and reference context",
        "query_plan_note": "Preliminary labels from the audited report; execution refreshes verified terminology aliases. Actual queries and source-specific filters are recorded in the trace.",
        "terminology_plan": terminology_plan(config), "targets": list(targets.values()),
        "study_sources": config.data.get("research", {}).get("sources", ["pubmed"]) if scope == "studies" else [],
        "max_articles_per_loinc": config.data.get("research", {}).get("max_articles_per_loinc", 3) if scope == "studies" else 0,
        "europe_pmc_query_filter": config.data.get("research", {}).get("europe_pmc", {}).get("QUERY_FILTER", "") if scope == "studies" else "",
        "system_prompt": prompts.SYSTEM,
        "prompts": {name: {"instruction": getattr(prompts, name), "response_schema": response_schema(getattr(prompts, name))}
            for name in ("NAME_QUERIES", "LOINC_REVIEW", "UNIT_SPELLING", "UNIT_CANDIDATES", "LOINC_UNITS", "LOINC_RANGES", "STUDY_BOUNDS", "STUDY_MEASUREMENTS", "STUDY_EXTRACT", "TERMINOLOGY_CLAIMS")
            if scope == "studies" or not name.startswith("STUDY_")},
        "source_values_and_reference_bounds": "Compared locally; not included in public search/model prompts",
        "execution": "plan_only_no_network_or_model_call"}


def research_evidence(config_path, report=None, output=None, *, scope=None, progress=None):
    config = BatchConfiguration(config_path, research=True, research_scope=scope)
    scope = config.data.get("research", {}).get("scope", "studies")
    if config.network_mode != "lookups":
        raise ValueError("Evidence retrieval needs explicitly enabled connected lookups")
    local_model_only(config)
    source = config.path_value(report or config.output.get("path", "output/laboratory-review.csv"))
    original, details = report_details(source, hydrate=True)
    source_summary = json.loads(source.with_suffix(".summary.json").read_text())
    require_grouped_report(source_summary)
    if progress:
        progress(f"Researching {len(original)} checked groups; scope={scope}; source values stay local.")
    suffix = ".recommendations.csv" if scope == "lookup" else ".research.csv"
    target = config.path_value(output) if output else source.with_name(source.stem + suffix)
    paths = {name: target.with_suffix(suffix) for name, suffix in {
        "csv": ".csv", "summary": ".summary.json", "evidence": ".evidence.jsonl",
        "trace": ".trace.jsonl", "studies": ".studies.csv", "html": ".html", "audit": ".audit.jsonl"}.items()}
    if progress:
        progress("Outputs: " + str(paths["html"]) + " ; study table: " + str(paths["studies"]) + " ; prompts/results: " + str(paths["trace"]))
    if any(path.exists() for path in paths.values()):
        raise ValueError("Research output already exists; choose a fresh --out prefix")
    if config.path in paths.values() or source in paths.values():
        raise ValueError("Research output must not replace configuration or the original check")
    protected = [config.input.get("path"), config.input.get("snapshot"),
                 config.data.get("quality", {}).get("review_registry")]
    protected += [row.get("path") for row in config.data.get("lookups", {}).values()]
    if any(config.path_value(name) in paths.values() for name in protected if name):
        raise ValueError("Research output must not replace source data, lookups or clinician decisions")
    profile = verify_report_profile(config, source_summary)
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
    started = time.monotonic()
    log = None
    try:
        registry = ReviewRegistry(config.path_value(registry_path)) if registry_path else None
        with tempfile.TemporaryDirectory(prefix=".evidence-", dir=target.parent) as name:
            folder = Path(name)
            log = AuditLog(folder / paths["audit"].name, run_id)
            try:
                log.event("started", operation="research-evidence", source_report_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    configuration_sha256=config.deployment.sha256, research_scope=scope, output_path=str(target), software=versions())
                reviewed = []
                with config.deployment.prepare(research=True, source_access=False) as prepared:
                    assessment_run = run_context(config, prepared, output_path=target, source_report=source, source_summary=source_summary)
                    checker = configured_checker(config, prepared, profile=profile, research=True)
                    try:
                        with (folder / paths["evidence"].name).open("w") as evidence_file:
                            for original_row in original:
                                if progress:
                                    progress(f"Group {len(reviewed)+1}/{len(original)} · code {original_row.get('code', 'unmapped')} · source statistics remain local")
                                execution_start = execution_position(checker)
                                detail = details[original_row["tuple_id"]]
                                payload = detail["metadata"]
                                raw, assessment = checker.assess(payload, detail["observed_values"])
                                item = assessment["report"]["measurements"][0]
                                for candidate in item.get("study_candidates", []):
                                    candidate.update(candidate_loinc=item.get("loinc_code"), candidate_version=item.get("loinc_version"))
                                row = dict(original_row)
                                proposals = item.get("laboratory_suggestions") or {}
                                approval = registry.proposal(payload, row.get("source_namespace", ""), profile, checker) if registry else {"state": "not_configured"}
                                row.update(mapping_review_state=approval["state"],
                                    approved_mapping_ready=approved_mapping_ready(approval, payload, detail["observed_values"]))
                                # Approved targets can add research without altering the source set.
                                if approval["state"] == "reused_clinician_approval":
                                    target_code = approval["target"]
                                    term = checker.loinc.lookup(target_code["code"], target_code["code_version"])
                                    item["study_candidates"] += [vars(candidate) | {"candidate_loinc": term.code, "candidate_version": term.version}
                                                                for candidate in checker.evidence.search_studies(term)]
                                # Research a selected, exactly verified alternative; do not expand every unresolved hypothesis.
                                for candidate in proposals.get("coding_candidates", []):
                                    if candidate["code"] == proposals.get("selected_code") and candidate["code"] != item.get("loinc_code") and candidate["selectable"]:
                                        term = checker.loinc.lookup(candidate["code"], candidate["version"])
                                        found = [vars(c) | {"candidate_loinc": term.code, "candidate_version": term.version}
                                                 for c in checker.evidence.search_studies(term)]
                                        item["study_candidates"] += found
                                row.update(suggested_code=proposals.get("selected_code") or row.get("suggested_code", ""),
                                    suggested_name=proposals.get("selected_name") or "", suggestion_state=proposals.get("state", "unresolved"),
                                    laboratory_suggestions_json=encoded(proposals),
                                    reference_candidates_json=encoded([candidate for candidate in item["study_candidates"]
                                        if candidate["evidence_type"] in {"reference_interval", "analytical_interval", "documented_extreme"}]),
                                    study_evidence_json=encoded(item["study_candidates"]),
                                    reported_measurements_json=encoded([candidate for candidate in item["study_candidates"]
                                        if candidate["evidence_type"] == "reported_measurement"]),
                                    clinician_mapping_json=encoded(approval),
                                    suggested_unit=raw.get("vorschlag_einheit", ""),
                                    suggested_factor=raw.get("vorschlag_faktor", ""),
                                    suggestion_basis=raw.get("vorschlag_grundlage", ""),
                                    transformation_json=encoded(assessment["recommendation"]["transformation"]))
                                selected = next((candidate for candidate in proposals.get("coding_candidates", [])
                                    if candidate["code"] == proposals.get("selected_code") and candidate["selectable"]), None)
                                if selected:
                                    row.update(suggested_code_system=selected["system"], suggested_code_version=selected["version"])
                                    fit = selected["unit_fit"]
                                    if selected["code"] != item.get("loinc_code"):
                                        from .recommendations import transformation
                                        from .fhir_quality_pipeline import FhirQualityPipeline
                                        mapping = {**fit, "class": fit["class"] if fit.get("source_unit_mapping_verified") else "unresolved",
                                            "factor": fit.get("factor_from_original")}
                                        pipeline = FhirQualityPipeline(checker.ucum, checker.loinc)
                                        plan = transformation(mapping, payload["quantity"], payload["reference_ranges"], fit.get("target"), pipeline)
                                        row.update(suggested_unit=fit.get("target") or "", suggested_factor=plan.get("factor") or "",
                                            suggestion_basis="verified_target_code_unit_candidate", transformation_json=encoded(plan))
                                if approval["state"] == "reused_clinician_approval":
                                    target_code = approval["target"]
                                    row.update(suggested_code=target_code["code"], suggested_code_system=target_code["code_system"],
                                        suggested_code_version=target_code["code_version"], suggested_name=target_code["name"],
                                        suggested_unit=target_code["unit"], suggested_factor=target_code["factor"],
                                        transformation_json=encoded(approval["verification"]))
                                from .export_review import mapping_proposal
                                coding = [{"target_code": candidate["code"], "target_system": candidate["system"],
                                    "target_version": candidate["version"], "target_verified": True, "source": candidate["source"],
                                    "unit_fit": candidate["unit_fit"], "review_required": True} for candidate in proposals.get("coding_candidates", [])]
                                row["coding_proposal_json"] = encoded(coding or json.loads(original_row.get("coding_proposal_json") or "[]"))
                                row["fhir_mapping_proposal_json"] = encoded(mapping_proposal(row,
                                    {"recommendation": {"transformation": json.loads(row["transformation_json"])}}, json.loads(row["coding_proposal_json"])))
                                assessment.update(metadata=payload, observed_values=detail["observed_values"])
                                row["evidence_json"] = encoded(assessment)
                                row.update(review_set_fields(row, assessment))
                                assessment["assessment_context"] = item_context(assessment_run, row, assessment)
                                assessment["assessment_context"]["execution"] = execution_since(checker, execution_start)
                                row["assessment_context_json"] = encoded(assessment["assessment_context"])
                                reviewed.append(row)
                                if progress:
                                    progress(f"Completed {len(reviewed)}/{len(original)} groups; study retrieval/extraction: {counts(item['study_candidates'])}")
                                evidence_file.write(encoded({"tuple_id": row["tuple_id"], "metadata": payload,
                                    "observed_values": detail["observed_values"], **assessment,
                                    "report_row": {key: val for key, val in row.items() if key != "evidence_json"}}) + "\n")
                        evidence = checker.evidence
                        llm = getattr(evidence, "llm", None)
                        trace = [{"kind": "query", **row} for row in getattr(evidence, "query_log", [])]
                        trace += [{"kind": "model", **row} for row in getattr(llm, "trace", [])]
                        (folder / paths["trace"].name).write_text("".join(encoded(row) + "\n" for row in trace))
                        summary = json.loads(source.with_suffix(".summary.json").read_text())
                        # Reused/withdrawn approvals are evaluated in this run, not inherited from A1.
                        summary["metrics"]["approved_mapping_ready"] = ratio(
                            sum(row["approved_mapping_ready"] for row in reviewed), len(reviewed))
                        summary.update(run_id=run_id, operation="research-evidence", software=versions(),
                            run_context=assessment_run,
                            output_path=str(target),
                            source_report_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            configuration_sha256=config.deployment.sha256, evidence_kind="retrieved_evidence_candidates",
                            terminology_plan=terminology_plan(config, profile),
                            research={"scope": scope, "sources": list(getattr(evidence, "source_records", {}).values()),
                                "study_providers": list(evidence.research_sources) if scope == "studies" else [],
                                **query_counts(getattr(evidence, "query_log", [])), "accepted_model_calls": getattr(llm, "audit_records", []),
                                "partially_accepted_model_calls": sum(bool(t.get("extraction_rejections")) and t.get("state") == "accepted_by_source_guards" for t in getattr(llm, "trace", [])),
                                "max_articles_per_loinc": evidence.max_articles_per_loinc,
                                "model_state": "responses_accepted" if getattr(llm, "audit_records", []) else "no_accepted_response",
                                "groups_with_code_proposals": sum(bool(row.get("suggested_code")) for row in reviewed),
                                "groups_with_name_proposals": sum(bool(row.get("suggested_name")) for row in reviewed),
                                "groups_with_retrieved_articles": sum(bool(json.loads(row["study_evidence_json"])) for row in reviewed),
                                "groups_with_study_evidence": sum(any(c.get("evidence_type") != "unclassified_discovery" and c.get("status") == "candidate" for c in json.loads(row["study_evidence_json"])) for row in reviewed),
                                "evidence_counts": write_studies(folder / paths["studies"].name, reviewed),
                                "model_elapsed_seconds": round(sum(c.get("elapsed_seconds", 0) for c in getattr(llm, "trace", [])), 2),
                                "semantic_correctness": "clinician_adjudication_required", "hospital_database_requeried": False})
                    finally:
                        if checker.terminology:
                            checker.terminology.close()
                with (folder / paths["csv"].name).open("w", encoding="utf-8-sig", newline="") as handle, ColumnWriter(handle) as writer:
                    for row in reviewed:
                        writer.writerow(project(row, {}, operation="research-evidence"))
                summary["source_check_elapsed_seconds"] = source_summary.get("elapsed_seconds")
                summary["elapsed_seconds"] = round(time.monotonic() - started, 4)
                write_html(folder / paths["html"].name, reviewed[:2000], summary, paths["csv"].name, paths["audit"].name)
                summary["output_hashes"] = {paths[key].name: hashlib.sha256((folder / paths[key].name).read_bytes()).hexdigest()
                                           for key in ("csv", "evidence", "trace", "studies", "html")}
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
                "audit": str(paths["audit"]), "groups": len(original), "scope": scope, "source_changed": False,
                "model_state": summary["research"]["model_state"],
                "groups_with_study_evidence": summary["research"]["groups_with_study_evidence"],
                "studies": str(paths["studies"]), "evidence_counts": summary["research"]["evidence_counts"]}
    finally:
        if log:
            log.close()
        lock.unlink(missing_ok=True)


def query_counts(events):
    """Keep retrieval requests separate from model extraction failures."""
    retrievals = [event for event in events if "state" not in event and "task" not in event]
    return {"queries": len(retrievals), "retrieval_requests": len(retrievals),
        "literature_searches": sum(event.get("source") == "europe_pmc" or event.get("endpoint") == "esearch.fcgi" for event in retrievals),
        "articles_screened": sum(event.get("task") == "study_screen" for event in events),
        "articles_selected": sum(event.get("task") == "study_screen" and event.get("state") == "selected" for event in events),
        "articles_excluded": sum(event.get("task") == "study_screen" and event.get("state") == "excluded" for event in events),
        "extraction_failures": sum("task" in event and event.get("state") == "unavailable" for event in events),
        "retrieval_failures": sum("task" not in event and event.get("state") == "unavailable" for event in events)}
