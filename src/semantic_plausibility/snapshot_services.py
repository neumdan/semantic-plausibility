# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Read-only adapters for the saved terminology evidence snapshot."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .observation_quality import LoincTerm


class SnapshotUcum:
    def __init__(self, verdicts: dict[str, bool | None], source: str = "saved snapshot"):
        self.verdicts = verdicts
        self.source = source

    def is_valid(self, unit: str) -> bool:
        verdict = self.verdicts.get(unit)
        if verdict is None:
            raise RuntimeError("No reliable saved NLM UCUM verdict for this unit")
        return verdict

    def factor(self, source: str, target: str) -> Decimal | None:
        if source == target and self.is_valid(source):
            return Decimal("1")
        raise RuntimeError("Saved snapshot contains no verified conversion factor")


class SnapshotLoinc:
    def __init__(self, terms: dict[str, LoincTerm]):
        self.terms = {(term.code, term.version): term for term in terms.values()}

    def lookup(self, code: str, version: str = "") -> LoincTerm | None:
        if version:
            return self.terms.get((code, version))
        matches = [term for (candidate, _), term in self.terms.items() if candidate == code]
        return matches[0] if len(matches) == 1 else None


def load_snapshot(path: Path) -> tuple[SnapshotUcum, SnapshotLoinc]:
    """Re-use saved official responses; conflicting/encoded units stay unknown."""
    statuses: dict[str, set[str]] = {}
    terms: dict[str, LoincTerm] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            unit, code = row["raw_unit"], row["loinc_code"]
            status = row["nlm_ucum_status_on_raw"]
            statuses.setdefault(unit, set()).add(status)
            examples = tuple(x.strip() for x in row["loinc_ucum_examples"].split(";") if x.strip())
            if code not in terms:
                terms[code] = LoincTerm(
                    code, "", example_ucum_units=examples, source=f"saved snapshot: {path.name}"
                )
            elif examples and set(examples) != set(terms[code].example_ucum_units):
                merged = tuple(dict.fromkeys((*terms[code].example_ucum_units, *examples)))
                terms[code] = LoincTerm(
                    code, "", example_ucum_units=merged, source=f"saved snapshot: {path.name}"
                )
    verdicts = {
        unit: True if states == {"valid"} else False if states == {"invalid"} else None
        for unit, states in statuses.items()
    }
    return SnapshotUcum(verdicts, f"saved snapshot: {path.name}"), SnapshotLoinc(terms)
