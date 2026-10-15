"""One-shot medication review: bounded DuckDB grouping, sourced reports and audit."""
from contextlib import closing
import csv
from decimal import Decimal
import hashlib
from html import escape
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import duckdb

from .audit import encoded
from .support import _csv_text
from .batch import BatchResult
from .deployment import environment
from .export_report import AuditLog, ratio, versions
from .export_review import InputContractError, _input_hash, reference_artifacts
from .fhir_validation import fhir_json, validate_input
from .medication_checks import MedicationChecker, SCOPE, terminology_plan
from .medication_input import records
from .report_context import run_context, item_context, run_context_html, item_context_html
from .report_comparison import CSS as comparison_css, legend_html, field_table_html, field_details_html
from .medication_report_table import medication_fields

FIELDS = ["tuple_id", "resource_type", "source_rows", "occurrence_count", "frequency_known", "status",
          "medication_status", "intent", "product_name", "product_codings_json", "medication_json",
          "dosage_json", "duration_json", "constant_rate_asserted", "source_set_json",
          "code_check", "unit_check", "dose_check", "canonical_names_json", "findings_json",
          "recommendations_json", "sources_json", "ddd_references_json", "clinical_dose_safety", "fhir_validation_state", "assessment_context_json"]


def quantity_text(value):
    """Display the supplied quantity, without converting or approving it."""
    if not isinstance(value, dict) or not value:
        return "not supplied"
    amount = value.get("value")
    return f"{value.get('comparator', '')}{amount if amount is not None else 'missing value'} {value.get('code') or value.get('unit') or '(unit missing)'}"


def concept_text(value):
    if not isinstance(value, dict):
        return "not supplied"
    return value.get("text") or "; ".join(str(c.get("display") or c.get("code") or "")
        for c in value.get("coding", []) if isinstance(c, dict)) or "not supplied"


def item_summary(row, detail):
    """Format source assertions and existing findings; never calculate a new dose."""
    data = detail["source_set"]
    product = data["medication"]
    lines = [f"Source product: {row.get('product_name') or concept_text(product.get('code'))}. Form: {concept_text(product.get('form'))}."]
    for index, ingredient in enumerate(product.get("ingredient", []), 1):
        if isinstance(ingredient, dict) and isinstance(ingredient.get("strength"), dict):
            strength = ingredient["strength"]
            lines.append(f"Declared strength, ingredient {index}: {quantity_text(strength.get('numerator'))} per {quantity_text(strength.get('denominator'))}. Product strength is not independently verified.")
    dose_label = {"MedicationRequest": "Ordered", "MedicationAdministration": "Recorded administration", "MedicationStatement": "Reported use"}.get(data["resource_type"], "Source")
    for index, dosage in enumerate(data["dosages"], 1):
        entries = dosage.get("doseAndRate", []) if data["resource_type"] in {"MedicationRequest", "MedicationStatement"} else [{"doseQuantity": dosage.get("dose")}]
        for number, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                continue
            value = quantity_text(entry.get("doseQuantity"))
            if isinstance(entry.get("doseRange"), dict):
                bounds = entry["doseRange"]
                value = f"range {quantity_text(bounds.get('low'))} to {quantity_text(bounds.get('high'))}"
            lines.append(f"{dose_label} dose, instruction {index}, entry {number}: {value}.")
        if dosage.get("rateQuantity"):
            lines.append(f"Declared rate: {quantity_text(dosage['rateQuantity'])}; elapsed duration: {quantity_text(data['duration'])}.")
        if dosage.get("route"):
            lines.append(f"Declared route, instruction {index}: {concept_text(dosage['route'])}.")
    if data["resource_type"] == "Medication":
        lines.append("Product description: no dose is required (dose_check=not_applicable).")
    elif not data["dosages"]:
        lines.append("No structured dosage was supplied.")
    dose_state = row.get("dose_check", "review_required")
    explanation = {"review_required": "overall review remains open, including when a partial calculation is consistent",
                   "inconsistent": "an error was found in dosage, duration or strength",
                   "not_applicable": "standalone product; no dose check"}.get(dose_state, "inspect detailed findings")
    lines.append(f"Dose review: {dose_state} — {explanation}.")
    lines.append("All timing, PRN, rate and other constraints remain in the source details; no daily dose is inferred.")
    calculations = 0
    for proposal in detail["recommendations"]:
        if proposal["type"] == "strength_conversion":
            calculations += 1
            lines.append(f"Conditional calculation: {proposal['input_value']} {proposal['input_unit']} → {proposal['proposed_value']} {proposal['target_unit']} (factor {proposal['factor']}). Requires verification of the source-declared strength and ingredient; not a replacement dose.")
        elif proposal["type"] == "infusion_comparison":
            calculations += 1
            outcome = "numerically consistent" if proposal["matches"] else "numerically different"
            assumption = "source asserts constant uninterrupted delivery" if proposal["constant_rate_asserted"] else "constant uninterrupted delivery is unconfirmed; comparison is conditional"
            lines.append(f"Rate × duration: {proposal['expected_value']} {proposal['unit']}; recorded dose: {proposal['recorded_value']} {proposal['unit']} — {outcome}; {assumption}.")
    if not calculations and data["resource_type"] != "Medication":
        lines.append("No supported dose calculation is available; review the findings and lookup coverage below.")
    errors = sum(f["state"] == "error" for f in detail["findings"])
    open_points = sum(f["state"] == "review" for f in detail["findings"])
    lines.append(f"Open findings: {errors} inconsistencies, {open_points} review points. Code check: {row['code_check']}; unit check: {row['unit_check']}. See explanations and sources below.")
    lines.append("Clinical dose safety is not assessed. A consistent calculation does not close the overall dosage review.")
    return lines


