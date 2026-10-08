"""Exercise the real DuckDB grouping and laboratory checks in focused regressions."""

from collections import Counter
from pathlib import Path
import tempfile

from semantic_plausibility.export_grouping import ExportGroups


def assess_groups(records, checker, *, mode="tuples"):
    with tempfile.TemporaryDirectory() as folder:
        groups = ExportGroups(
            Path(folder), mode="items" if mode == "resources" else "distinct", group_value=False
        )
        try:
            accounting = groups.ingest(records)
            rows, evidence = [], []
            for key, payload, stats, metadata in groups.groups():
                row, detail = checker.assess(payload, stats)
                row.update(
                    tupel_id=key,
                    ressource_id=metadata["source_id"],
                    messpfad=metadata["item_path"] or payload["path"],
                )
                rows.append({name: "" if value is None else str(value) for name, value in row.items()})
                evidence.append({"tuple_id": key, "metadata": payload, "observed_values": stats, **detail})
            summary = {
                "input_records": accounting["individual_records"] + accounting["distinct_records"],
                "assessed_items": len(rows),
                "statuses": dict(Counter(row["status"] for row in rows)),
                "full_fhir_validation": "out_of_scope",
            }
            return summary, rows, evidence, []
        finally:
            groups.close()
