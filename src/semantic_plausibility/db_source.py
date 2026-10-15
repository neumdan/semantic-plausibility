# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Read-only PostgreSQL source adapter for typed custom LIS tables.

Only identifiers from a local mapping file become SQL identifiers. All filters are
parameters. The resource builder is deliberately narrow so a flattened row never
silently claims an unobserved FHIR element.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import partial
from pathlib import Path
import re
from typing import Any, Iterator

from .batch_quality import CsvMapping, observation_from_row


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _identifier_parts(name: str, *, relation: bool = False) -> tuple[str, ...]:
    parts = tuple(name.split("."))
    if len(parts) > (2 if relation else 1) or any(not IDENTIFIER.fullmatch(p) for p in parts):
        raise ValueError("Mapping contains an invalid SQL identifier")
    return parts


def load_mapping(path: Path) -> dict[str, Any]:
    return validate_mapping(json.loads(path.read_text(encoding="utf-8")))


def sqlite_columns_present(connection, mapping):
    """Reject unknown columns before SQLite treats quoted names as string literals."""
    relation = _identifier_parts(mapping['relation'], relation=True)
    schema = '"' + relation[0] + '".' if len(relation) == 2 else ''
    actual = {row[1] for row in connection.execute('PRAGMA ' + schema + 'table_info("' + relation[-1] + '")')}
    if not set(mapping['fields'].values()) <= actual:
        raise ValueError('SQLite table is missing one or more mapped columns')
    return True


def validate_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping, dict) or mapping.get("resource_type") not in {"Observation", "Medication", "MedicationRequest", "MedicationAdministration", "MedicationStatement"}:
        raise ValueError("DB mapping needs a supported resource_type")
    if mapping.get("format") not in {None, "columns"}:
        raise ValueError("DB mapping uses laboratory columns or resource_json, not generic FHIR paths")
    if mapping.get("input_granularity", "auto") not in {"auto", "individual", "distinct"}:
        raise ValueError("input_granularity must be auto, individual or distinct")
    _identifier_parts(str(mapping.get("relation") or ""), relation=True)
    fields = mapping.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("DB mapping needs logical fields to SQL columns")
    for column in fields.values():
        _identifier_parts(str(column))
    if mapping.get("resource_type_column"):
        _identifier_parts(str(mapping["resource_type_column"]))
    if mapping.get("since_column"):
        _identifier_parts(str(mapping["since_column"]))
    if (
        mapping.get("resource_type") == "Observation"
        and "resource_json" not in fields
        and not {"code", "unit", "code_system"} <= (set(fields) | set(mapping.get("defaults") or {}))
    ):
        raise ValueError("Observation mapping needs code, unit and code_system")
    return mapping


def record_from_row(row: dict[str, Any], mapping: dict, source_ref: str, context: dict | None = None):
    """One frequency contract for mapped LIS and CDS Hub laboratory views."""
    from .audit import SourceRecord
    from .fhir_quality_pipeline import number

    from .aggregate_statistics import ROLES, supplied_statistics
    resource, local_context = resource_from_row(row, mapping)
    raw_count = row.get("count")
    count = number(1 if raw_count is None else raw_count)
    if count is None or count < 1 or count != count.to_integral_value():
        raise ValueError("DB count must be a positive integer")
    granularity = mapping.get("input_granularity", "auto")
    if granularity == "auto":
        granularity = (
            "distinct"
            if {"count", "value_min", "value_max", "value_mean", "value_sd"} & (set(mapping["fields"]) | set(row))
            else "individual"
        )
    return SourceRecord(
        resource,
        {**local_context, **(context or {})},
        source_ref,
        int(count),
        row.get("value_min"),
        row.get("value_max"),
        bool(set(ROLES) & row.keys()),
        granularity,
        granularity == "individual" or raw_count is not None,
        supplied_statistics(row, resource.get("valueQuantity", {})) if set(ROLES) & row.keys() else {},
    )


def build_query(mapping: dict[str, Any], *, since: str = "", loinc_only: bool = False):
    """Build psycopg SQL with quoted identifiers and bound filter values."""
    from psycopg import sql

    fields = mapping["fields"]
    columns = sql.SQL(", ").join(
        sql.SQL("{} AS {}").format(sql.Identifier(column), sql.Identifier(logical))
        for logical, column in fields.items()
    )
    relation = sql.Identifier(*_identifier_parts(mapping["relation"], relation=True))
    predicates = []
    params: list[str] = []
    if mapping.get("resource_type_column"):
        predicates.append(sql.SQL("{} = %s").format(sql.Identifier(mapping["resource_type_column"])))
        params.append(mapping["resource_type"])
    if since:
        column = mapping.get("since_column")
        if not column:
            raise ValueError("Mapping has no since_column")
        predicates.append(sql.SQL("{} >= %s").format(sql.Identifier(column)))
        params.append(since)
    if loinc_only:
        if mapping["resource_type"] != "Observation" or "code_system" not in fields:
            raise ValueError("--loinc-only requires an Observation code_system column")
        predicates.append(sql.SQL("{} = %s").format(sql.Identifier(fields["code_system"])))
        params.append("http://loinc.org")
    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(predicates) if predicates else sql.SQL("")
    return sql.SQL("SELECT {} FROM {}{}").format(columns, relation, where), params


def resource_from_row(row: dict[str, Any], mapping: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    kind = mapping["resource_type"]
    if "resource_json" in row:
        payload = row["resource_json"]
        resource = json.loads(payload, parse_float=Decimal) if isinstance(payload, str) else payload
        if not isinstance(resource, dict) or resource.get("resourceType") != kind:
            raise ValueError("JSONB source has the wrong FHIR resource type")
        from .export_review import encoding_issues
        resource = dict(resource)
        resource["_plausibility_input_issues"] = encoding_issues(resource)
        defaults = mapping.get("defaults") or {}
        context = {
            key: str(row.get(key) or defaults.get(key) or "")
            for key in ("source_test", "loinc_system", "method")
        }
        axes = {name: str(row.get(role) or defaults.get(role) or "") for name, role in (
            ("component", "loinc_component"), ("property", "loinc_property"),
            ("time", "loinc_time_aspect"), ("scale", "loinc_scale")) if row.get(role) or defaults.get(role)}
        if axes:
            context["source_axes"] = axes
        return resource, context
    if kind == "Observation":
        csv_mapping = CsvMapping(
            mapping.get("profile") or "fhir-r4-lab",
            {key: key for key in mapping["fields"]},
            mapping.get("defaults") or {},
        )
        values = {
            key: ""
            if value is None
            else json.dumps(value, default=str)
            if isinstance(value, (dict, list))
            else str(value)
            for key, value in row.items()
        }
        resource, context = observation_from_row(values, csv_mapping, with_values=True, allow_incomplete=True)
        if row.get("id") is not None:
            resource["id"] = str(row["id"])
        return resource, context
    raise ValueError("Only laboratory Observations are supported")


def scan(
    mapping: dict[str, Any], dsn: str, *, since: str = "", loinc_only: bool = False, batch_size: int = 500
) -> Iterator[dict[str, Any]]:
    if not 1 <= batch_size <= 5000:
        raise ValueError("batch_size must be 1..5000")
    import psycopg
    from psycopg.rows import dict_row

    query, params = build_query(mapping, since=since, loinc_only=loinc_only)
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        from psycopg.types.json import set_json_loads

        set_json_loads(partial(json.loads, parse_float=Decimal), context=connection)
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor(name="semantic_plausibility_scan") as cursor:
            cursor.execute(query, params)
            while rows := cursor.fetchmany(batch_size):
                yield from rows
