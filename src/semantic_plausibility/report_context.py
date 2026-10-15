"""Small, secret-free assessment records shared by CSV and HTML reports."""

from datetime import datetime, timezone
from html import escape
import json
from urllib.parse import urlsplit, urlunsplit

from .report_sources import document, lookup_attempts, source_html


def safe_location(value):
    """Never copy URL credentials, query parameters or fragments into the record."""
    text = str(value or "")
    if "://" not in text:
        return text
    try:
        url = urlsplit(text)
        if url.scheme not in {"http", "https"} or not url.hostname:
            return "Endpoint withheld"
        return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    except ValueError:
        return "Endpoint withheld"


def select(data, keys):
    return {key: data[key] for key in keys if key in data}


def run_context(config, prepared, *, output_path=None, source_report=None, source_summary=None):
    """Allowlist settings; never serialize the complete configuration or environment."""
    research = config.kind == "Observation" and config.data.get("research", {}).get("enabled", False)
    active_research = research and config.network_mode == "lookups"
    scope = prepared.scope if active_research else "disabled"
    lookups = []
    for alias, entry in config.data.get("lookups", {}).items():
        auth = [name for name, keys in {
            "Basic Auth": ("username_env", "username_file", "password_env", "password_file"),
            "Bearer token": ("token_env", "token_file"),
            "Client certificate": ("client_cert", "client_key"),
            "OAuth": ("client_id_env", "client_id_file", "client_secret_env", "client_secret_file"),
        }.items() if any(entry.get(key) for key in keys)]
        systems = [{"loinc": "http://loinc.org", "ucum": "http://unitsofmeasure.org"}[entry['type']]] if entry.get('type') in {'loinc', 'ucum'} else entry.get('systems', [])
        lookups.append({"alias": alias, **select(entry, ("enabled", "type", "role", "version", "complete")), "systems": systems,
                        "location": safe_location(entry.get("path") or entry.get("endpoint")) or "Configured endpoint reference",
                        "authentication": auth or ["None configured"]})
    model = None
    if active_research and prepared.models_file:
        models = json.loads(prepared.models_file.read_text())["models"]
        selected = next(row for row in models if row["alias"] == prepared.model_alias)
        model = select(selected, ("alias", "model", "temperature", "max_tokens", "timeout_seconds", "response_mode", "reasoning_effort", "enable_thinking"))
        model["endpoint"] = safe_location(selected.get("base_url"))
    original = source_summary or {}
    source = select(config.input, ("type", "origin", "path", "granularity", "columns", "defaults", "elements", "context", "context_file", "manifest", "snapshot"))
    source["database_selection"] = select(config.database, ("DB_SCHEMA", "DB_TABLE"))
    if source_report:
        source = {"type": "audited_check_report", "path": str(source_report), "hospital_data_requeried": False,
                  "original_input": original.get("run_context", {}).get("input", "Not recorded by the earlier report version"),
                  "original_input_sha256": original.get("input_sha256"),
                  "original_configuration_sha256": original.get("configuration_sha256")}
    settings = config.data.get("research", {})
    return {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration_file": str(config.path), "configuration_sha256": config.deployment.sha256,
            "operation": config.operation, "level": "A3" if scope == "studies" else "A2" if scope == "lookup" else "A1",
            "resource_type": config.kind, "network_mode": config.network_mode,
            "input": source, "output": {"mode": config.output.get("mode", "distinct"), "path": str(output_path or config.output.get("path", ""))},
            "grouping": {"engine": "duckdb", "group_by": sorted(config.group_by) if config.kind == "Observation" else "Complete medication, dosage, status and context set"},
            "target": select(config.data.get("specification", {}), ("FHIR_VERSION", "PROFILE", "PROFILE_VERSION", "IG_PACKAGE", "IG_VERSION", "IG_CANONICAL", "STRUCTURE_DEFINITION", "PROFILE_FILE", "VALUE_SETS")),
            "lookup_strategy": config.lookup_strategy, "configured_lookups": lookups,
            "validation": select(config.validation, ("enabled", "backend", "fail_on_error")),
            "research": {"scope": scope, "model": model,
                         "study_providers": settings.get("sources", ["pubmed"]) if scope == "studies" else [],
                         "max_articles_per_loinc": settings.get("max_articles_per_loinc", 3) if scope == "studies" else 0},
            "configuration_scope": "Selected effective settings only. Credentials, connection strings and secret-file contents are excluded. Enabled services are not proof of use; see each item's lookup evidence."}


def execution_position(checker):
    evidence = getattr(checker, "evidence", None)
    return (len(getattr(getattr(evidence, "llm", None), "trace", [])), len(getattr(evidence, "query_log", [])))


def execution_since(checker, position):
    from collections import Counter
    evidence = getattr(checker, "evidence", None)
    calls = getattr(getattr(evidence, "llm", None), "trace", [])[position[0]:]
    return {"new_model_calls": len(calls), "model_call_states": dict(Counter(c.get("state", "unknown") for c in calls)),
            "new_research_retrieval_events": len(getattr(evidence, "query_log", [])) - position[1],
            "scope": "New events while processing this item. Earlier cached results may be reused; zero new calls does not mean no evidence. Ordinary terminology calls are listed separately."}


