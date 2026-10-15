"""Local evidence grades, explicit readiness denominators and an offline HTML report."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import platform
from importlib.metadata import version, PackageNotFoundError

from ._version import __version__
from .audit import STATE_LABELS, canonical, encoded

STATES = {label: state for state, label in STATE_LABELS.items()}

FIELDS = [
    "tuple_id",
    "input_rows",
    "occurrence_count",
    "frequency_known",
    "resource_type",
    "profile",
    "code",
    "code_system",
    "code_version",
    "value",
    "value_min",
    "value_max",
    "unit",
    "unit_code",
    "unit_system",
    "specimen",
    "method",
    "reference_low",
    "reference_high",
    "reference_unit",
    "reference_ranges_json",
    "status",
    "reason",
    "code_check",
    "unit_check",
    "code_unit_fit",
    "reference_check",
    "value_reference_check",
    "suggested_unit",
    "suggested_factor",
    "suggestion_basis",
    "transformation_json",
    "evidence_sources",
    "evidence_json",
    "input_type",
    "input_origin",
    "fhir_version",
    "ig_package",
    "ig_version",
    "ig_canonical",
    "configuration_sha256",
    "assessment_context_json",
    "review_status",
    "review_complete",
    "review_correct",
    "review_notes",
]


def review_row(row, evidence, config):
    """Project the checked semantic set into the stable public CSV columns."""
    stats = evidence["observed_values"]
    result = {
        "tuple_id": evidence["tuple_id"],
        "input_rows": stats["aggregate_inputs"] + stats["individual_inputs"],
        "occurrence_count": stats["occurrences"],
        "frequency_known": not bool(stats["unknown_frequency_inputs"]),
        "resource_type": "Observation",
        "profile": config.profile,
        "value": stats.get("group_value", ""),
        "reference_ranges_json": json.dumps(evidence["metadata"]["reference_ranges"], ensure_ascii=False),
        "configuration_sha256": config.deployment.sha256,
    }
    spec = config.data.get("specification", {})
    result.update(
        input_type=config.input_type,
        input_origin=config.input.get("origin", ""),
        fhir_version=spec.get("FHIR_VERSION", "4.0.1"),
        ig_package=spec.get("IG_PACKAGE", ""),
        ig_version=spec.get("IG_VERSION", ""),
        ig_canonical=spec.get("IG_CANONICAL", ""),
    )
    aliases = {
        "code_system": "codesystem",
        "code": "code",
        "code_version": "code_version",
        "value_min": "werte_min",
        "value_max": "werte_max",
        "unit": "eingangseinheit",
        "unit_code": "einheiten_code",
        "unit_system": "einheiten_system",
        "specimen": "material",
        "method": "methode",
        "reference_low": "referenz_unten",
        "reference_high": "referenz_oben",
        "reference_unit": "referenz_einheit",
        "status": "status",
        "code_check": "code_pruefung",
        "unit_check": "einheit_pruefung",
        "code_unit_fit": "code_einheit_pruefung",
        "reference_check": "referenz_pruefung",
        "value_reference_check": "werte_referenz_pruefung",
        "suggested_unit": "vorschlag_einheit",
        "suggested_factor": "vorschlag_faktor",
        "suggestion_basis": "vorschlag_grundlage",
        "evidence_sources": "evidenz_quellen",
    }
    for target, original in aliases.items():
        value = row.get(original, "")
        result[target] = STATES.get(value, value)
    result["code_unit_fit"] = evidence.get("check", {}).get("code_unit_fit", result["code_unit_fit"])
    local = evidence.get("check", {}).get("values", {}).get("local_reference", [])
    result["value_reference_check"] = (
        "; ".join((item.get("state", "unknown") for item in local)) if isinstance(local, list) else "unknown"
    )
    result["reason"] = {
        "passed": "All assessed checks passed within the stated scope.",
        "suggestion": "A documented mapping or normalization needs expert approval.",
        "unclear": "Evidence, context or a unique mapping is missing; review is required.",
        "error": "At least one deterministic code, unit, profile or numeric check failed.",
        "not_applicable": "Outside the quantitative laboratory scope.",
    }[row["status"]]
    result["transformation_json"] = json.dumps(
        evidence.get("recommendation", {}).get("transformation", {}), ensure_ascii=False
    )
    result["evidence_json"] = (
        json.dumps(evidence, ensure_ascii=False) if config.output.get("include_evidence", True) else ""
    )
    return result


READINESS_DEFINITION = (
    "Selected quantitative laboratory groups with an explicit valid LOINC coding, explicit valid UCUM unit code, "
    "documented unchanged or verified linearly convertible code-unit fit, finite ordered value/bounds, at least one numerically valid reference "
    "interval, and no deterministic error, ambiguous LOINC identity or uninterpreted modifier. "
    "This numeric semantic readiness proxy is not MII/FDPG certification, full FHIR validation or clinical plausibility."
)


def versions():
    libraries = {}
    for name in ("duckdb", "psycopg", "psycopg-binary"):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = "not_installed"
    return {"semantic_plausibility": __version__, "python": platform.python_version(), "libraries": libraries}


class AuditLog:
    """Append-only hash chain. Never log raw identifiers, values or credentials."""

    def __init__(self, path: Path, run_id: str):
        self.handle = path.open("w", encoding="utf-8")
        self.previous, self.sequence, self.run_id = "", 0, run_id

    def event(self, phase, **details):
        from .progress import phase_event
        phase_event(phase, details)
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "run_id": self.run_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "previous_sha256": self.previous,
            "details": details,
        }
        digest = hashlib.sha256(encoded(record).encode()).hexdigest()
        self.handle.write(json.dumps({**record, "sha256": digest}, ensure_ascii=False) + "\n")
        self.handle.flush()
        self.previous = digest

    def close(self):
        self.handle.close()


def semantic_signature(payload):
    """Study identity ignores representation-only labels/path; retains semantic qualifiers."""
    data = dict(payload)
    data.pop("path", None)
    data.pop("input_issues", None)
    code = data.get("code", {})
    if code.get("coding"):
        data["code"] = {
            "coding": [
                {key: value for key, value in coding.items() if key in {"system", "code", "version"}}
                for coding in code["coding"]
            ]
        }
    data["context"] = {key: value for key, value in data.get("context", {}).items() if key != "source_test"}
    return hashlib.sha256(encoded(canonical(data)).encode()).hexdigest()


def grade(row, evidence):
    check = evidence["report"]["measurements"][0]
    stats = evidence["observed_values"]
    ranges = evidence.get("reference_metadata", [])
    numeric = (stats["minimum"] is not None or stats["maximum"] is not None) and not stats["invalid"]
    refs = bool(ranges) and all(interval.get("state") == "bounds_valid" for interval in ranges)
    ready = (
        row["code_system"] == "http://loinc.org"
        and row["unit_system"] == "http://unitsofmeasure.org"
        and row["code_check"] == "valid"
        and row["unit_check"] == "valid"
        and row["code_unit_fit"] in {"correct", "conversion_verified"}
        and numeric
        and refs
        and row["status"] != "error"
        and not check.get("multiple_loinc_codes")
        and not check.get("multiple_loinc_versions")
        and not evidence["metadata"].get("modifier_extensions")
    )
    fields = {
        "code": row["code_check"] == "valid",
        "unit": row["unit_check"] == "valid",
        "numeric_value_or_bounds": bool(numeric),
        "numeric_reference": refs,
    }
    supported = sum(fields.values())
    credibility = (
        "rejected"
        if row["status"] == "error"
        else "supported_by_configured_sources"
        if supported == 4
        else "partial"
        if supported
        else "unverified"
    )
    return {
        "lookup_credibility": credibility,
        "code_verified": fields["code"],
        "unit_verified": fields["unit"],
        "value_numeric_ready": fields["numeric_value_or_bounds"],
        "reference_numeric_ready": refs,
        "mii_numeric_set_ready": bool(ready),
        "clinical_value_evidence": "not_established",
        "clinical_reference_evidence": "not_established",
    }


def ratio(numerator, denominator):
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": numerator / denominator if denominator else None,
    }


def write_html(path, rows, summary, csv_name, log_name):
    from .report_sources import recommendation_html, source_set_html, linked_data_html, source_html
    from .report_context import run_context_html, item_context_html
    from .research_summary import html as evidence_summary_html
    title = "Laboratory evidence research" if summary.get("operation") == "research-evidence" else "Laboratory export review"
    if summary.get("operation") == "research-evidence" and summary.get("research", {}).get("scope") == "lookup":
        title = "Laboratory terminology recommendations"
    metrics = summary["metrics"]
    head = "<th>Source set</th><th>Linked terminology</th><th>Complete proposal and supporting sources</th><th>Checks</th>"
    body = ""
    for row in rows:
        checks = {key: row.get(key, "") for key in ("status", "reason", "code_check", "unit_check", "code_unit_fit", "reference_check", "value_reference_check", "lookup_credibility", "mii_numeric_set_ready")}
        checked = "".join(f'<p><strong>{escape(key.replace("_", " "))}</strong>: {escape(str(value))}</p>' for key, value in checks.items())
        body += '<tr><td class="set">' + source_set_html(row) + '</td><td class="set">' + linked_data_html(row) + '</td><td class="recommendations">' + recommendation_html(row) + '</td><td class="set">' + checked + '</td></tr>'
        body += '<tr><td colspan="4" class="assessment-cell">' + item_context_html(row) + '</td></tr>'
    spec = summary.get("specification", {})
    links = {"FHIR R4 Observation": "https://hl7.org/fhir/R4/observation.html", **spec.get("links", {})}
    profile_html = '<section><h2>Target profile and resources</h2><p>Package: <code>' + escape(str(spec.get("IG_PACKAGE", "hl7.fhir.r4.core"))) + ' | ' + escape(str(spec.get("IG_VERSION", "4.0.1"))) + '</code></p>'
    if spec.get("IG_CANONICAL"):
        profile_html += source_html(spec["IG_CANONICAL"], title="Profile canonical")
    profile_html += '<ul>' + ''.join('<li>' + source_html(url, title=label) + '</li>' for label, url in links.items()) + '</ul><p>These are documentation links. Validation uses the pinned installed StructureDefinition and guide package; a link is not a validation result.</p></section>'
    research = summary.get("research", {})
    research_html = ""
    if summary.get("operation") == "research-evidence":
        research_html = '<section><h2>Research execution</h2><p>Scope: <strong>' + escape(str(research.get("scope", "not_recorded"))) + '</strong>. Local model: <strong>' + escape(str(research.get("model_state", "unknown"))) + '</strong>; accepted calls: ' + str(len(research.get("accepted_model_calls", []))) + '; retrieval requests: ' + str(research.get("retrieval_requests", "not_recorded")) + '; literature searches: ' + str(research.get("literature_searches", "not_recorded")) + '; extraction failures: ' + str(research.get("extraction_failures", "not_recorded")) + '.</p><p><a href="' + escape(Path(csv_name).with_suffix(".trace.jsonl").name, quote=True) + '">Executed searches, prompts and responses</a>. No accepted model response means extraction may be missing; inspect rejected/unavailable attempts before review.</p></section>'
    counts = " · ".join(f"{escape(key)}: {value}" for key, value in sorted(summary["statuses"].items()))
    readiness = metrics["mii_numeric_set_ready"]
    percent = f"{100 * readiness['ratio']:.1f}%" if readiness["ratio"] is not None else "unavailable"
    definitions = "".join(
        f"<li><strong>{escape(key.replace('_', ' '))}</strong>: {value['numerator']} / {value['denominator']}</li>"
        for key, value in metrics.items()
        if isinstance(value, dict) and "numerator" in value
    )
    validation = summary.get("fhir_validation", {"state": "disabled"})
    validation_html = '<section><h2>FHIR resource validation</h2><p>State: <strong>' + escape(str(validation["state"])) + '</strong>.</p>'
    if validation.get("operation_outcomes"):
        validation_html += '<p><a href="' + escape(validation["operation_outcomes"], quote=True) + '">Full validator OperationOutcomes</a></p>'
    validation_html += '<p>' + escape(str(validation.get("scope", "Full resource validation was not enabled for this run."))) + '</p>'
    if validation.get("profile"):
        validation_html += '<p>Requested profile: <code>' + escape(validation["profile"]) + '</code><br>Packages: ' + escape(", ".join(validation.get("packages", []))) + '</p>'
    if validation.get("issues"):
        validation_html += '<p>Validator issues: ' + escape(json.dumps(validation["issues"])) + '</p>'
    validation_html += '</section>'
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{title}</title><style>body{{font:16px/1.6 system-ui;background:#f4f7f9;color:#142d42;margin:0}}main{{max-width:1150px;margin:auto;padding:30px}}
section{{background:white;padding:24px;margin:18px 0;border:1px solid #d5e1e8;border-radius:8px}}h1,h2{{line-height:1.2}}a{{color:#176c63}}table{{border-collapse:collapse;font-size:13px;white-space:nowrap}}th,td{{padding:9px;text-align:left;vertical-align:top;border-bottom:1px solid #d5e1e8}}th{{background:#edf5f3}}.scroll{{overflow:auto}}.note{{border-left:3px solid #176c63;padding:14px;background:#eef7f4}}code{{font-family:monospace}}.recommendations{{white-space:normal;min-width:360px;max-width:520px}}.set{{white-space:normal;min-width:240px;max-width:360px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}.recommendation{{padding:12px 0;border-bottom:1px solid #d5e1e8}}.recommendation p{{margin:4px 0}}.recommendation ul{{padding-left:18px}}.source-meta{{display:block;color:#526978;font-size:12px;overflow-wrap:anywhere}}blockquote{{margin:8px 0;padding-left:12px;border-left:2px solid #176c63}}.recommendations code,.recommendations a{{overflow-wrap:anywhere}}.assessment-cell{{white-space:normal;max-width:1000px}}.assessment table{{white-space:normal}}summary{{cursor:pointer;font-weight:600}}</style>
<main><h1>{title}</h1><p>Local batch · {escape(summary["mode"])} · {escape(summary["run_id"])}</p>
{run_context_html(summary, csv_name)}
{evidence_summary_html(summary, csv_name)}
<section><h2>{percent} numerically ready laboratory sets</h2><p>{readiness["numerator"]} / {readiness["denominator"]} selected groups. {counts}</p>
<p class="note">{escape(READINESS_DEFINITION)}</p><p>Evidence mode: <strong>{escape(summary["evidence_kind"])}</strong>. Research evidence is reported separately from numerical completeness.</p><ul>{definitions}</ul>
<p>Observation-weighted readiness: {escape(str(summary.get("weighted_readiness")))}. Unknown source frequencies stay unknown.</p></section>
{profile_html}
{validation_html}
{research_html}
<section><h2>Review each set</h2><p><a href="{escape(csv_name, quote=True)}">Full review CSV</a> · <a href="{escape(log_name, quote=True)}">Auditable run log</a>. Read left to right: source data → linked terminology → complete proposed set and explanations. CSV retains the same structured data and expert review fields.</p>
<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div><p>Preview: {len(rows)} rows; the CSV contains every selected item/group.</p></section>
<section><h2>Reproduce and review</h2><p>Run/configuration, input and reference-pack hashes are recorded in the summary. Physicians/laboratory physicians review suggestions before any mapping is applied. Source data is unchanged.</p>
<pre>{escape(json.dumps(summary["software"], indent=2))}</pre><p>Full resource validation uses the configured local Matchbox/HL7 or MII validator. Its results remain separate from numeric semantic readiness and clinician approval. FDE availability/completeness remains a separate measure.</p></section></main></html>'''
    path.write_text(html, encoding="utf-8")
