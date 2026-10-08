# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Extract candidate intervals from FHIR R4 ObservationDefinition catalogs."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .observation_quality import ResearchCandidate, UnitEvidence


LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"


def number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation, ValueError:
        return None


def serial(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


class ObservationDefinitionCatalog:
    def __init__(self, path: Path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            resources = payload
        elif isinstance(payload, dict) and payload.get("resourceType") == "Bundle":
            resources = [entry.get("resource") for entry in payload.get("entry") or []]
        else:
            resources = [payload]
        self.source = str(path)
        self.by_loinc: dict[str, list[dict[str, Any]]] = {}
        for resource in resources:
            if not isinstance(resource, dict) or resource.get("resourceType") != "ObservationDefinition":
                continue
            for coding in (resource.get("code") or {}).get("coding") or []:
                if coding.get("system") == LOINC and coding.get("code"):
                    self.by_loinc.setdefault(str(coding["code"]), []).append(resource)

    def for_loinc(self, code: str) -> tuple[list[UnitEvidence], list[ResearchCandidate]]:
        units: list[UnitEvidence] = []
        intervals: list[ResearchCandidate] = []
        for resource in self.by_loinc.get(code, []):
            source = str(resource.get("url") or self.source)
            details = resource.get("quantitativeDetails") or {}
            for key in ("unit", "customaryUnit"):
                for coding in (details.get(key) or {}).get("coding") or []:
                    if coding.get("system") == UCUM and coding.get("code"):
                        units.append(UnitEvidence(str(coding["code"]), "observation_definition", source))
            for interval in resource.get("qualifiedInterval") or []:
                category = interval.get("category") or "unspecified"
                kind = {
                    "reference": "reference_interval",
                    "critical": "critical_interval",
                    "absolute": "absolute_interval",
                }.get(category)
                if not kind:
                    continue
                bounds = interval.get("range") or {}
                low, high = bounds.get("low") or {}, bounds.get("high") or {}
                low_value, high_value = number(low.get("value")), number(high.get("value"))
                if (
                    low_value is None
                    and high_value is None
                    or (low_value is not None and high_value is not None and low_value > high_value)
                ):
                    continue
                bound_units = {
                    (bound.get("system"), bound.get("code"))
                    for bound in (low, high)
                    if bound.get("value") is not None
                }
                if len(bound_units) != 1:
                    continue
                unit_system, unit = next(iter(bound_units))
                if unit_system != UCUM or not unit:
                    continue
                context = {
                    field: interval.get(field)
                    for field in ("context", "appliesTo", "gender", "age", "gestationalAge", "condition")
                    if interval.get(field) is not None
                }
                intervals.append(
                    ResearchCandidate(
                        kind,
                        source,
                        low=serial(low_value),
                        high=serial(high_value),
                        unit=unit,
                        population=json.dumps(context, ensure_ascii=False, sort_keys=True),
                        excerpt="FHIR ObservationDefinition.qualifiedInterval",
                        status="candidate",
                    )
                )
        return units, intervals