def compact_sources(checks):
    rows = []
    for check in checks:
        metadata = check.get("metadata") or {}
        artifact = metadata.get("artifact") or check.get("artifact") or {}
        rows.append({**select(check, ("path", "alias", "system", "code", "state", "reason", "error_type", "checked_at")),
                     "source_sha256": check.get("source_sha256") or artifact.get("source_sha256", ""),
                     "version": metadata.get("version") or check.get("version") or artifact.get("artifact_version", ""),
                     "source": safe_location(check.get("source")),
                     "display": metadata.get("display", "")})
    return rows


def label_sources(measurement):
    """Show translation provenance without treating supplements as code validation."""
    rows, seen = [], set()
    checks = measurement.get("name_checks", []) + [measurement.get("unit_label_check") or {}]
    for check in checks:
        for label in check.get("aliases", []):
            state = "label_matched" if label in check.get("matched_aliases", []) else "label_available"
            row = {"system": check.get("system", ""), "code": check.get("code", ""), "state": state,
                   "scope": "Label evidence only; does not validate code membership or analyte mapping.",
                   "source": safe_location(label.get("source")), "source_sha256": label.get("source_sha256", ""),
                   "version": label.get("supplement_version") or label.get("artifact_version") or label.get("version", "")}
            identity = json.dumps(row, sort_keys=True)
            if identity not in seen:
                rows.append(row)
                seen.add(identity)
    return rows


def item_context(context, row, detail):
    """Explain observed results without inventing missing context or new verdicts."""
    medication = row.get("resource_type", "Observation") != "Observation"
    methods, assumptions = [], []
    if medication:
        sources = compact_sources(detail.get("terminology", []))
        for label, key in (("Product code membership", "code_check"), ("Quantity unit membership", "unit_check"), ("Dose/strength/timing consistency", "dose_check")):
            methods.append({"method": label, "result": row.get(key, "not_recorded")})
        assumptions += ["Product strength and formulation are supplied by the source; individual code membership does not verify them.",
                        "Dose calculations are conditional arithmetic, not clinically recommended doses. DDD is classification context."]
        if row.get("resource_type") == "MedicationAdministration":
            assumptions.append("Constant uninterrupted delivery is " + ("explicitly asserted by the source." if detail.get("source_set", {}).get("constant_rate") else "not asserted; rate × time is conditional."))
    else:
        measurement = next(iter(detail.get("report", {}).get("measurements", [])), {})
        sources = compact_sources(lookup_attempts(measurement, unit={"system": row.get("unit_system", ""), "code": row.get("unit_code", "")}))
        sources += label_sources(measurement)
        for label, key in (("Exact code membership", "code_check"), ("Unit syntax/membership", "unit_check"),
                           ("Code–unit fit", "code_unit_fit"), ("Name/alias match", "name_check"),
                           ("Numeric values", "value_numeric_ready"), ("Reference interval structure", "reference_check"),
                           ("Values versus source reference intervals", "value_reference_check")):
            methods.append({"method": label, "result": row.get(key, "not_recorded")})
        assumptions += ["Declared source code systems, column meanings and reference intervals are source/operator assertions.",
                        "A value outside a source reference interval is not proof of a semantic error or a clinically wrong result.",
                        "A LOINC example unit is a candidate; conversion requires a verified factor and compatible context."]
        for field in ("specimen", "method"):
            if not row.get(field):
                assumptions.append(field.capitalize() + " is not supplied in the selected source field; no agreement is inferred from the code alone.")
        suggestions = measurement.get("laboratory_suggestions") or {}
        methods.append({"method": "Terminology candidate review", "result": suggestions.get("state", "not_requested"),
                        "model_used": suggestions.get("model_used", False)})
        candidates = measurement.get("study_candidates", [])
        methods.append({"method": "Study evidence", "result": "not_requested" if context["research"]["scope"] != "studies" else "retrieved_candidates" if candidates else "no_supported_candidate",
                        "candidate_count": len(candidates)})
    if not row.get("frequency_known") or str(row.get("frequency_known")).lower() == "false":
        assumptions.append("Source occurrence count is unknown; no population frequency is inferred.")
    if context["output"].get("mode") == "distinct":
        assumptions.append("This row groups the selected semantic set; individual resource IDs are not retained.")
    if context["input"].get("defaults") or context["input"].get("context"):
        assumptions.append("Configured defaults/context are listed in the run configuration. Field-level default provenance is not retained by this adapter.")
    research = context["operation"] == "research-evidence"
    if research:
        assumptions.append("Check status and numeric readiness are inherited from the original A1 report. Research adds candidates; it does not certify or replace that result.")
    status = row.get("status", "unknown")
    action = ("Review failed checks against the original export; correct approved source/ETL issues and rerun." if status == "error"
              else "No correction identified within the implemented checks; semantic correctness still requires expert review." if status == "passed"
              else "Review sourced candidates and missing evidence; apply only approved changes in the source mapping/ETL, then rerun.")
    return {"configuration_sha256": context["configuration_sha256"], "configuration_section": "run-configuration",
            "tuple_id": row.get("tuple_id"), "level": context["level"], "resource_type": row.get("resource_type", "Observation"),
            "check_result_basis": "original_A1_report" if research else "this_run",
            "input": {"rows": row.get("source_rows", row.get("input_rows")), "occurrences": row.get("occurrence_count"),
                      "frequency_known": row.get("frequency_known"),
                      **(select(row, ("source_id", "source_ref", "measurement_path")) if context["output"].get("mode") == "items" else {})},
            "methods": methods, "assumptions_and_limits": assumptions, "lookup_evidence": sources,
            "fhir_validation": {"run_state": row.get("fhir_validation_state", "not_recorded"),
                                "scope": "Run-level resource validation outcome; not an independently attributed verdict for this grouped item."},
            "output": {"status": status, "next_step": action, "automatic_changes": False}}


