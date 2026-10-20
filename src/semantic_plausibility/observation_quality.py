# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Evidence-preserving quality checks for FHIR laboratory Observations.

This module never changes a source Observation. It produces a sidecar report that
can be called before FHIR persistence, during ingest, or after retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


LOINC_URI = "http://loinc.org"
UCUM_URI = "http://unitsofmeasure.org"


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
    part_codes: dict[str, str] = field(default_factory=dict)
    designations: tuple[dict[str, str], ...] = ()
    synonyms: tuple[str, ...] = ()
    status: str = ""
    axis_sources: dict[str, dict] = field(default_factory=dict)
    lookup_attempts: tuple[dict, ...] = ()


@dataclass(frozen=True)
class UnitEvidence:
    unit: str
    kind: str  # loinc_ucum_example | loinc_other_example | website_example | llm_candidate
    source: str
    note: str = ""


@dataclass(frozen=True)
class ResearchCandidate:
    evidence_type: str  # reference_interval | analytical_interval | analytical_limit | reported_measurement | documented_extreme
    source: str  # PMID or other persistent source URL
    low: str | None = None
    high: str | None = None
    unit: str | None = None
    population: str = ""
    specimen: str = ""
    method: str = ""
    excerpt: str = ""
    status: str = "candidate"
    reported_value: str | None = None
    value_kind: str = ""
    source_title: str = ""
    source_sha256: str = ""
    retrieved_at: str = ""
    doi: str = ""
    unit_ucum: str = ""
    unit_validation_state: str = "not_checked"
    unit_validation_source: str = ""
    source_text_rendering: str = ""
    source_kind: str = "abstract"
    source_location: str = ""
    full_text_source: str = ""
    source_license: str = ""
    sample_size: str = ""


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
