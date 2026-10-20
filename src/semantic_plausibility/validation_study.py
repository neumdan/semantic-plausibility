"""Offline clinician evaluation of audited laboratory reports; no mapping approvals."""

import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path
import statistics

from .review_feedback import read_csv
from .report_context import workflow_name
from .study_compare import verify_audit


IDENTITY_FIELDS = ("report_sha256", "tuple_id", "code", "analyte_name", "unit_code")
REVIEW_CHOICES = {
    "reference_assessment": {"correct", "error", "metadata_only", "indeterminate"},
    "recommendation_assessment": {"acceptable", "partly_correct", "incorrect", "unassessable", "no_proposal"},
    "evidence_assessment": {"supported_applicable", "unsupported_or_inapplicable", "uncertain", "not_present"},
}
REVIEW_FIELDS = (
    "reference_assessment", "issue_fields", "recommendation_assessment", "evidence_assessment",
    "review_seconds", "reviewer", "notes",
)
FIELDS = (*IDENTITY_FIELDS, *REVIEW_FIELDS)
SCOPES = ("profile_qc", "identity_set", "quantity_set", "code_quantity_fit", "complete_set")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _workflow(summary):
    context = summary.get("run_context", {})
    name = workflow_name(context)
    if not context.get("workflow") and name == "lookup" and summary.get("fhir_validation", {}).get("documents"):
        return "fhir-validation"
    return name


def _study_report(report):
    report = Path(report)
    verify_audit(report.with_suffix(".audit.jsonl"))
    summary = json.loads(report.with_suffix(".summary.json").read_text())
    digest = _sha256(report)
    if summary.get("output_hashes", {}).get(report.name) != digest:
        raise ValueError("Use the original audited report CSV")
    if summary.get("mode") != "distinct":
        raise ValueError('Prepare a laboratory check with output.mode="distinct" for this study')
    rows = read_csv(report)
    indexed = {}
    for row in rows:
        if row.get("resource_type") != "Observation":
            raise ValueError("This validation study evaluates laboratory Observation sets")
        identity = row.get("tuple_id")
        if not identity or identity in indexed:
            raise ValueError("Study report needs unique tuple_id values")
        # These persisted assessments are the ones shown in the original CSV/HTML.
        states = [row.get(f"qc.{scope}.state") for scope in SCOPES]
        proposals = [v for k, v in row.items() if v and k.startswith("assessment.") and k.endswith(".proposal_status")]
        if not all(states) or not proposals or not str(row.get("detected_error_count", "")).isdigit():
            raise ValueError("Recreate the report with current field and assessment-scope columns")
        if any(v not in {"green", "yellow", "red", "conditional", "grey"} for v in proposals):
            raise ValueError("Unknown proposal status in the study report")
        indexed[identity] = {
            **{key: row.get(key, "") for key in IDENTITY_FIELDS},
            "report_sha256": digest,
            "system_error": int(row["detected_error_count"]) > 0 or "error" in states,
            "system_review": any(v in {"yellow", "red", "conditional"} for v in proposals),
        }
        indexed[identity]["system_review"] |= indexed[identity]["system_error"]
    return summary, indexed, digest


def prepare_study_review(report, output):
    """Create an unlabelled clinician CSV bound to one original distinct report."""
    summary, rows, digest = _study_report(report)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows.values():
            # Source cells are already formula-escaped by the report CSV writer.
            writer.writerow({key: row[key] for key in IDENTITY_FIELDS})
    return {"review_csv": str(output), "sets": len(rows), "report_sha256": digest,
            "workflow": _workflow(summary),
            "next": "Complete the review columns using docs/studies.md, then run study-metrics"}


