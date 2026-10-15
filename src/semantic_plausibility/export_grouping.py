"""Bounded-memory staging, bulk DuckDB grouping and frequency-aware statistics.

Input values are preserved as decimal strings. A histogram is never expanded
into repeated observations. Summary-only inputs do not acquire invented means.
"""
from __future__ import annotations

import csv
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

import duckdb

from .audit import canonical, encoded, semantic_items
from .fhir_quality_pipeline import number, serial
from .aggregate_statistics import pool_summaries

COLS = ["key", "payload", "value_key", "source_id", "source_ref", "item_path", "weight", "known",
        "aggregate_input", "distinct_input", "minimum", "maximum", "point", "numeric", "missing",
        "invalid", "censored", "summary"]


def weighted_statistics(distribution: list[tuple[str, int]]) -> dict:
    if not distribution:
        return {name: None for name in ("mean", "sd", "se", "median", "q1", "q3", "iqr")}
    values = sorted((Decimal(value), int(count)) for value, count in distribution)
    n = sum(count for _, count in values)
    with localcontext() as context:
        context.prec = 38
        mean = sum(value * count for value, count in values) / n
        variance = sum(count * (value - mean) ** 2 for value, count in values) / (n - 1) if n > 1 else None
        sd = variance.sqrt() if variance is not None else None

        def at_rank(rank: int) -> Decimal:
            cumulative = 0
            for value, count in values:
                cumulative += count
                if rank < cumulative:
                    return value
            raise ValueError("Histogram count mismatch")

        def quantile(q: str) -> Decimal:
            rank = Decimal(q) * (n - 1)
            low = int(rank)
            fraction = rank - low
            return at_rank(low) + fraction * (at_rank(low + 1) - at_rank(low)) if fraction else at_rank(low)

        median, q1, q3 = quantile("0.5"), quantile("0.25"), quantile("0.75")
        return {key: serial(value) for key, value in {
            "mean": mean, "sd": sd, "se": sd / Decimal(n).sqrt() if sd is not None else None,
            "median": median, "q1": q1, "q3": q3, "iqr": q3 - q1}.items()}


