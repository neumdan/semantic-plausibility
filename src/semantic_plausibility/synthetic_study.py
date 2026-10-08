"""Synthetic LIS/FHIR/database exports and repeatable study examples; no clinical data."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

LOCAL = "urn:example:lis-tests"
UCUM = "http://unitsofmeasure.org"
LOINC = "http://loinc.org"
HEADER = ["code", "code_system", "value", "unit", "unit_code", "unit_system", "count", "reference_ranges_json", "specimen", "source_test", "status"]


def data_rows():
    def row(code, value, unit, count, low, high, label, *, system=LOINC):
        references = [{"low": {"value": low, "code": unit if unit != "10*6" else "10*3/uL", "system": UCUM},
                       "high": {"value": high, "code": unit if unit != "10*6" else "10*3/uL", "system": UCUM},
                       "text": "SYNTHETIC interval, not clinical evidence"}] if low is not None else []
        return dict(zip(HEADER, [code, system, value, unit, unit, UCUM, count,
            json.dumps(references, separators=(",", ":")), "Bld", label, "final"], strict=True))
    return [row("718-7", "14", "g/dL", 40, 12, 16, "Hemoglobin"),
            row("718-7", "18.5", "g/dL", 5, 12, 16, "Hemoglobin"),
            row("718-7", "140", "g/L", 10, 120, 160, "Hemoglobin"),
            row("6690-2", "5.6", "10*3/uL", 20, 4, 10, "Leukocytes"),
            row("6690-2", "5.6", "10*6", 3, 4, 10, "Leukocytes"),
            row("718-7", "not-a-number", "g/dL", 1, 11, 15, "Hemoglobin"),
            row("718-7", "14", "g/dL", 2, None, None, "Hemoglobin"),
            row("LIS-HB", "14", "g/dL", 3, 12, 16, "Hemoglobin", system=LOCAL)]


def create_examples(folder: Path, snapshot: Path):
    folder.mkdir(parents=True, exist_ok=True)
    rows = data_rows()
    with (folder / "lis.distinct.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)
    evidence = json.loads(snapshot.read_text())
    evidence["ucum"]["10*6"] = True
    (folder / "reference-pack.synthetic.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (folder / "local-codes.csv").write_text("system,version,code,display\nurn:example:lis-tests,,LIS-HB,Hemoglobin\n")
    concept_map = {"resourceType": "ConceptMap", "url": "urn:example:lis-to-loinc", "version": "synthetic-1",
        "status": "draft", "group": [{"source": LOCAL, "target": LOINC, "element": [{"code": "LIS-HB", "target": [{"code": "718-7", "equivalence": "equivalent"}]}]}]}
    (folder / "conceptmap.synthetic.json").write_text(json.dumps(concept_map, indent=2) + "\n")
    observations = []
    for index, row in enumerate(rows):
        for repetition in range(row["count"]):
            try:
                value = float(row["value"])
            except ValueError:
                value = row["value"]  # Deliberately malformed FHIR value, for the negative example.
            observations.append({"resourceType": "Observation", "id": f"synthetic-{index}-{repetition}", "status": row["status"],
                "code": {"coding": [{"system": row["code_system"], "code": row["code"]}]},
                "valueQuantity": {"value": value, "unit": row["unit"], "code": row["unit_code"], "system": row["unit_system"]},
                "referenceRange": json.loads(row["reference_ranges_json"])})
    (folder / "fhir.bundle.json").write_text(json.dumps({"resourceType": "Bundle", "type": "collection",
        "entry": [{"resource": resource} for resource in observations]}, indent=2) + "\n")
    (folder / "lis.manifest.json").write_text(json.dumps({"exported_rows": len(rows), "source_occurrences": sum(r["count"] for r in rows),
        "description": "SYNTHETIC matched cohort; no clinical reference intervals"}, indent=2) + "\n")
    (folder / "fhir.manifest.json").write_text(json.dumps({"exported_rows": len(observations)}, indent=2) + "\n")
    return rows


def create_hub_database(path: Path, rows):
    """Portable SQLite stand-in for the site's mapped PostgreSQL lab view."""
    changed = [dict(row) for row in rows]
    changed[3]["count"] -= 1  # Known frequency loss.
    for row in changed[:2]:
        reference = json.loads(row["reference_ranges_json"])
        reference[0]["high"]["value"] = 17  # Deliberate reference information change.
        row["reference_ranges_json"] = json.dumps(reference, separators=(",", ":"))
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE lab (" + ",".join('"' + name + '" TEXT' for name in HEADER) + ")")
        db.executemany("INSERT INTO lab VALUES (" + ",".join("?" for _ in HEADER) + ")",
                       [[row[name] for name in HEADER] for row in changed])
    db.close()


def config_text(folder: Path, *, kind="csv", source="lis", output: Path, items=False):
    path = folder / ("lis.distinct.csv" if kind == "csv" else "fhir.bundle.json" if kind == "fhir_file" else "hub.sqlite")
    result = f'''config_version=3
[general]
PROJECT_NAME="synthetic-laboratory-study"
RESOURCE_TYPE="Observation"
[input]
type={json.dumps(kind)}
origin={json.dumps(source)}
source_namespace="synthetic-site"
path={json.dumps(str(path))}
snapshot={json.dumps(str(folder / 'reference-pack.synthetic.json'))}
granularity={json.dumps('individual' if kind == 'fhir_file' else 'distinct')}
'''
    if kind != "fhir_file":
        result += '[input.columns]\n' + "\n".join(name + "=" + json.dumps(name) for name in HEADER) + "\n"
    else:
        result += '[input.context]\nloinc_system="Bld"\nmethod=""\nsource_test=""\n'
    if kind == "sqlite":
        result += '[database]\nDB_SCHEMA="main"\nDB_TABLE="lab"\n'
    result += f'''[specification]
FHIR_VERSION="4.0.1"
PROFILE="fhir-r4-lab"
[aggregation]
engine="duckdb"
group_by=["code","code_system","unit","unit_system","reference_range"]
[network]
mode="offline"
[lookups.local]
enabled=true
type="csv"
systems=["{LOCAL}"]
path={json.dumps(str(folder / 'local-codes.csv'))}
complete=false
[quality]
concept_map={json.dumps(str(folder / 'conceptmap.synthetic.json'))}
[study]
cohort="synthetic-cohort-v1"
period_start="2026-01-01"
period_end="2026-01-31"
source_label={json.dumps(source)}
[output]
mode={json.dumps('items' if items else 'distinct')}
path={json.dumps(str(output))}
include_evidence=true
include_distribution=true
'''
    return result
