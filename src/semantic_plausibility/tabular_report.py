"""Scalar CSV projection; complete review objects stay in the audited evidence JSONL."""

import csv
import json
import re
import tempfile

from .support import _csv_text


def flatten(value, prefix, result):
    """One leaf per column; one-based positions preserve every repeated FHIR element."""
    if isinstance(value, dict):
        for key, child in value.items():
            # Escape separators so a source key cannot collide with a nested path.
            key = str(key).replace("~", "~0").replace(".", "~1")
            flatten(child, f"{prefix}.{key}", result)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value, 1):
            flatten(child, f"{prefix}.{index}", result)
    else:
        result[prefix] = "" if value is None else value


def project(row, detail, *, operation="check", summary=None):
    """Keep scalar summary/review columns and the same field decisions as the HTML."""
    result = {key: value for key, value in row.items()
              if not key.endswith("_json") and not key.startswith(("assessment.", "qc."))
              and not isinstance(value, (dict, list))}
    if row.get("resource_type") == "Observation":
        from .laboratory_assessment import assessment_layers
        from .laboratory_report_table import laboratory_fields
        source = json.loads(row.get("source_data_json") or "{}")
        if "source_id" not in row:
            source = {key: val for key, val in source.items() if key not in {"source_id", "source_ref"}}
        if operation == "research-evidence" and summary is None:
            raise ValueError("Research CSV projection requires the same summary as the HTML report")
        fields = laboratory_fields(row, summary or {"operation": operation})
        layers = assessment_layers(row, summary or {"operation": operation})
        flatten(layers, "qc", result)
        transform = json.loads(row.get("transformation_json") or "{}")
        flatten(transform, "transformation", result)
        if row.get("study_comparison_json"):
            comparison = json.loads(row["study_comparison_json"])
            # Detailed comparisons remain in the audit; scalar columns expose each value.
            flatten(comparison, "evidence_comparison", result)
    else:
        from .medication_report_table import medication_fields
        source = detail.get("source_set", {})
        fields = medication_fields(row, detail)
    flatten(source, "input", result)
    if row.get("input_decision_json"):
        decision = json.loads(row["input_decision_json"])
        flatten({key: decision[key] for key in ("state", "model", "revision", "declared", "min_probability", "calibration", "decisions")
                 if key in decision}, "decision", result)
    for field in fields:
        name = re.sub(r"[^a-z0-9]+", "_", field.name.lower()).strip("_")
        prefix = "assessment." + name
        sources = []
        for record in field.sources:
            source = {key: record[key] for key in ("source", "version", "title", "excerpt", "source_location", "source_sha256")
                      if record.get(key) not in (None, "") and not isinstance(record[key], (dict, list))}
            if source and source not in sources:
                sources.append(source)
        flatten({"finding": field.findings, "action": field.action,
                 "recommendation": field.recommendation, "reason": field.reason,
                 "condition": field.condition, "support_level": field.support_level,
                 "source_status": field.source_state, "proposal_status": field.proposal_state,
                 "source": sources}, prefix, result)
    return result


class ColumnWriter:
    """Spool rows locally, collect union headers, then write CSV with bounded row memory."""

    def __init__(self, handle):
        self.handle = handle
        self.spool = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.columns = {}

    def __enter__(self):
        return self

    def writerow(self, row):
        for key, value in row.items():
            if isinstance(value, (dict, list, tuple)):
                raise ValueError("CSV projection requires one scalar value per cell")
            self.columns.setdefault(key, None)
        # A malformed input must not silently generate a spreadsheet with lost columns.
        if len(self.columns) > 16384:
            raise ValueError("CSV exceeds 16384 columns; select a smaller semantic export")
        self.spool.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                writer = csv.DictWriter(self.handle, fieldnames=list(self.columns))
                writer.writeheader()
                self.spool.seek(0)
                for line in self.spool:
                    row = json.loads(line)
                    writer.writerow({key: _csv_text(val) if isinstance(val, str) else val
                                     for key, val in row.items()})
        finally:
            self.spool.close()
