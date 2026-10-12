# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""CSV/OMOP column adapter for the same FHIR quality decision kernel."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CsvMapping:
    profile: str
    fields: dict[str, str]
    defaults: dict[str, str]
    input_granularity: str = "auto"

    @classmethod
    def read(cls, path: Path) -> "CsvMapping":
        data = json.loads(path.read_text(encoding="utf-8"))
        fields = data.get("fields")
        defaults = data.get("defaults") or {}
        if not isinstance(fields, dict) or not all(fields.get(key) for key in ("code", "unit")):
            raise ValueError("CSV mapping needs code and unit column names")
        if not isinstance(defaults, dict) or not isinstance(data.get("profile"), str):
            raise ValueError("CSV mapping needs a profile and defaults object")
        granularity = data.get("input_granularity", "auto")
        if granularity not in {"auto", "individual", "distinct"}:
            raise ValueError("input_granularity must be auto, individual or distinct")
        return cls(data["profile"], fields, defaults, granularity)

    @property
    def granularity(self) -> str:
        if self.input_granularity != "auto":
            return self.input_granularity
        return "distinct" if {"count", "value_min", "value_max"} & self.fields.keys() else "individual"

    def value(self, row: dict[str, str], key: str) -> str:
        column = self.fields.get(key)
        value = row.get(column) if column else self.defaults.get(key)
        return "" if value is None else str(value).strip()


def observation_from_row(
    row: dict[str, str],
    mapping: CsvMapping,
    *,
    with_values: bool = False,
    with_ranges: bool = False,
    allow_incomplete: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Construct an internal Observation shape; never assert an unverified UCUM code."""
    code, raw_unit = mapping.value(row, "code"), mapping.value(row, "unit")
    code_system = mapping.value(row, "code_system")
    if not allow_incomplete and (not code or not raw_unit or not code_system):
        raise ValueError("Row lacks code, unit or code_system")
    quantity: dict[str, Any] = {"unit": raw_unit} if raw_unit else {}
    unit_code, unit_system = mapping.value(row, "unit_code"), mapping.value(row, "unit_system")
    if unit_code:
        quantity["code"] = unit_code
    if unit_system:
        quantity["system"] = unit_system
    if mapping.value(row, "comparator"):
        quantity["comparator"] = mapping.value(row, "comparator")
    coding = {"system": code_system, "code": code}
    if mapping.value(row, "code_version"):
        coding["version"] = mapping.value(row, "code_version")
    if mapping.value(row, "code_display"):
        coding["display"] = mapping.value(row, "code_display")
    obs: dict[str, Any] = {
        "resourceType": "Observation",
        "code": {"coding": [coding]},
        "valueQuantity": quantity,
    }
    if mapping.value(row, "code_text"):
        obs["code"]["text"] = mapping.value(row, "code_text")
    if mapping.value(row, "status"):
        obs["status"] = mapping.value(row, "status")
    for role, target in (("method_json", "method"), ("modifier_extensions_json", "modifierExtension")):
        if mapping.value(row, role):
            obs[target] = json.loads(mapping.value(row, role))
    if with_values:
        value = mapping.value(row, "value")
        if value:
            quantity["value"] = value
    if with_values or with_ranges:
        ranges = reference_ranges_from_row(row, mapping)
        if ranges:
            obs["referenceRange"] = ranges
    context = {
        "source_test": mapping.value(row, "source_test"),
        "loinc_system": mapping.value(row, "loinc_system"),
        "method": mapping.value(row, "method"),
    }
    axes = {name: mapping.value(row, role) for name, role in (
        ("component", "loinc_component"), ("property", "loinc_property"),
        ("time", "loinc_time_aspect"), ("scale", "loinc_scale")) if mapping.value(row, role)}
    if axes:
        context["source_axes"] = axes
    return obs, context


def reference_ranges_from_row(row: dict[str, str], mapping: CsvMapping) -> list[dict[str, Any]]:
    """Preserve bound units and population qualifiers independently of patient values."""
    encoded = mapping.value(row, "reference_ranges_json")
    if encoded:
        ranges = json.loads(encoded)
        if not isinstance(ranges, list) or any(not isinstance(rng, dict) for rng in ranges):
            raise ValueError("reference_ranges_json must contain an array of FHIR reference ranges")
        return ranges

    def quantity(prefix: str) -> dict[str, str]:
        result = {}
        value = mapping.value(row, prefix + "_value") or mapping.value(row, prefix)
        if value:
            result["value"] = value
        for field in ("unit", "code", "system", "comparator"):
            text = mapping.value(row, prefix + "_" + field)
            if text:
                result[field] = text
        return result

    interval: dict[str, Any] = {}
    for side in ("low", "high"):
        bound = quantity("range_" + side)
        if bound:
            interval[side] = bound
    for source, target in (("range_type", "type"), ("range_applies_to", "appliesTo")):
        system, code, text = (
            mapping.value(row, source + "_" + field) for field in ("system", "code", "text")
        )
        concept: dict[str, Any] = {}
        if code or (system and source + "_system" in mapping.fields):
            concept["coding"] = [{"system": system, "code": code}]
        if text:
            concept["text"] = text
        if concept:
            interval[target] = [concept] if target == "appliesTo" else concept
    age = {side: quantity("range_age_" + side) for side in ("low", "high")}
    if any(age.values()):
        interval["age"] = {side: value for side, value in age.items() if value}
    if mapping.value(row, "range_text"):
        interval["text"] = mapping.value(row, "range_text")
    return [interval] if interval else []


def _weight(row: dict[str, str], mapping: CsvMapping) -> int:
    raw = mapping.value(row, "count")
    if not raw:
        return 1
    count = int(raw)
    if count < 0:
        raise ValueError("Pair count cannot be negative")
    return count
