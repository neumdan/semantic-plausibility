# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Evidence-preserving quality checks for FHIR laboratory Observations.

This module never changes a source Observation. It produces a sidecar report that
can be called before FHIR persistence, during ingest, or after retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
import re


LOINC_URI = "http://loinc.org"
UCUM_URI = "http://unitsofmeasure.org"
PHASES = {"pre_fhir", "at_fhir", "post_fhir"}
AMBIGUOUS_NOTATION = re.compile(r"(\.\.\.|…|\b10\s*\^\s*[+-]?\d+\s*$|\b10e[+-]?\d+\b)", re.I)


@dataclass(frozen=True)
class LoincTerm:
    code: str
    display: str
    component: str = ""
    property: str = ""
    system: str = ""
    scale: str = ""
    method: str = ""
    example_ucum_units: tuple[str, ...] = ()
    example_units: tuple[str, ...] = ()
    source: str = ""
    version: str = ""
    time_aspect: str = ""


@dataclass(frozen=True)
class UnitEvidence:
    unit: str
    kind: str  # loinc_ucum_example | loinc_other_example | website_example | llm_candidate
    source: str
    note: str = ""


@dataclass(frozen=True)
class ResearchCandidate:
    evidence_type: str  # reference_interval | analytical_interval | documented_extreme
    source: str  # PMID or other persistent source URL
    low: str | None = None
    high: str | None = None
    unit: str | None = None
    population: str = ""
    specimen: str = ""
    method: str = ""
    excerpt: str = ""
    status: str = "candidate"


class UnitAuthority(Protocol):
    def is_valid(self, unit: str) -> bool: ...
    def factor(self, source: str, target: str) -> Decimal | None: ...


class LoincAuthority(Protocol):
    def lookup(self, code: str) -> LoincTerm | None: ...


class EvidenceResearch(Protocol):
    def website_units(self, code: str, term: LoincTerm) -> list[UnitEvidence]: ...
    def suggest_units(
        self, source_unit: str, term: LoincTerm, examples: list[UnitEvidence]
    ) -> list[UnitEvidence]: ...
    def search_studies(self, term: LoincTerm) -> list[ResearchCandidate]: ...


@dataclass(frozen=True)
class ApprovedMapping:
    """Approval is scoped to local test and context, never only to a LOINC code."""

    source_test: str
    loinc_code: str
    source_unit: str
    target_unit: str
    factor: Decimal
    specimen: str
    method: str
    approved_by: str
    approved_at: str
    evidence: str

    def matches(
        self,
        *,
        source_test: str,
        loinc_code: str,
        source_unit: str,
        target_unit: str,
        factor: Decimal,
        specimen: str,
        method: str,
    ) -> bool:
        return bool(self.approved_by and self.approved_at and self.evidence) and all(
            (
                self.source_test == source_test,
                self.loinc_code == loinc_code,
                self.source_unit == source_unit,
                self.target_unit == target_unit,
                self.factor == factor,
                self.specimen == specimen,
                self.method == method,
            )
        )
