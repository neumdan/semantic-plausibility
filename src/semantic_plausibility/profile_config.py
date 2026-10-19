# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Versioned input policies; profile claims never count as validation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any


LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"


def missing_metadata_only(policy: dict) -> bool:
    """Keep formal FHIR violations, but distinguish absent data from contradictions."""
    issues = set(policy.get("issues", []))
    return bool(issues) and issues <= {
        "missing_code", "guide_requires_code_system", "missing_system_for_coded_check",
        "qty-3_missing_system", "guide_requires_unit_code", "guide_requires_unit_system",
        "guide_requires_unit_display",
    }


@dataclass(frozen=True)
class ElementRule:
    """One IG constraint. None means inherited FHIR R4, not an allow-list."""

    allowed_systems: tuple[str, ...] | None = None
    required_coding_system: str = ""
    require_code: bool = False
    require_system: bool = False
    require_unit: bool = False
    prohibited: bool = False
    binding: str = ""
    binding_strength: str = ""
    binding_version: str = ""
    reference: str = ""
    system_bindings: dict[str, tuple[str, str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class InputProfile:
    name: str
    canonical: str = ""
    guide_version: str = ""
    element_rules: dict[str, ElementRule] = field(default_factory=dict)
    source_sha256: str = ""


FHIR_R4_RULES = {
    "Observation.code": ElementRule(
        binding="http://hl7.org/fhir/ValueSet/observation-codes",
        binding_strength="example",
        reference="https://hl7.org/fhir/R4/observation.html",
    ),
    "Observation.component.code": ElementRule(
        binding="http://hl7.org/fhir/ValueSet/observation-codes",
        binding_strength="example",
        reference="https://hl7.org/fhir/R4/observation.html",
    ),
    "*.Quantity": ElementRule(reference="https://hl7.org/fhir/R4/datatypes.html"),
}

MII_LAB_RULES = {
    "Observation.code": ElementRule(
        binding="http://hl7.org/fhir/uv/ips/ValueSet/results-laboratory-observations-uv-ips",
        binding_strength="preferred",
        reference="https://www.medizininformatik-initiative.de/Kerndatensatz/Modul_Laborbefund/Observation.html",
    ),
    "Observation.valueQuantity": ElementRule(
        allowed_systems=(UCUM,),
        require_code=True,
        require_system=True,
        require_unit=True,
        reference="https://www.medizininformatik-initiative.de/Kerndatensatz/Modul_Laborbefund/Observation.html",
    ),
}

ISIK_LAB_RULES = {
    "Observation.code": ElementRule(
        required_coding_system=LOINC,
        require_code=True,
        binding="http://hl7.org/fhir/uv/ips/ValueSet/results-laboratory-observations-uv-ips",
        binding_strength="extensible",
        reference="https://gemspec.gematik.de/ig/fhir/isik/icu/6.0.0/StructureDefinition-ISiKLaboruntersuchung.html",
    ),
    "Observation.valueQuantity": ElementRule(
        allowed_systems=(UCUM,),
        require_code=True,
        require_system=True,
        reference="https://gemspec.gematik.de/ig/fhir/isik/icu/6.0.0/StructureDefinition-ISiKLaboruntersuchung.html",
    ),
    "Observation.referenceRange.low": ElementRule(
        allowed_systems=(UCUM,),
        require_code=True,
        require_system=True,
        reference="https://gemspec.gematik.de/ig/fhir/isik/icu/6.0.0/StructureDefinition-ISiKLaboruntersuchung.html",
    ),
    "Observation.referenceRange.high": ElementRule(
        allowed_systems=(UCUM,),
        require_code=True,
        require_system=True,
        reference="https://gemspec.gematik.de/ig/fhir/isik/icu/6.0.0/StructureDefinition-ISiKLaboruntersuchung.html",
    ),
}

EU_LAB_REFERENCE = (
    "https://hl7.eu/fhir/laboratory/2.0.0/StructureDefinition-Observation-resultslab-eu-lab.html"
)
EU_LAB_RULES = {
    "Observation.code": ElementRule(
        binding="http://hl7.eu/fhir/laboratory/ValueSet/lab-obsCode-eu-lab",
        binding_strength="preferred",
        reference=EU_LAB_REFERENCE,
    ),
    "Observation.component.code": ElementRule(
        binding="http://hl7.eu/fhir/laboratory/ValueSet/lab-obsCode-eu-lab",
        binding_strength="preferred",
        reference=EU_LAB_REFERENCE,
    ),
    "Observation.valueQuantity": ElementRule(
        allowed_systems=(UCUM,),
        reference="https://hl7.eu/fhir/laboratory/2.0.0/StructureDefinition-Quantity-eu-lab.html",
    ),
    "Observation.component.valueQuantity": ElementRule(
        allowed_systems=(UCUM,),
        reference="https://hl7.eu/fhir/laboratory/2.0.0/StructureDefinition-Quantity-eu-lab.html",
    ),
}



PROFILES = {
    "fhir-r4-lab": InputProfile("fhir-r4-lab"),
    "eu-lab": InputProfile(
        "eu-lab",
        canonical="http://hl7.eu/fhir/laboratory/StructureDefinition/Observation-resultslab-eu-lab",
        guide_version="2.0.0",
        element_rules=EU_LAB_RULES,
    ),
    "mii-kds-lab": InputProfile(
        "mii-kds-lab",
        canonical="https://www.medizininformatik-initiative.de/fhir/core/modul-labor/StructureDefinition/ObservationLab",
        guide_version="2026.0.3",
        element_rules=MII_LAB_RULES,
    ),
    "gematik-isik-lab": InputProfile(
        "gematik-isik-lab",
        canonical="https://gematik.de/fhir/isik/StructureDefinition/ISiKLaboruntersuchung",
        guide_version="6.0.0",
        element_rules=ISIK_LAB_RULES,
    ),
    "csv-loinc-unit": InputProfile("csv-loinc-unit"),
    "omop-measurement": InputProfile("omop-measurement"),
    "fhir-r4": InputProfile("fhir-r4"),
}


def element_rule(profile: InputProfile, path: str) -> tuple[ElementRule, str]:
    """Resolve a specific IG rule, then inherited FHIR R4 semantics."""
    normalized = re.sub(r"\[\d+\]", "[*]", path.replace("[x]", ""))
    for candidate in (normalized, normalized.replace("[*]", "")):
        if candidate in profile.element_rules:
            return profile.element_rules[candidate], profile.canonical or profile.name
        if candidate in FHIR_R4_RULES:
            return FHIR_R4_RULES[candidate], "FHIR R4"
    if normalized.endswith("Quantity") or ".referenceRange." in normalized:
        return FHIR_R4_RULES["*.Quantity"], "FHIR R4"
    resource = normalized.split(".", 1)[0]
    return ElementRule(reference=f"https://hl7.org/fhir/R4/{resource.lower()}.html"), "FHIR R4"


def assess_coding(profile: InputProfile, path: str, system: str, code: str) -> dict[str, Any]:
    rule, origin = element_rule(profile, path)
    binding, strength, binding_version = rule.system_bindings.get(
        system, (rule.binding, rule.binding_strength, rule.binding_version)
    )
    issues = []
    review = []
    if rule.prohibited:
        issues.append("element_excluded_by_guide")
    if rule.require_code and not code:
        issues.append("missing_code")
    if rule.require_system and not system:
        issues.append("guide_requires_code_system")
    if code and not system:
        review.append("missing_system_for_coded_check")
    if rule.allowed_systems is not None and system and system not in rule.allowed_systems:
        issues.append("system_excluded_by_guide")
    return {
        "path": path,
        "system": system,
        "code": code,
        "policy_source": origin,
        "reference": rule.reference,
        "binding": binding or None,
        "binding_strength": strength or None,
        "binding_version": binding_version or None,
        "allowed_systems": list(rule.allowed_systems) if rule.allowed_systems is not None else None,
        "state": "invalid" if issues else "unverifiable" if review else "compatible",
        "issues": issues + review,
    }


def assess_quantity(profile: InputProfile, path: str, quantity: dict[str, Any]) -> dict[str, Any]:
    rule, origin = element_rule(profile, path)
    code = str(quantity.get("code") or "")
    system = str(quantity.get("system") or "")
    issues = []
    if rule.prohibited:
        issues.append("element_excluded_by_guide")
    # FHIR R4 qty-3: a coded unit requires its defining system.
    if code and not system:
        issues.append("qty-3_missing_system")
    if rule.require_code and not code:
        issues.append("guide_requires_unit_code")
    if rule.require_system and not system:
        issues.append("guide_requires_unit_system")
    if rule.require_unit and not quantity.get("unit"):
        issues.append("guide_requires_unit_display")
    if rule.allowed_systems is not None and system and system not in rule.allowed_systems:
        issues.append("system_excluded_by_guide")
    return {
        "path": path,
        "code": code,
        "system": system,
        "policy_source": origin,
        "reference": rule.reference,
        "allowed_systems": list(rule.allowed_systems) if rule.allowed_systems is not None else None,
        "state": "invalid" if issues else "compatible",
        "issues": issues,
    }


def load_profile(name: str, config: Path | None = None, *, data: dict | None = None) -> InputProfile:
    """Load explicit per-element IG constraints; omitted elements inherit FHIR R4."""
    if config is None and data is None:
        if name not in PROFILES:
            raise ValueError(f"Unknown input profile: {name}")
        return PROFILES[name]
    if config is not None and data is not None:
        raise ValueError("Choose a profile file or imported profile data")
    data = data if data is not None else json.loads(config.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("name") != name:
        raise ValueError("Profile config must be an object with the selected name")
    if data.get("fhir_version", "4.0.1") != "4.0.1":
        raise ValueError("Only FHIR R4 (4.0.1) fallback semantics are implemented")
    if data.get("require_profile_validation"):
        raise ValueError("Full FHIR validation is outside this project's scope")
    raw_rules = data.get("element_rules", {})
    if not isinstance(raw_rules, dict):
        raise ValueError("element_rules must be an object keyed by FHIR element path")
    rules = {}
    for path, raw in raw_rules.items():
        if not isinstance(path, str) or not isinstance(raw, dict):
            raise ValueError("Each element rule needs a path and object")
        for flag in ("require_code", "require_system", "require_unit", "prohibited"):
            if flag in raw and not isinstance(raw[flag], bool):
                raise ValueError(f"{flag} must be a Boolean")
        if raw.get("binding_strength", "") not in {"", "required", "extensible", "preferred", "example"}:
            raise ValueError("Unknown ValueSet binding strength")
        systems = raw.get("allowed_systems")
        if systems is not None and (
            not isinstance(systems, list)
            or any(
                not isinstance(value, str) or not value.startswith(("http://", "https://", "urn:"))
                for value in systems
            )
        ):
            raise ValueError("allowed_systems must be a URI array or null")
        rules[path] = ElementRule(
            allowed_systems=tuple(systems) if systems is not None else None,
            required_coding_system=str(raw.get("required_coding_system") or ""),
            require_code=bool(raw.get("require_code", False)),
            require_system=bool(raw.get("require_system", False)),
            require_unit=bool(raw.get("require_unit", False)),
            prohibited=bool(raw.get("prohibited", False)),
            binding=str(raw.get("binding") or ""),
            binding_strength=str(raw.get("binding_strength") or ""),
            binding_version=str(raw.get("binding_version") or ""),
            reference=str(raw.get("reference") or ""),
            system_bindings={
                str(system): (
                    str(value["value_set"]),
                    str(value.get("strength") or "required"),
                    str(value.get("version") or ""),
                )
                for system, value in (raw.get("system_bindings") or {}).items()
            },
        )
    return InputProfile(name, str(data.get("canonical") or ""), str(data.get("guide_version") or ""), rules,
                        data.get("import_provenance", {}).get("source_sha256", ""))


def profile_assessment(resource: dict[str, Any], profile: InputProfile) -> dict[str, Any]:
    """Policy provenance only. This project does not perform FHIR validation."""
    declared = (
        profile.canonical
        in [str(value).split("|")[0] for value in (resource.get("meta") or {}).get("profile", [])]
        if profile.canonical
        else False
    )
    return {
        "name": profile.name,
        "canonical": profile.canonical or None,
        "guide_version": profile.guide_version or "4.0.1",
        "declared": declared,
        "state": "policy_selected",
        "full_fhir_validation": "out_of_scope",
    }