class ExportGroups:
    def __init__(self, folder: Path, *, mode: str, group_value: bool):
        self.folder, self.mode, self.group_value = folder, mode, group_value
        self.db = duckdb.connect(str(folder / "groups.duckdb"), config={
            "allow_unsigned_extensions": False, "autoinstall_known_extensions": False,
            "autoload_known_extensions": False, "threads": 2, "memory_limit": "512MB"})
        self.accounting = {"individual_records": 0, "distinct_records": 0, "known_source_occurrences": 0,
                           "unknown_frequency_records": 0, "semantic_items_seen": 0}

    def close(self):
        self.db.close()

    def ingest(self, records):
        staging = self.folder / "staging.csv"
        with staging.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(COLS)
            for index, record in enumerate(records, 1):
                if type(record.occurrences) is not int or not 1 <= record.occurrences < 2**63:
                    raise ValueError("Source counts must be positive signed 64-bit integers")
                granularity = record.input_granularity
                if granularity == "auto":
                    granularity = "distinct" if record.aggregate_values or record.occurrences != 1 else "individual"
                if granularity not in {"individual", "distinct"} or type(record.occurrences_known) is not bool:
                    raise ValueError("Unknown input frequency contract")
                if granularity == "individual" and (record.aggregate_values or record.occurrences != 1 or not record.occurrences_known):
                    raise ValueError("Individual rows cannot supply aggregate counts/bounds")
                if self.mode == "items" and granularity != "individual":
                    raise ValueError("Distinct input cannot recover individual source IDs")
                if self.group_value and record.aggregate_values:
                    raise ValueError("Exact value grouping cannot use min/max-only input")
                self.accounting[granularity + "_records"] += 1
                self.accounting["known_source_occurrences"] += record.occurrences if record.occurrences_known else 0
                self.accounting["unknown_frequency_records"] += not record.occurrences_known
                for payload, value, path in semantic_items(record, preserve_order=self.mode == "items"):
                    self.accounting["semantic_items_seen"] += 1
                    payload = canonical(payload, preserve_lists=self.mode == "items")
                    if self.mode == "items":
                        payload["path"] = path
                    text = encoded(payload)
                    value_key = canonical({"value": value})["value"] if self.group_value or self.mode == "items" else None
                    identity = [text, value_key] if self.group_value else text
                    if self.mode == "items":
                        identity = [index, record.source_ref, path, text]
                    key = hashlib.sha256(encoded(identity).encode()).hexdigest()[:24]
                    raw = [record.value_min, record.value_max] if record.aggregate_values else [value]
                    present = [v for v in raw if v is not None and v != ""]
                    numbers = [number(v) for v in present]
                    invalid = any(v is None for v in numbers) or record.aggregate_values and len(numbers) == 2 and numbers[0] > numbers[1]
                    censored = bool(payload["quantity"].get("comparator"))
                    valid_numbers = [] if invalid else [v for v in numbers if v is not None]
                    low, high = (min(valid_numbers), max(valid_numbers)) if valid_numbers else (None, None)
                    point = (canonical({"value": serial(valid_numbers[0])})["value"] if valid_numbers and not record.aggregate_values and not censored
                             and len(payload.get("source_value_types", [])) <= 1 and not payload.get("has_data_absent_reason") else None)
                    writer.writerow([key, text, encoded(value_key), str(record.resource.get("id") or "") if self.mode == "items" else "",
                        record.source_ref if self.mode == "items" else "", path if self.mode == "items" else "",
                        record.occurrences, int(record.occurrences_known), int(record.aggregate_values), int(granularity == "distinct"),
                        serial(low) if not censored else None, serial(high) if not censored else None, point,
                        len(valid_numbers), len(raw) - len(present), len(present) if invalid else 0, int(censored),
                        encoded({**record.supplied_statistics, "n": record.occurrences if record.occurrences_known else None}) if record.aggregate_values else ""])
        # External access is disabled after this single application-owned file read.
        if not self.accounting["semantic_items_seen"]:
            raise ValueError("Input contains no selected laboratory measurements")
        self.db.execute("CREATE TABLE stage AS SELECT * FROM read_csv(?, header=true, all_varchar=true, nullstr='')", [str(staging)])
        self.db.execute("SET enable_external_access=false")
        self.db.execute("""CREATE TABLE groups AS SELECT key, first(payload) AS payload, first(value_key) AS value_key,
          first(source_id) AS source_id, first(source_ref) AS source_ref, first(item_path) AS item_path,
          count(*) AS n_rows, sum(CAST(weight AS BIGINT)*CAST(known AS BIGINT)) AS known_occurrences,
          sum(1-CAST(known AS BIGINT)) AS unknown_frequency_inputs,
          sum(CAST(aggregate_input AS BIGINT)) AS aggregate_inputs,
          sum(1-CAST(aggregate_input AS BIGINT)) AS individual_inputs,
          sum(CAST(distinct_input AS BIGINT)) AS distinct_inputs,
          sum(CAST(numeric AS BIGINT)) AS numeric,
          sum(CAST(missing AS BIGINT)) AS missing,
          sum(CAST(invalid AS BIGINT)) AS invalid, sum(CAST(censored AS BIGINT)) AS censored
          FROM stage GROUP BY key""")
        self.db.execute("""CREATE TABLE distribution AS SELECT key, point, sum(CAST(weight AS BIGINT)) AS frequency
          FROM stage WHERE point IS NOT NULL AND known='1' GROUP BY key,point""")
        self.db.execute("CREATE INDEX stage_key ON stage(key)")
        self.db.execute("CREATE INDEX distribution_key ON distribution(key)")
        staging.unlink()
        return self.accounting

    def groups(self):
        cursor = self.db.cursor()
        try:
            cursor.execute("SELECT * FROM groups ORDER BY key")
            names = [x[0] for x in cursor.description]
            while batch := cursor.fetchmany(256):
                for values in batch:
                    row = dict(zip(names, values, strict=True))
                    key = row.pop("key")
                    payload = json.loads(row.pop("payload"))
                    value_key = json.loads(row.pop("value_key"))
                    metadata = {name: row.pop(name) or "" for name in ("source_id", "source_ref", "item_path")}
                    minima, maxima = [], []
                    range_cursor = self.db.cursor()
                    try:
                        range_cursor.execute("SELECT minimum,maximum FROM stage WHERE key=?", [key])
                        while interval_batch := range_cursor.fetchmany(1024):
                            for low, high in interval_batch:
                                if low is not None:
                                    number_low = Decimal(low)
                                    minima = [min(minima[0], number_low)] if minima else [number_low]
                                if high is not None:
                                    number_high = Decimal(high)
                                    maxima = [max(maxima[0], number_high)] if maxima else [number_high]
                    finally:
                        range_cursor.close()
                    row.update(minimum=serial(min(minima)) if minima else None, maximum=serial(max(maxima)) if maxima else None,
                        occurrences=None if row["unknown_frequency_inputs"] else row["known_occurrences"],
                        group_value=value_key)
                    distribution = sorted([(point, int(count)) for point, count in self.db.execute(
                        "SELECT point,frequency FROM distribution WHERE key=?", [key]).fetchall()], key=lambda x: Decimal(x[0]))
                    n = sum(count for _, count in distribution)
                    available = not (row["unknown_frequency_inputs"] or row["aggregate_inputs"] or row["censored"])
                    row.update(n_numeric=n if available else None, distribution=distribution if available else [],
                        statistics_availability="point_values_with_known_frequency" if available else
                            "bounds_only" if row["aggregate_inputs"] else "unknown_frequency" if row["unknown_frequency_inputs"] else "censored_values")
                    row.update(weighted_statistics(distribution if available else []))
                    if row["aggregate_inputs"]:
                        if row["individual_inputs"]:
                            raise ValueError("Do not mix point records and summary aggregates for the same semantic set")
                        summaries = [json.loads(value) for (value,) in self.db.execute("SELECT summary FROM stage WHERE key=?", [key]).fetchall()]
                        row.update(pool_summaries(summaries))
                        row["supplied_statistics"] = summaries
                        if row["mean"] is not None or row["sd"] is not None:
                            row["statistics_availability"] = "supplied_summary" if len(summaries)==1 else "pooled_disjoint_sample_summaries"
                            row["n_numeric"] = row["occurrences"]
                    row["value_distribution_sha256"] = hashlib.sha256(encoded(row["distribution"]).encode()).hexdigest() if available and not row["invalid"] and not row["missing"] else None
                    yield key, payload, row, metadata
        finally:
            cursor.close()
