# Adapted for the standalone semantic-plausibility project (2026-10-02).
"""Build a provenance-preserving terminology inventory from pinned FHIR IG packages.

No clinical code lists are copied: this records profile bindings, CodeSystem URIs
and ValueSet composition from the provided versioned package artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
import tarfile
from typing import Any


KNOWN_LABELS = {
    "http://loinc.org": "LOINC",
    "http://unitsofmeasure.org": "UCUM",
    "http://snomed.info/sct": "SNOMED CT",
    "http://www.whocc.no/atc": "ATC (WHO)",
    "http://fhir.de/CodeSystem/bfarm/atc": "ATC (BfArM)",
    "http://fhir.de/CodeSystem/ifa/pzn": "PZN",
    "http://fhir.de/CodeSystem/ask": "ASK",
    "http://fdasis.nlm.nih.gov": "UNII",
    "http://standardterms.edqm.eu": "EDQM Standard Terms",
    "http://fhir.de/CodeSystem/bfarm/icd-10-gm": "ICD-10-GM",
    "http://fhir.de/CodeSystem/bfarm/ops": "OPS",
}


def _read_package(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resources: list[dict[str, Any]] = []
    metadata: dict[str, Any] | None = None
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith("package/"):
                continue
            if member.name.count("/") != 1 or not member.name.endswith(".json"):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            try:
                obj = json.load(stream)
            except ValueError, UnicodeDecodeError:
                continue
            if member.name == "package/package.json":
                metadata = obj
            elif obj.get("resourceType") in {"StructureDefinition", "ValueSet", "CodeSystem"}:
                obj["_package_file"] = member.name
                resources.append(obj)
    if metadata is None:
        raise ValueError(f"No package/package.json in {path}")
    return metadata, resources


def _write(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generate(manifest: Path, package_dir: Path, output_dir: Path) -> dict[str, int]:
    package_specs = json.loads(manifest.read_text(encoding="utf-8"))["packages"]
    package_rows: list[dict[str, Any]] = []
    artifacts: list[tuple[str, str, dict[str, Any]]] = []
    for spec in package_specs:
        name, version = spec["name"], spec["version"]
        file = package_dir / f"{name}-{version}.tgz"
        row = {
            "package": name,
            "version": version,
            "status": "missing",
            "source": f"https://packages2.fhir.org/packages/{name}/{version}",
            "artifact_count": 0,
        }
        if file.exists():
            metadata, resources = _read_package(file)
            if metadata.get("name") != name or metadata.get("version") != version:
                raise ValueError(f"Package identity mismatch in {file}")
            row.update(status="loaded", artifact_count=len(resources))
            artifacts.extend((name, version, resource) for resource in resources)
        package_rows.append(row)

    value_sets: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
    for package, version, resource in artifacts:
        if resource["resourceType"] == "ValueSet" and resource.get("url"):
            value_sets[resource["url"]].append((package, version, resource))

    def resolve_value_set(url: str, package: str, seen: frozenset[str] = frozenset()) -> tuple[set[str], str]:
        canonical = url.split("|")[0]
        if canonical in seen:
            return set(), "cycle"
        choices = value_sets.get(canonical, [])
        if not choices:
            return set(), "external_valueset"
        _, _, resource = next((item for item in choices if item[0] == package), choices[0])
        systems: set[str] = set()
        status = "resolved"
        for include in (resource.get("compose") or {}).get("include", []):
            if include.get("system"):
                systems.add(include["system"])
            for nested in include.get("valueSet", []):
                more, nested_status = resolve_value_set(nested, package, seen | {canonical})
                systems.update(more)
                if nested_status != "resolved":
                    status = "partial"
        if not systems:

            def walk_contains(nodes: list[dict[str, Any]]) -> None:
                for node in nodes:
                    if node.get("system"):
                        systems.add(node["system"])
                    walk_contains(node.get("contains") or [])

            walk_contains((resource.get("expansion") or {}).get("contains") or [])
        return systems, status if systems else "no_system_in_package"

    bindings: list[dict[str, Any]] = []
    fixed_systems: list[dict[str, Any]] = []
    valueset_rows: list[dict[str, Any]] = []
    system_sources: dict[str, set[str]] = defaultdict(set)
    system_profiles: dict[str, set[str]] = defaultdict(set)
    for package, version, resource in artifacts:
        kind = resource["resourceType"]
        if kind == "CodeSystem" and resource.get("url"):
            system_sources[resource["url"]].add(f"{package}#{version}:CodeSystem")
        if kind == "ValueSet":
            include = (resource.get("compose") or {}).get("include", [])
            exclude = (resource.get("compose") or {}).get("exclude", [])
            systems, status = resolve_value_set(resource.get("url", ""), package)
            valueset_rows.append(
                {
                    "package": package,
                    "version": version,
                    "valueset": resource.get("url", ""),
                    "title": resource.get("title") or resource.get("name") or "",
                    "include_systems": ";".join(sorted({x["system"] for x in include if x.get("system")})),
                    "exclude_systems": ";".join(sorted({x["system"] for x in exclude if x.get("system")})),
                    "nested_valuesets": ";".join(sorted({v for x in include for v in x.get("valueSet", [])})),
                    "resolved_systems": ";".join(sorted(systems)),
                    "resolution": status,
                }
            )
            for system in systems:
                system_sources[system].add(f"{package}#{version}:ValueSet")
        if kind != "StructureDefinition":
            continue
        profile = resource.get("url", "")
        differential_ids = {e.get("id") for e in (resource.get("differential") or {}).get("element", [])}
        elements = (resource.get("snapshot") or {}).get("element") or (
            resource.get("differential") or {}
        ).get("element", [])
        for element in elements:
            path = element.get("path", "")
            binding = element.get("binding") or {}
            value_set = binding.get("valueSet", "")
            if value_set:
                systems, status = resolve_value_set(value_set, package)
                bindings.append(
                    {
                        "package": package,
                        "version": version,
                        "resource_type": resource.get("type", ""),
                        "profile": profile,
                        "element": element.get("id") or path,
                        "binding_origin": "declared"
                        if element.get("id") in differential_ids
                        else "inherited",
                        "strength": binding.get("strength", ""),
                        "valueset": value_set,
                        "systems": ";".join(sorted(systems)),
                        "resolution": status,
                    }
                )
                for system in systems:
                    system_sources[system].add(f"{package}#{version}:binding")
                    system_profiles[system].add(profile)
            for field in ("fixedCoding", "patternCoding"):
                coding = element.get(field) or {}
                if coding.get("system"):
                    system = coding["system"]
                    fixed_systems.append(
                        {
                            "package": package,
                            "version": version,
                            "resource_type": resource.get("type", ""),
                            "profile": profile,
                            "element": element.get("id") or path,
                            "constraint": field,
                            "system": system,
                        }
                    )
                    system_sources[system].add(f"{package}#{version}:{field}")
                    system_profiles[system].add(profile)
            if path.endswith(".system"):
                for field in ("fixedUri", "patternUri", "fixedCanonical", "patternCanonical"):
                    system = element.get(field)
                    if system:
                        fixed_systems.append(
                            {
                                "package": package,
                                "version": version,
                                "resource_type": resource.get("type", ""),
                                "profile": profile,
                                "element": element.get("id") or path,
                                "constraint": field,
                                "system": system,
                            }
                        )
                        system_sources[system].add(f"{package}#{version}:{field}")
                        system_profiles[system].add(profile)

    systems_rows = [
        {
            "system": system,
            "label": KNOWN_LABELS.get(system, ""),
            "profile_count": len(system_profiles[system]),
            "profiles": ";".join(sorted(system_profiles[system])),
            "sources": ";".join(sorted(system_sources[system])),
        }
        for system in sorted(system_sources)
    ]
    _write(
        output_dir / "packages.csv",
        package_rows,
        ["package", "version", "status", "source", "artifact_count"],
    )
    _write(
        output_dir / "bindings.csv",
        bindings,
        [
            "package",
            "version",
            "resource_type",
            "profile",
            "element",
            "binding_origin",
            "strength",
            "valueset",
            "systems",
            "resolution",
        ],
    )
    for name, types in (
        ("observation", {"Observation", "DiagnosticReport", "Specimen"}),
        (
            "medication",
            {"Medication", "MedicationRequest", "MedicationStatement", "MedicationAdministration"},
        ),
    ):
        selected = [
            row
            for row in bindings
            if row["resource_type"] in types
            and row["binding_origin"] == "declared"
            and ".kerndatensatz." in row["package"]
        ]
        _write(
            output_dir / f"{name}_bindings.csv",
            selected,
            [
                "package",
                "version",
                "resource_type",
                "profile",
                "element",
                "binding_origin",
                "strength",
                "valueset",
                "systems",
                "resolution",
            ],
        )
    _write(
        output_dir / "valuesets.csv",
        valueset_rows,
        [
            "package",
            "version",
            "valueset",
            "title",
            "include_systems",
            "exclude_systems",
            "nested_valuesets",
            "resolved_systems",
            "resolution",
        ],
    )
    _write(
        output_dir / "fixed_systems.csv",
        fixed_systems,
        ["package", "version", "resource_type", "profile", "element", "constraint", "system"],
    )
    _write(
        output_dir / "systems.csv", systems_rows, ["system", "label", "profile_count", "profiles", "sources"]
    )
    return {
        "packages": len(package_rows),
        "loaded": sum(r["status"] == "loaded" for r in package_rows),
        "bindings": len(bindings),
        "valuesets": len(valueset_rows),
        "fixed_systems": len(fixed_systems),
        "systems": len(systems_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract terminology bindings from pinned MII FHIR packages")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("packages.json"))
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("generated"))
    args = parser.parse_args()
    print(json.dumps(generate(args.manifest, args.packages, args.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