def _json(value):
    return '<pre>' + escape(json.dumps(value, ensure_ascii=False, indent=2, default=str)) + '</pre>'


def run_context_html(summary, csv_name):
    context = summary.get("run_context")
    if not context:
        return '<section id="run-configuration"><h2>Configuration and methods</h2><p>Not captured by this report version. Rerun to obtain the assessment record; do not infer settings from the current TOML.</p></section>'
    from pathlib import Path
    links = ' · '.join('<a href="' + escape(Path(csv_name).with_suffix(suffix).name, quote=True) + '">' + label + '</a>'
                       for suffix, label in ((".summary.json", "Run summary"), (".evidence.jsonl", "Item evidence"), (".audit.jsonl", "Audit log")))
    parts = [('Input and mapping', context['input']), ('Target and grouping', {"target": context['target'], "applied_profile_rules": summary.get('terminology_plan', {}), "grouping": context['grouping']}),
             ('Configured terminology sources (including disabled sources)', {"strategy": context['lookup_strategy'], "sources": context['configured_lookups']}),
             ('Validation and research settings', {"validation": context['validation'], "research": context['research']}),
             ('Output and reproducibility', {"output": context['output'], "input_sha256": summary.get('input_sha256'), "software": summary.get('software')})]
    return '<section id="run-configuration"><h2>Configuration and methods</h2><p><b>' + escape(context['level'] + ' · ' + context['resource_type']) + '</b> · ' + escape(context['network_mode']) + '</p><p>Configuration: <code>' + escape(context['configuration_file']) + '</code><br>Configuration SHA-256: <code>' + escape(context['configuration_sha256']) + '</code></p><p>' + escape(context['configuration_scope']) + '</p>' + ''.join('<details><summary>' + label + '</summary>' + _json(data) + '</details>' for label, data in parts) + '<p>' + links + '</p></section>'


def item_context_html(row):
    item = document(row.get('assessment_context_json'))
    if not item:
        return '<p>Assessment context was not captured in this report version.</p>'
    methods = ''.join('<tr><td>' + escape(str(r['method'])) + '</td><td>' + escape(str(r['result'])) + '</td></tr>' for r in item['methods'])
    sources = ''.join('<tr><td>' + escape(str(r.get('code', ''))) + '<br>' + escape(str(r.get('system', ''))) + '</td><td>' + escape(str(r.get('state', 'unknown'))) + ('<br>' + escape(r['scope']) if r.get('scope') else '') + '</td><td>' + source_html(r.get('source'), version=r.get('version', ''), sha256=r.get('source_sha256', '')) + '</td></tr>' for r in item['lookup_evidence'])
    execution = '<h4>Model and research activity for this item</h4>' + _json(item['execution']) if item.get('execution') else ''
    return '<details class="assessment"><summary>How this item was assessed · ' + escape(str(item.get('tuple_id', ''))) + '</summary><p><a href="#run-configuration">Run configuration, mapping and target profile</a> · ' + escape(item['level']) + '<br>Configuration SHA-256: <code>' + escape(item['configuration_sha256']) + '</code><br>Check result basis: ' + escape(item['check_result_basis']) + '</p><h4>Input accounting</h4>' + _json(item['input']) + '<h4>Methods and results</h4><table><tr><th>Method</th><th>Result</th></tr>' + methods + '</table>' + execution + '<p>FHIR validation (run-level): ' + escape(str(item['fhir_validation']['run_state'])) + '. ' + escape(item['fhir_validation']['scope']) + '</p><h4>Assumptions and limits</h4><ul>' + ''.join('<li>' + escape(t) + '</li>' for t in item['assumptions_and_limits']) + '</ul><h4>Lookup evidence and attempts for this item</h4><table><tr><th>Code / system</th><th>Result</th><th>Source / edition</th></tr>' + (sources or '<tr><td colspan="3">No lookup evidence recorded.</td></tr>') + '</table><h4>Output and improvement step</h4><p>' + escape(item['output']['status']) + ': ' + escape(item['output']['next_step']) + '</p><p>See the sourced proposal for candidate fields, explanations and conversion factors. Input data remains unchanged.</p></details>'