def _reviewed_cases(review, source):
    with Path(review).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if len(reader.fieldnames or []) != len(FIELDS) or set(reader.fieldnames or []) != set(FIELDS):
            raise ValueError("Keep the study-template CSV headers; use docs/studies.md for allowed values")
        seen, cases = set(), []
        for line, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Review row {line}: keep one value per CSV column")
            identity = row["tuple_id"]
            if identity not in source or identity in seen:
                raise ValueError(f"Review row {line}: unknown or duplicate tuple_id")
            seen.add(identity)
            original = source[identity]
            if any(row[key] != original[key] for key in IDENTITY_FIELDS):
                raise ValueError(f"Review row {line}: identity/source cells differ from the original report")
            row = {**row, **{key: row[key].strip() for key in REVIEW_FIELDS}}
            for key, choices in REVIEW_CHOICES.items():
                if row[key] and row[key] not in choices:
                    raise ValueError(f"Review row {line}: {key} must be one of {', '.join(sorted(choices))}")
            if any(row[key] for key in REVIEW_FIELDS) and not (row["reference_assessment"] and row["reviewer"]):
                raise ValueError(f"Review row {line}: supply reference_assessment and reviewer")
            if row["reference_assessment"] in {"error", "metadata_only"} and not row["issue_fields"]:
                raise ValueError(f"Review row {line}: name the affected fields in issue_fields")
            seconds = None
            if row["review_seconds"]:
                try:
                    seconds = float(row["review_seconds"])
                except ValueError:
                    raise ValueError(f"Review row {line}: review_seconds must be a finite non-negative number") from None
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError(f"Review row {line}: review_seconds must be a finite non-negative number")
            cases.append({**original, **row, "review_seconds": seconds})
    # Keep all sampled cases visible: incomplete reviews stay blank, not deleted.
    if seen != set(source):
        raise ValueError("Keep every template row; leave unfinished reviews blank")
    return cases


def _rate(numerator, denominator, *, interval=False):
    result = {"numerator": numerator, "denominator": denominator,
              "value": numerator / denominator if denominator else None}
    if interval:
        bounds = None
        if denominator:
            # Wilson score interval, 95%, for unweighted set-level proportions.
            z = 1.959963984540054
            p, n = numerator / denominator, denominator
            centre = (p + z * z / (2 * n)) / (1 + z * z / n)
            half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
            bounds = [max(0, centre - half), min(1, centre + half)]
        result["wilson_95"] = bounds
    return result


def _summarize(cases):
    """Pure study arithmetic; blanks/uncertainty are never silently negative."""
    counts = {key: {choice: sum(c.get(key) == choice for c in cases) for choice in sorted(choices)}
              for key, choices in REVIEW_CHOICES.items()}
    judged = [c for c in cases if c.get("reference_assessment") in {"correct", "error"}]
    errors = [c for c in judged if c["reference_assessment"] == "error"]
    tp = sum(c["system_error"] for c in errors)
    fp = sum(c["system_error"] for c in judged if c["reference_assessment"] == "correct")
    fn, tn = len(errors) - tp, len(judged) - len(errors) - fp
    recommendations = counts["recommendation_assessment"]
    assessed = sum(recommendations.values())
    eligible = sum(recommendations[k] for k in ("acceptable", "partly_correct", "incorrect"))
    evidence = counts["evidence_assessment"]
    timed = [c["review_seconds"] for c in cases if c.get("review_seconds") is not None]
    metrics = {
        "reference_review_completion": _rate(sum(counts["reference_assessment"].values()), len(cases)),
        "error_evaluation_coverage": _rate(len(judged), len(cases)),
        "error_detection_sensitivity": _rate(tp, tp + fn, interval=True),
        "error_flag_precision": _rate(tp, tp + fp, interval=True),
        "review_capture_sensitivity": _rate(sum(c["system_review"] for c in errors), len(errors)),
        "review_queue_fraction": _rate(sum(c["system_review"] for c in cases), len(cases)),
        "recommendation_review_completion": _rate(assessed, len(cases)),
        "recommendation_acceptability": _rate(recommendations["acceptable"], eligible, interval=True),
        "proposal_availability_among_assessed": _rate(assessed - recommendations["no_proposal"], assessed),
        "evidence_applicability": _rate(evidence["supported_applicable"],
                                      evidence["supported_applicable"] + evidence["unsupported_or_inapplicable"]),
    }
    return {"sets": len(cases), "counts": counts, "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "metrics": metrics,
            "excluded_error_flags": {label: sum(c["system_error"] for c in cases if c.get("reference_assessment") == label)
                                     for label in ("metadata_only", "indeterminate")},
            "effort": {"timed_sets": len(timed), "total_minutes": sum(timed) / 60 if timed else None,
                       "median_seconds_per_set": statistics.median(timed) if timed else None}}


