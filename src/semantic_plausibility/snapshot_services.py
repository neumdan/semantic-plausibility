# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Read-only adapters for the saved terminology evidence snapshot."""

from __future__ import annotations

from decimal import Decimal

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
