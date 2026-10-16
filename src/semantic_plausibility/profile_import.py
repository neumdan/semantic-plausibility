"""Import a selected laboratory policy from an installed R4 profile snapshot.

This is intentionally not a FHIRPath evaluator or complete IG validator. Unsupported
constraints are retained as limitations; official MII validation remains separate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def import_structure_definition(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"Install the selected profile with profile-init, or set STRUCTURE_DEFINITION to your downloaded snapshot: {path}")
    raw = path.read_bytes()
    return parse_structure_definition(raw)


def parse_structure_definition(raw: bytes) -> dict:
    """Validate and project a real downloaded snapshot before storing it."""
    data = json.loads(raw)
    if data.get("resourceType") != "StructureDefinition" or data.get("type") != "Observation":
        raise ValueError("Selected profile must be an Observation StructureDefinition")
    if data.get("fhirVersion") not in {None, "4.0.1"}:
        raise ValueError("Selected profile is not compatible with FHIR R4 4.0.1")
    if not data.get("url") or not data.get("version"):
        raise ValueError("Selected profile requires its canonical and pinned version")
    elements = data.get("snapshot", {}).get("element")
    if not isinstance(elements, list) or not elements:
        raise ValueError("Profile needs a resolved snapshot; resolve package dependencies with the official validator tooling")
    if data.get("status") != "active" or data.get("experimental"):
        raise ValueError("Select an active, non-experimental installed profile")
    rules = {}
    limitations = []
    selected = {"Observation.code", "Observation.component.code", "Observation.valueQuantity",
                "Observation.component.valueQuantity", "Observation.referenceRange.low", "Observation.referenceRange.high",
                "Observation.component.referenceRange.low", "Observation.component.referenceRange.high"}

    def normalized(value):
        return value.replace("value[x]", "valueQuantity")

    for element in elements:
        source_path = normalized(element.get("path", ""))
        if source_path in selected:
            rule = rules.setdefault(source_path, {})
            if element.get("max") == "0":
                rule["prohibited"] = True
            binding = element.get("binding", {})
            if binding.get("valueSet"):
                canonical, _, version = binding["valueSet"].partition("|")
                rule.update(binding=canonical, binding_strength=binding.get("strength", ""), binding_version=version)
        for quantity in selected - {"Observation.code", "Observation.component.code"}:
            if source_path == quantity + ".system" and (element.get("fixedUri") or element.get("patternUri")):
                rules.setdefault(quantity, {})["allowed_systems"] = [element.get("fixedUri") or element["patternUri"]]
            if source_path == quantity + ".system" and element.get("min", 0) > 0:
                rules.setdefault(quantity, {})["require_system"] = True
            if source_path == quantity + ".code" and element.get("min", 0) > 0:
                rules.setdefault(quantity, {})["require_code"] = True
            if source_path == quantity + ".unit" and element.get("min", 0) > 0:
                rules.setdefault(quantity, {})["require_unit"] = True
        if element.get("constraint") and any(source_path.startswith(target) for target in selected):
            limitations.extend({"element": element.get("id", source_path), "constraint": c.get("key"),
                                "expression": c.get("expression"), "state": "requires_full_fhir_validator"}
                               for c in element["constraint"])
    # A mandatory Coding slice with a fixed system makes that system required;
    # optional alternate slices do not prohibit other codings in the concept.
    by_id = {element.get("id", ""): element for element in elements}
    for ident, element in by_id.items():
        if ".coding:" in ident and ident.endswith(".system"):
            parent = by_id.get(ident.removesuffix(".system"), {})
            fixed = element.get("fixedUri") or element.get("patternUri")
            if fixed and parent.get("min", 0) > 0:
                concept = normalized(element["path"].removesuffix(".coding.system"))
                if concept in selected:
                    rule = rules.setdefault(concept, {})
                    if rule.get("required_coding_system") and rule["required_coding_system"] != fixed:
                        raise ValueError("Multiple mandatory coding systems need a curated policy plus full FHIR validation")
                    rule["required_coding_system"] = fixed
    return {"name": "installed-laboratory-profile", "canonical": data["url"], "guide_version": data["version"],
            "element_rules": rules, "import_provenance": {"source_sha256": hashlib.sha256(raw).hexdigest(),
                "scope": "selected_coding_bindings_and_quantity_system_constraints", "limitations": limitations}}