def html_report(path, rows, summary, csv_name):
    def text(value):
        return escape(str(value))
    blocks = []
    for index, (row, detail) in enumerate(rows, 1):
        label = row["product_name"] or "; ".join(c.get("code", "") for c in detail["source_set"]["medication"].get("code", {}).get("coding", [])) or "Unnamed product"
        fields = medication_fields(row, detail)
        prefix = "item-" + str(row["tuple_id"])
        known = row["frequency_known"] is True or str(row["frequency_known"]).lower() == "true"
        overview = '<h3>Item summary</h3><ul>' + ''.join('<li>' + text(line) + '</li>' for line in item_summary(row, detail)) + '</ul>'
        trace = '<details><summary>Lookup attempts and catalogue editions</summary><pre>' + text(json.dumps(detail['terminology'], ensure_ascii=False, indent=2)) + '</pre></details>'
        proposals = '<details><summary>Recorded proposals, calculations and DDD context</summary><pre>' + text(json.dumps({k: detail[k] for k in ('recommendations', 'ddd_references', 'findings')}, indent=2, ensure_ascii=False)) + '</pre><p>Published DDD is classification context, never a recommended dose.</p></details>'
        source = '<details><summary>Original source fields</summary><pre>' + text(json.dumps(detail['source_set'], ensure_ascii=False, indent=2, default=str)) + '</pre></details>'
        blocks.append('<section class="item" data-tuple-id="' + text(row['tuple_id']) + '"><div class="item-heading"><h2>'
            + str(index) + '. ' + text(label) + '</h2><p>' + text(row['occurrence_count'] if known else 'Unknown') + ' occurrences</p></div>'
            + '<p class="muted">' + text(row['resource_type']) + ' · status: ' + text(row.get('medication_status') or 'not supplied')
            + ' · intent: ' + text(row.get('intent') or 'not supplied') + ' · source verdict: ' + text(row['status']) + '</p>'
            + field_table_html(fields, prefix=prefix)
            + '<details class="set-details"><summary>Sources &amp; details</summary>' + field_details_html(fields, prefix=prefix)
            + '<p>Set <code>' + text(row['tuple_id']) + '</code></p>'
            + '<details><summary>Complete item summary</summary>' + overview + '</details>' + item_context_html(row)
            + source + trace + proposals + '</details></section>')
    notice = '<p class="note">' + text(summary["view_notice"]) + '</p>' if summary.get("view_notice") else ''
    path.write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';"><title>Medication plausibility review</title><style>{comparison_css}</style><main><h1>Medication plausibility review</h1><p>Lookup + deterministic consistency checks · <a href="{text(csv_name)}">CSV</a></p><p>{summary["input_records"]} source rows → {summary["distinct_tuples"]} report items · FHIR validation: {text(summary["fhir_validation"]["state"])} · Clinician mapping approval: not implemented.</p>{notice}
