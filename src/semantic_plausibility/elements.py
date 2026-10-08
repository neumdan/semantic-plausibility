"""Explicit scalar element selectors for laboratory exports."""

from __future__ import annotations
import re
from typing import Any

PATH_TOKEN = re.compile(r"([A-Za-z][A-Za-z0-9]*)(?:\[(\d+)\])?\Z")


def set_fhir_path(resource: dict, path: str, value: Any) -> None:
    """Populate an explicitly mapped scalar FHIR path; never guess array flattening."""
    if isinstance(value, (list, dict)):
        raise ValueError(
            "FHIR path mapping encountered arrays/objects; use full resource_json or a curated scalar view"
        )
    if value is None:
        return
    parts = path.split(".")
    if parts[0] == "resourceType":
        raise ValueError("Mapped fields must not override resourceType")
    node = resource
    for i, part in enumerate(parts):
        match = PATH_TOKEN.fullmatch(part)
        if not match or len(parts) > 20:
            raise ValueError("Invalid mapped FHIR path")
        name, index = match.groups()
        if index is not None:
            index = int(index)
            if index > 49:
                raise ValueError("Mapped FHIR array index exceeds the budget")
            values = node.setdefault(name, [])
            if not isinstance(values, list):
                raise ValueError("Conflicting mapped FHIR paths")
            while len(values) <= index:
                values.append({})
            if i == len(parts) - 1:
                values[index] = value
            else:
                node = values[index]
        elif i == len(parts) - 1:
            node[name] = value
        else:
            node = node.setdefault(name, {})
        if i < len(parts) - 1 and not isinstance(node, dict):
            raise ValueError("Conflicting mapped FHIR paths")
