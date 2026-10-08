"""Internal laboratory settings, local file hashes and safe CSV text."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from .audit import ndjson_files


@dataclass(frozen=True)
class CheckSettings:
    """Core settings. Research/model settings deliberately live in a separate feature."""

    profile: str = "auto"
    profile_config: str | Path | None = None
    snapshot: str | Path | None = None
    terminology_sources: str | Path | None = None
    unit_catalog: str | Path | None = None
    approvals: str | Path | None = None
    context_file: str | Path | None = None
    context: Mapping[str, str] = field(default_factory=dict)
    delimiter: str = ","
    deployment_sha256: str = ""
    source_alias: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.delimiter, str) or len(self.delimiter) != 1:
            raise ValueError("delimiter must be one character")
        if (
            not isinstance(self.context, Mapping)
            or set(self.context) - {"source_test", "loinc_system", "method"}
            or any(not isinstance(value, str) for value in self.context.values())
        ):
            raise ValueError("context supports source_test, loinc_system and method strings")

    def checker_options(self, *, profile: str | None = None) -> dict[str, Any]:
        options = {
            key: Path(value) if value is not None else None
            for key in ("profile_config", "snapshot", "terminology_sources", "unit_catalog", "approvals")
            if (value := getattr(self, key)) is not None
        }
        return {**options, "profile": profile or self.profile, "research": False}


def read_contexts(path: str | Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    result = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"resource_id", "source_test", "loinc_system", "method"} <= set(reader.fieldnames or []):
            raise ValueError("Context CSV needs resource_id,source_test,loinc_system,method")
        for row in reader:
            key = row["resource_id"]
            if not key or key in result:
                raise ValueError("Context CSV needs unique nonempty resource_id values")
            result[key] = {name: row[name] for name in ("source_test", "loinc_system", "method")}
    return result


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return _stream_hash(handle)


def _stream_hash(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _input_hash(path: Path) -> str:
    if not path.is_dir():
        return _sha256(path)
    digest = hashlib.sha256()
    for source in ndjson_files(path):
        digest.update(json.dumps([source.name, _sha256(source)], ensure_ascii=False).encode())
    return digest.hexdigest()


def _csv_text(value: str) -> str:
    # Keep genuine signed numbers numeric, but do not export spreadsheet formulas.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
        from .fhir_quality_pipeline import number

        if number(value) is None:
            return "'" + value
    return value