def _html(result):
    def display(value):
        return "Not estimated" if value is None else f"{value:.3g}"

    rows = []
    for name, metric in result["metrics"].items():
        bounds = metric.get("wilson_95")
        percent = "Not estimated" if metric["value"] is None else f'{100 * metric["value"]:.1f}%'
        interval = f"{100 * bounds[0]:.1f}–{100 * bounds[1]:.1f}%" if bounds else "—"
        rows.append(f"<tr><th>{escape(name.replace('_', ' '))}</th><td>{metric['numerator']}/{metric['denominator']}</td>"
                    f"<td>{percent}</td><td>{interval}</td></tr>")
    labels = "".join(f"<tr><th>{escape(key.replace('_', ' '))}</th><td>{escape(label)}</td><td>{count}</td></tr>"
                     for key, choices in result["counts"].items() for label, count in choices.items())
    effort = result["effort"]
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Laboratory validation study</title><style>body{font:16px/1.5 system-ui;max-width:1000px;'
            'margin:32px auto;padding:16px;color:#172e3a}table{border-collapse:collapse;width:100%;margin:20px 0}'
            'th,td{text-align:left;padding:8px;border-bottom:1px solid #d5e1e4}th{background:#edf5f3}'
            'code{overflow-wrap:anywhere}</style><h1>Laboratory validation study</h1>'
            f"<p>{result['sets']} distinct sets · workflow: {escape(result['workflow'])}</p>"
            '<p>One set is one case. Error sensitivity/precision use only clinician labels correct and error. '
            'Metadata-only, indeterminate and unfinished cases are counted separately. A review request '
            'is measured separately from a definite error flag.</p>'
            '<table><tr><th>Metric</th><th>n / denominator</th><th>Result</th><th>95% Wilson interval</th></tr>'
            + "".join(rows) + '</table><p>Recommendation acceptability covers the whole action plan, including keep; '
            'partly correct counts against acceptability. Unassessable/no-proposal cases are excluded from that rate. '
            'Evidence applicability excludes uncertain/absent evidence.</p>'
            '<h2>Review labels</h2><table><tr><th>Assessment</th><th>Label</th><th>Sets</th></tr>'
            + labels + '</table><h2>Review effort</h2>'
            f"<p>{effort['timed_sets']} timed sets · total {display(effort['total_minutes'])} minutes · "
            f"median {display(effort['median_seconds_per_set'])} seconds/set. Blank times are excluded.</p>"
            f"<p>Error flags among metadata-only sets: {result['excluded_error_flags']['metadata_only']}; "
            f"among indeterminate sets: {result['excluded_error_flags']['indeterminate']}.</p>"
            '<p>Intervals assume independent sampled sets; related analytes/site clusters and enriched error samples '
            'need a study-specific analysis. Rates describe reviewed cases, not automatic clinical approval.</p>'
            f"<p>Report SHA-256: <code>{result['report_sha256']}</code><br>"
            f"Review SHA-256: <code>{result['review_sha256']}</code></p></html>")


def evaluate_study_review(report, review, output):
    """Score human labels against frozen report flags; publish JSON + a small HTML table."""
    summary, source, digest = _study_report(report)
    try:
        cases = _reviewed_cases(review, source)
    except csv.Error as exc:
        raise ValueError(f"Malformed review CSV: {exc}") from exc
    output = Path(output)
    files = [output.with_suffix(suffix) for suffix in (".json", ".html")]
    if any(path.exists() for path in files):
        raise ValueError("Study output already exists; choose a new --out prefix")
    result = {
        "schema_version": 1,
        "report_sha256": digest, "review_sha256": _sha256(review),
        "configuration_sha256": summary.get("configuration_sha256"),
        "workflow": _workflow(summary),
        "unit_of_analysis": "distinct_laboratory_set",
        "error_flag_rule": "detected_error_count > 0 or any of the five qc scope states is error",
        "review_flag_rule": "error flag or any field proposal is yellow, red or conditional",
        **_summarize(cases),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for path, text in zip(files, (json.dumps(result, indent=2, allow_nan=False) + "\n", _html(result))):
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
    return {**result, "json": str(files[0]), "html": str(files[1])}
