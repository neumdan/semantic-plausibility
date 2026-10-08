# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Registry-wide adapter tests and opt-in live terminology checks.

Synthetic adapter coverage does not prove membership in a real terminology.
Only codes/units, never patient data, are sent by the live checks.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ..observation_services import LoincFhirClient, NlmUcumClient
from ..profile_config import LOINC, PROFILES, UCUM
from ..terminology_router import (
    CsvCodeSource,
    FhirCodeSource,
    SqliteCodeSource,
    TerminologyRouter,
    load_sources,
)

ROOT = Path(__file__).resolve().parent
INVALID_CODE = "__SEMANTIC_PLAUSIBILITY_INVALID_CODE__"


@dataclass
class RegisteredSystem:
    system: str
    label: str
    usage: str
    origins: set[str]


def load_registry(
    generated: Path = ROOT / "generated", *, source_entries: list[dict[str, Any]] | None = None
) -> list[RegisteredSystem]:
    entries: dict[str, RegisteredSystem] = {}

    def add(system: str, label: str, origin: str) -> None:
        parsed = urlparse(system)
        if parsed.scheme not in {"http", "https", "urn"} or not parsed.path and not parsed.netloc:
            raise ValueError(f"Registry contains an invalid canonical URI: {system!r}")
        if system not in entries:
            entries[system] = RegisteredSystem(system, label, "coded_element", set())
        if label:
            entries[system].label = label
        entries[system].origins.add(origin)

    with (generated / "systems.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            add(row["system"], row["label"], "kds_inventory")
    services = json.loads((ROOT / "services.json").read_text(encoding="utf-8"))
    for row in services["systems"]:
        add(row["canonical"], row["name"], "services.json")
    for profile in PROFILES.values():
        for rule in profile.element_rules.values():
            for system in (
                *tuple(rule.allowed_systems or ()),
                *rule.system_bindings,
                *([rule.required_coding_system] if rule.required_coding_system else []),
            ):
                add(system, "", "profile_policy")
    for source in source_entries or []:
        for system in source.get("systems") or []:
            add(system, "", "configured_source")
    fixed_paths: dict[str, list[str]] = {}
    with (generated / "fixed_systems.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            fixed_paths.setdefault(row["system"], []).append(row["element"])
    # Identifier namespaces are present in the IG inventory but are not finite
    # CodeSystems. Do not submit identifier membership to a terminology server.
    for entry in entries.values():
        if entry.system == UCUM:
            entry.usage = "unit"
        elif re.search(r"/(?:sid|NamingSystem)/", entry.system) or (
            fixed_paths.get(entry.system)
            and all(
                re.search(r"(?:^Identifier|\.(?:identifier|masterIdentifier)(?::[^.]+)?)\.system$", path)
                for path in fixed_paths[entry.system]
            )
        ):
            entry.usage = "identifier_namespace"
    return sorted(entries.values(), key=lambda entry: entry.system)


def offline_checks(registry: list[RegisteredSystem]) -> list[dict[str, Any]]:
    """Exercise real CSV/SQLite readers and routing with isolated test content."""
    rows = []
    with tempfile.TemporaryDirectory(prefix="interpolar-tx-check-") as directory:
        catalog = Path(directory) / "synthetic.csv"
        db_path = Path(directory) / "synthetic.sqlite"
        codes = [f"SYNTHETIC_{index:04d}" for index in range(len(registry))]
        with catalog.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["system", "code", "version", "display"])
            writer.writerows(
                (entry.system, codes[index], version, f"SYNTHETIC {version}")
                for index, entry in enumerate(registry)
                for version in ("v1", "v2")
            )
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("CREATE TABLE codes(system TEXT,code TEXT,version TEXT,display TEXT)")
            db.executemany(
                "INSERT INTO codes VALUES(?,?,?,?)",
                [
                    (entry.system, codes[index], version, f"SYNTHETIC {version}")
                    for index, entry in enumerate(registry)
                    for version in ("v1", "v2")
                ],
            )
        systems = [entry.system for entry in registry]
        csv_router = TerminologyRouter(
            [{"kind": "csv", "path": str(catalog), "systems": systems, "complete": True}]
        )
        sqlite_router = TerminologyRouter(
            [{"kind": "sqlite", "path": str(db_path), "systems": systems, "complete": True}]
        )
        partial = CsvCodeSource(catalog)
        absent = TerminologyRouter([])
        try:
            for index, entry in enumerate(registry):
                checks = {}
                for name, router in (("csv", csv_router), ("sqlite", sqlite_router)):
                    exact = router.check(entry.system, codes[index], "v1")
                    second = router.check(entry.system, codes[index], "v2")
                    checks[f"{name}_exact"] = (
                        exact["state"] == "valid" and exact["metadata"]["version"] == "v1"
                    )
                    checks[f"{name}_version"] = (
                        second["state"] == "valid" and second["metadata"]["version"] == "v2"
                    )
                    checks[f"{name}_negative"] = (
                        router.check(entry.system, INVALID_CODE, "v1")["state"] == "invalid"
                    )
                    foreign = codes[(index + 1) % len(codes)] if len(codes) > 1 else "FOREIGN"
                    checks[f"{name}_system_isolation"] = (
                        router.check(entry.system, foreign, "v1")["state"] == "invalid"
                    )
                    checks[f"{name}_ambiguous_version"] = (
                        router.check(entry.system, codes[index])["state"] == "unknown"
                    )
                checks["partial_absence_unknown"] = (
                    partial.check(entry.system, INVALID_CODE)["state"] == "unknown"
                )
                checks["no_service_unknown"] = absent.check(entry.system, codes[index])["state"] == "unknown"
                rows.append(
                    {
                        "system": entry.system,
                        "label": entry.label,
                        "usage": entry.usage,
                        "origins": sorted(entry.origins),
                        "offline_contract": "passed" if all(checks.values()) else "failed",
                        "synthetic_checks": checks,
                        "live_status": "not_checked",
                        "positive": None,
                        "negative": None,
                        "errors": [],
                    }
                )
        finally:
            sqlite_router.close()
            csv_router.close()
    return rows


def load_environment(path: Path) -> None:
    """Read explicit service keys only. No shell evaluation or variable expansion."""
    allowed = {
        "TX_FHIR_BASE_URL",
        "TX_FHIR_BEARER_TOKEN",
        "LOINC_FHIR_BASE_URL",
        "LOINC_FHIR_USERNAME",
        "LOINC_FHIR_PASSWORD",
        "UCUM_NLM_BASE_URL",
        "SQC_TERMINOLOGY_SOURCES",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.strip().removeprefix("export ").split("=", 1)
        if key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def error_label(exc: Exception) -> str:
    # Exception URLs can include bearer query parameters or service userinfo.
    # The review table needs the failure class/status, not credentials.
    return f"HTTP_{exc.code}" if isinstance(exc, HTTPError) else type(exc).__name__


def first_concept(resource: dict[str, Any]) -> str | None:
    def visit(concepts: list[dict[str, Any]]) -> str | None:
        for concept in concepts:
            unusable = any(
                p.get("code") in {"notSelectable", "inactive"} and p.get("valueBoolean") is True
                for p in concept.get("property") or []
            )
            if concept.get("code") and not unusable:
                return str(concept["code"])
            nested = visit(concept.get("concept") or [])
            if nested:
                return nested
        return None

    return visit(resource.get("concept") or [])


class LiveChecks:
    def __init__(self, registry: list[RegisteredSystem], entries: list[dict[str, Any]], *, timeout: int = 10):
        effective = list(entries)
        tx = os.getenv("TX_FHIR_BASE_URL", "")
        if tx:
            effective.append(
                {
                    "kind": "fhir",
                    "base_url": tx,
                    "token_env": "TX_FHIR_BEARER_TOKEN",
                    "systems": [e.system for e in registry if e.usage != "identifier_namespace"],
                }
            )
        self.router = TerminologyRouter(effective)
        self.timeout = timeout
        for _, source, _ in self.router.sources:
            if isinstance(source, FhirCodeSource):
                source.client.timeout = timeout
        self.ucum = NlmUcumClient(
            os.getenv("UCUM_NLM_BASE_URL", "https://ucum.nlm.nih.gov/ucum-service/v1"), timeout=timeout
        )
        self.loinc = LoincFhirClient(
            os.getenv("LOINC_FHIR_BASE_URL", "https://fhir.loinc.org"),
            os.getenv("LOINC_FHIR_USERNAME", ""),
            os.getenv("LOINC_FHIR_PASSWORD", ""),
            timeout=timeout,
        )
        self.examples = {
            row["system"]: {**row, "version": "", "origin": "published_example"}
            for row in json.loads((ROOT / "test_cases.json").read_text())["code_examples"]
        }

    def close(self) -> None:
        self.router.close()

    def discover(self, system: str) -> tuple[dict[str, Any] | None, list[str]]:
        errors = []
        for systems, source, website in self.router.sources:
            if system not in systems or website:
                continue
            try:
                if isinstance(source, CsvCodeSource):
                    row = next((r for (s, _, _), r in source.codes.items() if s == system), None)
                    if row:
                        return {
                            "code": row["code"],
                            "version": row.get("version", ""),
                            "source": str(source.path),
                        }, errors
                elif isinstance(source, SqliteCodeSource):
                    with source._lock:
                        row = source.db.execute(
                            "SELECT code,version FROM codes WHERE system=? LIMIT 1", (system,)
                        ).fetchone()
                    if row:
                        return {"code": row[0], "version": row[1], "source": str(source.path)}, errors
                elif isinstance(source, FhirCodeSource):
                    client = source.client
                    url = client.base_url + "/CodeSystem?" + urlencode({"url": system, "_count": 1})
                    headers = {"Accept": "application/fhir+json"}
                    if client.bearer_token:
                        headers["Authorization"] = "Bearer " + client.bearer_token
                    options = {"context": client.tls_context} if client.tls_context else {}
                    with urlopen(Request(url, headers=headers), timeout=self.timeout, **options) as response:
                        data = json.load(response)
                    if data.get("resourceType") != "Bundle":
                        raise ValueError("CodeSystem search did not return a Bundle")
                    for entry in data.get("entry") or []:
                        resource = entry.get("resource") or {}
                        if resource.get("resourceType") != "CodeSystem" or resource.get("url") != system:
                            continue
                        sample = {
                            "code": first_concept(resource),
                            "version": resource.get("version", ""),
                            "content": resource.get("content"),
                            "source": client.base_url,
                        }
                        if sample["content"] == "supplement" or sample["code"]:
                            return sample, errors
            except Exception as exc:
                errors.append(error_label(exc))
        return None, errors

    def system(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        system = row["system"]
        if row["usage"] == "identifier_namespace":
            result["live_status"] = "not_applicable"
            result["reason"] = "identifier_namespace_is_not_a_code_list"
            return result
        if system == UCUM:
            try:
                positive, negative = self.ucum.is_valid("g/dL"), self.ucum.is_valid(INVALID_CODE)
                result.update(
                    positive={"code": "g/dL", "state": "valid" if positive else "invalid"},
                    negative={"code": INVALID_CODE, "state": "valid" if negative else "invalid"},
                    live_status="passed" if positive and not negative else "failed",
                )
            except Exception as exc:
                result.update(live_status="unknown", errors=[error_label(exc)])
            return result
        sample, errors = self.discover(system)
        result["errors"] = errors
        if sample and sample.get("content") == "supplement":
            result.update(
                usage="supplement",
                live_status="not_applicable",
                reason="supplement_does_not_define_an_independent_code_list",
                catalog=sample,
            )
            return result
        # A known example lets LOINC be tested even if its CodeSystem search
        # intentionally omits the large concept list.
        if system == LOINC:
            sample = {"code": "718-7", "version": "", "source": "https://loinc.org/718-7/"}
        elif not sample:
            sample = self.examples.get(system)
        if sample and sample.get("code"):
            if system == LOINC:
                try:
                    term = self.loinc.lookup(sample["code"])
                    positive = {
                        "state": "valid" if term else "unknown",
                        "code": sample["code"],
                        "source": term.source if term else self.loinc.base_url,
                    }
                except Exception as exc:
                    positive = {"state": "unknown", "code": sample["code"], "errors": [error_label(exc)]}
                if positive["state"] != "valid":
                    positive = {
                        **self.router.check(system, sample["code"], sample.get("version", "")),
                        "code": sample["code"],
                    }
            else:
                positive = {
                    **self.router.check(system, sample["code"], sample.get("version", "")),
                    "code": sample["code"],
                }
            result["positive"] = positive
            result["catalog"] = sample
        negative = self.router.check(system, INVALID_CODE, (sample or {}).get("version", ""))
        if (
            system == LOINC
            and negative["state"] == "unknown"
            and (result["positive"] or {}).get("state") == "valid"
        ):
            try:
                term = self.loinc.lookup(INVALID_CODE)
                negative = {"state": "valid" if term else "invalid", "source": self.loinc.base_url}
            except Exception as exc:
                negative = {"state": "unknown", "errors": [error_label(exc)]}
        result["negative"] = {**negative, "code": INVALID_CODE}
        positive_state = (result["positive"] or {}).get("state", "not_checked")
        if positive_state == "invalid" or negative["state"] == "valid":
            result["live_status"] = "failed"
        elif positive_state == "valid" and negative["state"] == "invalid":
            result["live_status"] = "passed"
        elif positive_state == "valid" or negative["state"] == "invalid":
            result["live_status"] = "partial"
        else:
            result["live_status"] = "unknown"
        if not sample:
            result["reason"] = "no_authoritative_positive_example"
        elif result["live_status"] == "partial":
            result["reason"] = "only_one_membership_direction_verified"
        elif result["live_status"] == "unknown":
            result["reason"] = "membership_unverifiable"
        return result

    def unit(self, case: dict[str, Any]) -> dict[str, Any]:
        result = {
            **case,
            "system": UCUM,
            "source": self.ucum.base_url,
            "actual_state": "unknown",
            "test_status": "unknown",
        }
        try:
            actual = self.ucum.is_valid(case["unit"])
            result["actual_state"] = "valid" if actual else "invalid"
            expected = case.get("expected_valid")
            result["test_status"] = (
                "observed" if expected is None else "passed" if actual is expected else "failed"
            )
        except Exception as exc:
            result["error"] = error_label(exc)
        return result

    def conversion(self, case: dict[str, Any]) -> dict[str, Any]:
        result = {
            **case,
            "system": UCUM,
            "source": self.ucum.base_url,
            "actual_factor": None,
            "test_status": "unknown",
        }
        try:
            factor = self.ucum.factor(case["source_unit"], case["target_unit"])
            result["actual_factor"] = str(factor) if factor is not None else None
            expected = case.get("expected_factor")
            result["test_status"] = (
                "passed"
                if (factor is None and expected is None)
                or (factor is not None and expected is not None and factor == Decimal(expected))
                else "failed"
            )
        except Exception as exc:
            result["error"] = error_label(exc)
        return result


def unit_cases(extra_csv: Path | None = None, unit_column: str = "unit") -> tuple[list, list]:
    cases = json.loads((ROOT / "test_cases.json").read_text(encoding="utf-8"))
    units = {row["unit"]: {**row, "origins": ["reference_test"]} for row in cases["units"]}
    if extra_csv:
        with extra_csv.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if unit_column not in (reader.fieldnames or []):
                raise ValueError(f"Unit input does not have column {unit_column!r}")
            for row in reader:
                unit = row[unit_column]  # preserve raw spelling, including whitespace
                case = units.setdefault(unit, {"unit": unit, "expected_valid": None, "origins": []})
                case["input_row_count"] = case.get("input_row_count", 0) + 1
                if "input_csv" not in case["origins"]:
                    case["origins"].append("input_csv")
    return sorted(units.values(), key=lambda row: row["unit"]), cases["conversions"]


def export(
    prefix: Path, systems: list[dict], units: list[dict], conversions: list[dict], *, live: bool
) -> dict[str, Any]:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    tables = {
        "systems": (
            systems,
            [
                "system",
                "label",
                "usage",
                "origins",
                "offline_contract",
                "live_status",
                "positive_state",
                "positive_code",
                "negative_state",
                "reason",
                "errors",
            ],
        ),
        "units": (
            units,
            [
                "system",
                "unit",
                "source",
                "origins",
                "input_row_count",
                "expected_valid",
                "actual_state",
                "test_status",
                "note",
                "error",
            ],
        ),
        "conversions": (
            conversions,
            [
                "system",
                "source_unit",
                "target_unit",
                "source",
                "expected_factor",
                "actual_factor",
                "test_status",
                "note",
                "error",
            ],
        ),
    }
    with Path(f"{prefix}.evidence.jsonl").open("w", encoding="utf-8") as evidence:
        for kind, (rows, fields) in tables.items():
            with Path(f"{prefix}.{kind}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for source in rows:
                    evidence.write(json.dumps({"kind": kind, **source}, ensure_ascii=False) + "\n")
                    row = {
                        **source,
                        "positive_state": (source.get("positive") or {}).get("state", "not_checked"),
                        "positive_code": (source.get("positive") or {}).get("code", ""),
                        "negative_state": (source.get("negative") or {}).get("state", "not_checked"),
                    }
                    writer.writerow(
                        {
                            field: json.dumps(row[field], ensure_ascii=False)
                            if isinstance(row.get(field), (list, dict))
                            else row.get(field, "")
                            for field in fields
                        }
                    )
    summary = {
        "kind": "terminology-registry-check/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live" if live else "offline_synthetic",
        "registered_systems": len(systems),
        "offline_contracts": dict(Counter(row["offline_contract"] for row in systems)),
        "offline_assertions": sum(len(row["synthetic_checks"]) for row in systems),
        "live_systems": dict(Counter(row["live_status"] for row in systems)),
        "unit_states": dict(Counter(row["actual_state"] for row in units)),
        "unit_tests": dict(Counter(row["test_status"] for row in units)),
        "input_csv_distinct_units": sum("input_csv" in row["origins"] for row in units),
        "input_csv_unit_states": dict(
            Counter(row["actual_state"] for row in units if "input_csv" in row["origins"])
        ),
        "input_csv_row_unit_states": {
            state: sum(
                row.get("input_row_count", 0)
                for row in units
                if row["actual_state"] == state and "input_csv" in row["origins"]
            )
            for state in sorted({row["actual_state"] for row in units if "input_csv" in row["origins"]})
        },
        "conversion_tests": dict(Counter(row["test_status"] for row in conversions)),
        "note": "Synthetic coverage proves adapter behavior only. A live pass checks one positive and one negative example, not the whole CodeSystem or IG binding.",
    }
    Path(f"{prefix}.summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Call configured terminology services")
    parser.add_argument("--env-file", type=Path, help="Explicit private service environment file")
    parser.add_argument("--terminology-sources", type=Path)
    parser.add_argument("--units-csv", type=Path, help="Also screen every distinct raw unit in this CSV")
    parser.add_argument("--unit-column", default="unit")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--require-live", action="store_true", help="Fail if live coverage is incomplete")
    args = parser.parse_args()
    if args.timeout < 1 or args.require_live and not args.live:
        parser.error("Positive timeout and --live with --require-live are required")
    if args.env_file:
        load_environment(args.env_file)
    sources_path = args.terminology_sources or os.getenv("SQC_TERMINOLOGY_SOURCES", "")
    entries = load_sources(Path(sources_path)) if sources_path else []
    registry = load_registry(source_entries=entries)
    systems = offline_checks(registry)
    units, conversions = unit_cases(args.units_csv, args.unit_column)
    if args.live:
        checker = LiveChecks(registry, entries, timeout=args.timeout)
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                completed = []
                for row in pool.map(checker.system, systems):
                    completed.append(row)
                    if len(completed) % 20 == 0:
                        print(
                            f"Checked {len(completed)}/{len(systems)} registry entries",
                            file=sys.stderr,
                            flush=True,
                        )
                systems = completed
                units = list(pool.map(checker.unit, units))
                conversions = list(pool.map(checker.conversion, conversions))
        finally:
            checker.close()
    else:
        units = [
            {**case, "system": UCUM, "actual_state": "not_checked", "test_status": "not_checked"}
            for case in units
        ]
        conversions = [{**case, "system": UCUM, "test_status": "not_checked"} for case in conversions]
    summary = export(args.output_prefix, systems, units, conversions, live=args.live)
    print(json.dumps(summary, ensure_ascii=False))
    failed = (
        summary["offline_contracts"].get("failed", 0)
        + summary["live_systems"].get("failed", 0)
        + summary["unit_tests"].get("failed", 0)
        + summary["conversion_tests"].get("failed", 0)
    )
    incomplete = (
        sum(summary["live_systems"].get(state, 0) for state in ("unknown", "partial"))
        + summary["unit_tests"].get("unknown", 0)
        + summary["conversion_tests"].get("unknown", 0)
    )
    raise SystemExit(1 if failed else 2 if args.require_live and incomplete else 0)


if __name__ == "__main__":
    main()
