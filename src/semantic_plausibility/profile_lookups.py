"""Selected profile rules choose target systems; operators choose their endpoints."""

from __future__ import annotations

import hashlib
import json

from .profile_config import element_rule, load_profile
from .profile_import import import_structure_definition


def selected_profile(config):
    spec = config.data.get("specification", {})
    if spec.get("STRUCTURE_DEFINITION"):
        document = import_structure_definition(config.path_value(spec["STRUCTURE_DEFINITION"]))
        return load_profile(document["name"], data=document)
    return load_profile("fhir-r4-lab" if config.profile == "auto" else config.profile,
                        config.path_value(spec["PROFILE_FILE"]) if spec.get("PROFILE_FILE") else None)


def terminology_plan(config, profile=None):
    profile = profile or selected_profile(config)
    value_sets = {}
    for name in config.data.get("specification", {}).get("VALUE_SETS", []):
        path = config.path_value(name)
        raw = path.read_bytes()
        document = json.loads(raw)
        if document.get("resourceType") != "ValueSet" or not document.get("url") or not document.get("version"):
            raise ValueError("Configured VALUE_SETS need real, versioned FHIR ValueSet artifacts")
        key = (document["url"], document["version"])
        if key in value_sets:
            raise ValueError("Duplicate configured ValueSet edition")
        value_sets[key] = (document, hashlib.sha256(raw).hexdigest())
    elements = []
    for path in ("Observation.code", "Observation.component.code", "Observation.valueQuantity",
                 "Observation.component.valueQuantity", "Observation.referenceRange.low", "Observation.referenceRange.high"):
        rule, origin = element_rule(profile, path)
        matches = [(data, sha) for (canonical, version), (data, sha) in value_sets.items()
                   if canonical == rule.binding and (not rule.binding_version or version == rule.binding_version)]
        matched = matches[0] if len(matches) == 1 else None
        systems = sorted({row["system"] for row in (matched[0].get("compose", {}).get("include", []) if matched else [])
                          if row.get("system")})
        required = [rule.required_coding_system] if rule.required_coding_system else list(rule.allowed_systems or [])
        primary = sorted(set(required or systems))
        elements.append({"element": path, "rule_source": origin, "prohibited": rule.prohibited,
            "required_systems": required, "preferred_or_bound_systems": systems,
            "primary_systems": primary, "binding": rule.binding, "binding_strength": rule.binding_strength,
            "binding_version": rule.binding_version, "value_set_edition": matched[0]["version"] if matched else None,
            "value_set_sha256": matched[1] if matched else None,
            "scope": "System routing only; filters, FHIRPath and full ValueSet membership require their validator/service"})
    services = []
    for alias, row in config.data.get("lookups", {}).items():
        if not row.get("enabled"):
            continue
        kind = row.get("type")
        systems = [{"loinc": "http://loinc.org", "ucum": "http://unitsofmeasure.org"}[kind]] if kind in {"loinc", "ucum"} else row.get("systems", [])
        default_role = "primary" if kind in {"loinc", "ucum", "fhir"} else "optional" if kind in {"ols", "website"} else "source"
        services.append({"alias": alias, "role": row.get("role", default_role), "systems": systems,
            "type": kind, "location": row.get("endpoint") or row.get("path") or "configured endpoint reference"})
    for entry in elements:
        entry["primary_services"] = [s["alias"] for s in services if s["role"] == "primary"
            and set(s["systems"]) & set(entry["primary_systems"])]
    return {"profile": profile.canonical or "http://hl7.org/fhir/StructureDefinition/Observation",
            "profile_version": profile.guide_version or "4.0.1", "elements": elements, "services": services,
            "profile_does_not_define_endpoint_urls": True, "formal_fhir_validation": "external"}
