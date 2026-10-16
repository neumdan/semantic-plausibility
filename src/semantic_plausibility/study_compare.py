"""Compare matched export reports without claiming reconstructed patient-level linkage."""
from __future__ import annotations

import csv
from html import escape
import json
from pathlib import Path

from .export_report import ratio
from .fhir_quality_pipeline import number
from .tabular_report import ColumnWriter, flatten


def read_report(path):
    path = Path(path)
    summary = json.loads(path.with_suffix(".summary.json").read_text())
    from .support import _input_hash
    expected = summary.get("output_hashes", {}).get(path.name)
    if not expected or _input_hash(path) != expected:
        raise ValueError("The comparison CSV differs from its recorded run; use the original report")
    if summary.get("mode") != "distinct":
        raise ValueError("Study comparison needs distinct reports")
    rows = {}
    # Evidence/distribution columns can exceed Python's small default CSV field
    # limit. Serialize the temporary parser setting with configured library runs.
    from .deployment import environment
    with environment({}):
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous_limit, 64 * 1024 * 1024))
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    key = row.get("semantic_signature")
                    if not key or key in rows:
                        raise ValueError("Report has ambiguous canonical groups; harmonize field selection before comparison")
                    rows[key] = row
        finally:
            csv.field_size_limit(previous_limit)
    return summary, rows


def compare_reports(baseline, targets, output):
    baseline_summary, source = read_report(baseline)
    study = baseline_summary.get("study", {})
    required = ("cohort", "period_start", "period_end")
    if not all(study.get(key) for key in required):
        raise ValueError("Comparison requires explicit matched cohort and period declarations")
    output = Path(output)
    files = [output.with_suffix(ext) for ext in (".csv", ".json", ".html")]
    if any(path.exists() for path in files):
        raise ValueError("Comparison output already exists; choose a new prefix")
    output.parent.mkdir(parents=True, exist_ok=True)
    comparisons, table = [], []
    for target in targets:
        summary, rows = read_report(target)
        if any(summary.get("study", {}).get(key) != study[key] for key in required):
            raise ValueError("Export cohort/period differs; loss ratios would not be comparable")
        label = summary.get("study", {}).get("source_label") or Path(target).stem
        shared = set(source) & set(rows)
        missing, added = set(source) - set(rows), set(rows) - set(source)
        changed_values, changed_frequency = 0, 0
        unknown = 0
        for key in sorted(set(source) | set(rows)):
            a, b = source.get(key), rows.get(key)
            state = "missing_in_target" if b is None else "added_or_changed_context" if a is None else "shared"
            value_comparison, count_difference = "not_comparable", ""
            if a and b:
                count_a, count_b = number(a.get("occurrence_count")), number(b.get("occurrence_count"))
                if count_a is not None and count_b is not None:
                    count_difference = str(count_b - count_a)
                    changed_frequency += count_a != count_b
                sha_a, sha_b = a.get("value_distribution_sha256"), b.get("value_distribution_sha256")
                if sha_a and sha_b:
                    value_comparison = "same_eligible_numeric_value_frequencies" if sha_a == sha_b else "changed_eligible_numeric_value_frequencies"
                    changed_values += sha_a != sha_b
                else:
                    unknown += 1
                    value_comparison = "summaries_only_no_exact_congruence_proof"
            row = b or a
            comparison = {"target": label, "semantic_signature": key, "code": row["code"], "unit_code": row["unit_code"],
                "comparison": state, "value_comparison": value_comparison, "target_minus_baseline_count": count_difference,
            }
            for side, item in (("baseline", a), ("target", b)):
                if not item:
                    continue
                # Scalar columns from current reports; parse the original array for older reports.
                comparison.update({side + "." + field.removeprefix("input."): value
                                   for field, value in item.items() if field.startswith("input.reference_ranges.")})
                if item.get("reference_ranges_json"):
                    flatten(json.loads(item["reference_ranges_json"]), side + ".reference_ranges", comparison)
            table.append(comparison)
        comparisons.append({"target": label, "baseline_groups": len(source), "target_groups": len(rows),
            "shared_groups": len(shared), "missing_or_changed_context": len(missing), "added_or_changed_context": len(added),
            "baseline_group_coverage": ratio(len(shared), len(source)), "changed_value_distributions": changed_values,
            "changed_known_frequencies": changed_frequency, "exact_value_comparison_unavailable": unknown,
            "target_readiness": summary["metrics"]["mii_numeric_set_ready"]})
    with files[0].open("w", encoding="utf-8-sig", newline="") as handle, ColumnWriter(handle) as writer:
        for row in table:
            writer.writerow(row)
    report = {"scope": "matched_export_group_and_value_frequency_comparison", "study": study,
        "cohort_equivalence": "operator_declared_not_independently_proven", "comparisons": comparisons,
        "limitations": ["No patient/event linkage is reconstructed from distinct exports",
                        "A missing semantic signature may reflect changed qualifiers/references rather than a lost observation",
                        "Summary-only, invalid, missing or censored values cannot prove exact value congruence",
                        "Numeric frequency agreement does not prove patient/event correspondence"]}
    files[1].write_text(json.dumps(report, indent=2) + "\n")
    files[2].write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Export comparison</title>'
        '<style>body{font:16px/1.6 system-ui;color:#142d42;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap}</style>'
        '<h1>LIS → FHIR → CDS Hub export comparison</h1><p>Matched cohort/period is declared by the operator. This compares semantic groups and available value frequencies.</p>'
        f'<p><a href="{escape(files[0].name,quote=True)}">Detailed comparison CSV</a></p><pre>{escape(json.dumps(report,indent=2))}</pre></html>')
    return report


def verify_audit(path):
    import hashlib
    from .audit import encoded
    path = Path(path)
    previous, count, final = "", 0, None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            digest = record.pop("sha256")
            count += 1
            if record.get("sequence") != count or record.get("previous_sha256") != previous or hashlib.sha256(encoded(record).encode()).hexdigest() != digest:
                raise ValueError("Audit hash chain verification failed")
            previous, final = digest, record
    if not final or final["phase"] != "completed":
        raise ValueError("Audit does not describe a completed run")
    for filename, expected in final["details"]["output_hashes"].items():
        if Path(filename).name != filename:
            raise ValueError("Invalid audit artifact filename")
        actual = hashlib.sha256((path.parent / filename).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("An output artifact differs from the completed run")
    return {"status": "verified", "events": count, "scope": "integrity_not_authentication"}