<p class="muted">One table per item. Read across each field: input → finding → recommendation. Clinical dose safety is not assessed.</p>
{legend_html()}
<p class="muted">AI-supported lookup / study evidence: Not implemented for medication. Strength conversions use source-declared product strength; no dose replacement is proposed.</p>
{"".join(blocks)}
<p class="muted">This preview shows at most 2,000 items; the CSV contains all results.</p>
<details class="run-details"><summary>Configuration, methods &amp; audit details</summary>{run_context_html(summary, csv_name)}<p>{text(SCOPE)}</p><p>Source statuses: passed = no issue in implemented checks; needs_review = unresolved or conditional evidence; error = supported inconsistency. dose_check is an overall review flag, not clinical dose approval. Colours apply only to the stated field check; new conversions always require review.</p><p>Counts: {text(summary["statuses"])}</p></details></main></html>''', encoding="utf-8")


def ordered(value):
    return {key: ordered(value[key]) for key in sorted(value)} if isinstance(value, dict) else [ordered(v) for v in value] if isinstance(value, list) else value


def states(rows):
    values = {r["state"] for r in rows}
    return "invalid" if "invalid" in values else "valid" if values == {"valid"} else "unknown"


def review_medication(config):
    target = config.path_value(config.output.get("path", "output/medication-review.csv"))
    if target.suffix.lower() != ".csv":
        raise ValueError("Medication output.path must end in .csv")
    paths = {key: target.with_suffix(ext) for key, ext in {"csv": ".csv", "report": ".html", "summary": ".summary.json", "log": ".audit.jsonl", "evidence": ".evidence.jsonl"}.items()}
    if config.validation.get("enabled") and config.full_fhir_input:
        paths["fhir_validation"] = target.with_suffix(".fhir-validation.jsonl")
    protected = {config.path}
    def path_refs(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, str) and (k == "path" or k.endswith(("_file", "_FILE")) or k in {"STRUCTURE_DEFINITION", "PATH_TO_DB_CONFIG_TOML"}) and v:
                    protected.add(config.path_value(v))
                elif k == "VALUE_SETS":
                    protected.update(config.path_value(x) for x in v)
                else:
                    path_refs(v)
    path_refs({k: v for k, v in config.data.items() if k != "output"})
    if any(p == ref or ref.is_dir() and p.is_relative_to(ref) for p in paths.values() for ref in protected):
        raise ValueError("Output cannot replace source data, configuration, secrets or terminology files")
    if any(p.exists() for p in paths.values()) and not config.output.get("overwrite", False):
        raise ValueError("Output already exists; choose a new path or set output.overwrite=true")
    run_id, start = str(uuid.uuid4()), time.monotonic()
    updates = {"SQC_BATCH_CDS_DSN": config.toolchain_database()} if config.input_type == "postgres" and config.database.get("PATH_TO_DB_CONFIG_TOML") else {}
    with tempfile.TemporaryDirectory(prefix=".medication-review-", dir=target.parent) as directory:
        folder, checker = Path(directory), None
        log = AuditLog(folder / paths["log"].name, run_id)
        phase = "configuration"
        try:
            log.event("started", software=versions(), configuration_sha256=config.deployment.sha256,
                      operation=config.operation, resource_type=config.kind, network_mode=config.network_mode,
                      mode=config.output.get("mode", "distinct"), output_path=str(target))
            with environment(updates), config.deployment.prepare(research=False) as prepared, closing(duckdb.connect(str(folder / "groups.duckdb"))) as db:
                routes = terminology_plan(config)
                artifacts = reference_artifacts(config)
                log.event("reference_artifacts", artifacts=artifacts, terminology_plan=routes)
                validation = {"state": "not_applicable" if config.validation.get("enabled") else "disabled",
                              "scope": "Full resource validation requires native FHIR/JSON-in-CSV input; scalar/DB rows receive semantic checks only"}
                if "fhir_validation" in paths:
                    phase = "fhir_resource_validation"
                    validation = validate_input(config, prepared.source["path"], folder / paths["fhir_validation"].name, **config.fhir_reader_options)
                    validation["operation_outcomes"] = paths["fhir_validation"].name
                    log.event("fhir_resources_validated", **validation)
                phase = "standardization_and_grouping"
                db.execute("CREATE TABLE groups (key VARCHAR PRIMARY KEY, payload VARCHAR, source_rows BIGINT, occurrences HUGEINT, known BOOLEAN, id VARCHAR, ref VARCHAR)")
                db.execute("CREATE TABLE ids (id VARCHAR PRIMARY KEY)")
                count = skipped = 0
                mode = config.output.get("mode", "distinct")
                input_hash = _input_hash(prepared.source["path"]) if config.input_type != "postgres" else None
                for record in records(config, prepared, folder):
                    if record is None:
                        skipped += 1
                        continue
                    payload, frequency, identifier, ref = record
                    count += 1
                    if identifier and config.input.get("granularity", "individual") == "individual":
                        try:
                            db.execute("INSERT INTO ids VALUES (?)", [identifier])
                        except duckdb.ConstraintException:
                            raise InputContractError("Repeated medication source ID; choose one version upstream") from None
                    canonical = fhir_json(ordered(payload))
                    key = hashlib.sha256((canonical + (f":item:{count}" if mode == "items" else "")).encode()).hexdigest()
                    # No IDs in distinct payload/group identity; every dose/timing/context field is retained.
                    db.execute("INSERT INTO groups VALUES (?,?,?,?,?,?,?) ON CONFLICT (key) DO UPDATE SET source_rows=groups.source_rows+1, occurrences=groups.occurrences+excluded.occurrences, known=groups.known AND excluded.known",
                               [key, fhir_json(payload), 1, frequency or 0, frequency is not None, identifier if mode == "items" else "", ref if mode == "items" else ""])
                if not count:
                    raise InputContractError("Input contains no selected medication records")
                log.event("grouped", input_records=count, skipped_resources=skipped, groups=db.execute("SELECT COUNT(*) FROM groups").fetchone()[0])
                phase = "terminology_and_dose_checks"
                assessment_run = run_context(config, prepared, output_path=target)
                checker = MedicationChecker(config, prepared)
                preview, statuses, total, ready, known_occurrences, unknown_groups = [], {}, 0, 0, 0, 0
                fields = FIELDS + (["source_id", "source_ref"] if mode == "items" else [])
                with (folder / paths["csv"].name).open("w", encoding="utf-8-sig", newline="") as out, (folder / paths["evidence"].name).open("w", encoding="utf-8") as evidence:
                    writer = csv.DictWriter(out, fieldnames=fields)
                    writer.writeheader()
                    cursor = db.execute("SELECT * FROM groups ORDER BY key")
                    while batch := cursor.fetchmany(100):
                        for key, text, nrows, occurrences, known, identifier, ref in batch:
                            payload = json.loads(text, parse_float=Decimal)
                            detail = {**checker.assess(payload), "tuple_id": key, "source_set": payload}
                            product = payload["medication"].get("code", {})
                            terms = detail["terminology"]
                            row = dict(tuple_id=key, resource_type=config.kind, source_rows=nrows,
                                       occurrence_count=occurrences if known else "", frequency_known=known,
                                       status=detail["status"], medication_status=payload["status"], intent=payload["intent"],
                                       product_name=product.get("text") or "; ".join(c.get("display", "") for c in product.get("coding", []) if isinstance(c, dict)),
                                       product_codings_json=encoded(product.get("coding", [])), medication_json=fhir_json(payload["medication"]),
                                       dosage_json=fhir_json(payload["dosages"]), duration_json=fhir_json(payload["duration"]),
                                       constant_rate_asserted=payload["constant_rate"], source_set_json=fhir_json(payload),
                                       code_check=states([x for x in terms if x["path"] == "Medication.code"]),
                                       unit_check=states([x for x in terms if x.get("kind") == "quantity"]),
                                       dose_check="not_applicable" if config.kind == "Medication" else "inconsistent" if any(f["state"] == "error" and f["path"].startswith(("dosage", "duration", "Medication.ingredient")) for f in detail["findings"]) else "review_required",
                                       canonical_names_json=encoded([p for p in detail["recommendations"] if p["type"] == "canonical_name"]),
                                       findings_json=encoded(detail["findings"]), recommendations_json=encoded(detail["recommendations"]),
                                       sources_json=encoded(terms), ddd_references_json=encoded(detail["ddd_references"]),
                                       clinical_dose_safety="not_assessed", fhir_validation_state=validation["state"])
                            if mode == "items":
                                row.update(source_id=identifier, source_ref=ref)
                            detail["assessment_context"] = item_context(assessment_run, row, detail)
                            row["assessment_context_json"] = encoded(detail["assessment_context"])
                            writer.writerow({k: _csv_text(v) if isinstance(v, str) else v for k, v in row.items()})
                            evidence.write(fhir_json(detail) + "\n")
                            if len(preview) < 2000:
                                preview.append((row, detail))
                            total += 1
                            ready += detail["status"] == "passed"
                            known_occurrences += occurrences
                            unknown_groups += not known
                            statuses[detail["status"]] = statuses.get(detail["status"], 0) + 1
                            if total % 100 == 0:
                                log.event("check_progress", assessed_groups=total, statuses=statuses)
                summary = {"format_version": 1, "resource_type": config.kind, "operation": config.operation, "run_context": assessment_run,
                           "output_path": str(target), "mode": mode, "input_records": count, "distinct_tuples": total,
                           "skipped_resources": skipped, "input_sha256": input_hash, "configuration_sha256": config.deployment.sha256,
                           "statuses": statuses, "known_source_occurrences": known_occurrences, "groups_with_unknown_frequency": unknown_groups,
                           "fhir_validation": validation, "specification": config.data.get("specification", {}), "terminology_plan": routes,
                           "metrics": {"terminology_and_dose_consistent": ratio(ready, total)}, "scope": SCOPE,
                           "source_strength": "Source assertion only; not a verified drug label", "elapsed_seconds": round(time.monotonic()-start, 2),
                           "software": versions(), "reference_artifacts": artifacts}
                summary["output_hashes"] = {paths[k].name: _input_hash(folder / paths[k].name) for k in paths if k not in {"summary", "report", "log"}}
                (folder / paths["summary"].name).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
                html_report(folder / paths["report"].name, preview, summary, target.name)
                log.event("completed", output_rows=total, statuses=statuses, output_hashes={**summary["output_hashes"],
                          paths["summary"].name: _input_hash(folder / paths["summary"].name), paths["report"].name: _input_hash(folder / paths["report"].name)})
                log.close()
                for key in [k for k in paths if k != "summary"] + ["summary"]:
                    source = folder / paths[key].name
                    os.chmod(source, 0o600)
                    os.replace(source, paths[key])
                return BatchResult(target, count, total, statuses, skipped, paths["summary"], paths["report"], paths["log"], validation["state"])
        except Exception as exc:
            if not log.handle.closed:
                log.event("failed", phase_name=phase, error_type=type(exc).__name__, diagnostic=str(exc) if isinstance(exc, InputContractError) else "redacted_external_or_data_exception")
                log.close()
                failed = target.with_name(target.stem + ".failed." + run_id + ".audit.jsonl")
                os.replace(folder / paths["log"].name, failed)
            raise ValueError(f"Medication batch failed during {phase} ({type(exc).__name__}); check the failure audit") from None
        finally:
            if checker:
                checker.close()
