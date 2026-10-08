"""Fixed extraction schemas and source guards; no general JSON Schema engine."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from . import research_prompts as prompts


def _text(limit: int, *, nullable: bool = False, nonempty: bool = False) -> dict[str, Any]:
    return {
        "type": ["string", "null"] if nullable else "string",
        "minLength": int(nonempty),
        "maxLength": limit,
    }


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items: dict[str, Any], limit: int) -> dict[str, Any]:
    return {"type": "array", "items": items, "maxItems": limit}


_BOUND = {
    **_text(80, nullable=True, nonempty=True),
    "pattern": r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$",
}
_CONTEXT = {name: _text(512) for name in ("population", "specimen", "method")}

SCHEMAS = {
    prompts.UNIT_SPELLING: _object({
        "selected_unit": _text(80, nullable=True, nonempty=True), "reason": _text(1000),
    }),
    prompts.LOINC_UNITS: _object({
        "units": _array(_object({"unit": _text(80, nonempty=True), "excerpt": _text(4500, nonempty=True)}), 15),
    }),
    prompts.LOINC_RANGES: _object({
        "ranges": _array(_object({
            "evidence_type": {"type": "string", "enum": ["reference_interval", "analytical_interval", "documented_extreme"]},
            "low": _BOUND, "high": _BOUND, "unit": _text(80, nonempty=True),
            **_CONTEXT, "excerpt": _text(6000, nonempty=True),
        }), 8),
    }),
    prompts.UNIT_CANDIDATES: _object({
        "units": _array(_object({"unit": _text(80, nonempty=True), "reason": _text(1000)}), 3),
    }),
    prompts.STUDY_BOUNDS: _object({
        "low": _BOUND, "high": _BOUND, "unit": _text(80, nullable=True, nonempty=True),
        **_CONTEXT, "excerpt": _text(6000),
    }),
    prompts.TERMINOLOGY_CLAIMS: _object({
        "claims": _array(_object({"claim": _text(300, nonempty=True), "excerpt": _text(1200, nonempty=True)}), 10),
    }),
}


def response_schema(instruction: str) -> dict[str, Any]:
    try:
        return SCHEMAS[instruction]
    except KeyError:
        raise ValueError("Model task has no registered response schema") from None


def validate_response(value: Any, schema: dict[str, Any], path: str = "response") -> None:
    """Validate only the keywords used by our fixed, application-owned schemas."""
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    matches = {
        "null": value is None, "string": isinstance(value, str),
        "object": isinstance(value, dict), "array": isinstance(value, list),
    }
    if not any(matches[item] for item in kinds):
        raise ValueError(f"Model schema mismatch at {path}: expected {'/'.join(kinds)}")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Model schema mismatch at {path}: unsupported category")
    if isinstance(value, dict):
        properties = schema["properties"]
        if set(value) != set(schema["required"]):
            raise ValueError(f"Model schema mismatch at {path}: missing or additional fields")
        for name, child in value.items():
            validate_response(child, properties[name], f"{path}.{name}")
    elif isinstance(value, list):
        if len(value) > schema["maxItems"]:
            raise ValueError(f"Model schema mismatch at {path}: too many items")
        for index, child in enumerate(value):
            validate_response(child, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 6000):
            raise ValueError(f"Model schema mismatch at {path}: invalid text length")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"Model schema mismatch at {path}: invalid numeric bound")


def explicit_token(token: str, excerpt: str) -> bool:
    """Reject a claimed unit/number appearing only inside a longer token."""
    try:
        Decimal(token)
        start, end = r"(?<![A-Za-z0-9.])", r"(?![A-Za-z0-9]|\.[0-9])"
    except InvalidOperation:
        start = r"(?<![A-Za-z0-9./_*^\[\]])"
        end = r"(?![A-Za-z0-9/_*^\[\]]|\.[A-Za-z0-9])"
    return bool(re.search(start + re.escape(token) + end, excerpt))


def _range_guard(row: dict[str, Any], source: str) -> None:
    excerpt = row["excerpt"]
    if excerpt and excerpt not in source:
        raise ValueError("Model returned a quotation absent from the supplied source")
    numbers = []
    for name in ("low", "high"):
        bound = row[name]
        if bound is not None:
            number = Decimal(bound)
            if not number.is_finite() or not excerpt or not explicit_token(bound, excerpt):
                raise ValueError("Model returned a numeric bound without an exact source token")
            numbers.append(number)
    if len(numbers) == 2 and numbers[0] > numbers[1]:
        raise ValueError("Model returned reversed interval bounds")
    unit = row["unit"]
    if unit is not None and (not excerpt or not explicit_token(unit, excerpt)):
        raise ValueError("Model returned a unit absent from its quotation")
    if numbers and not unit:
        raise ValueError("Model returned numeric bounds without a quoted unit")
    for name in _CONTEXT:
        label = row[name]
        if label and label.casefold() not in source.casefold():
            raise ValueError(f"Model returned ungrounded context: {name}")


def validate_grounding(instruction: str, value: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Source presence is necessary; it does not establish clinical applicability."""
    if instruction == prompts.UNIT_SPELLING:
        allowed = {row["unit"] for row in evidence.get("candidates", []) if row.get("validation") == "valid"}
        if value["selected_unit"] is not None and value["selected_unit"] not in allowed:
            raise ValueError("Model selected a spelling outside the validated unit catalogue shortlist")
    elif instruction == prompts.LOINC_UNITS:
        source = evidence.get("page_excerpt", "")
        for row in value["units"]:
            if row["excerpt"] not in source or not explicit_token(row["unit"], row["excerpt"]):
                raise ValueError("Model returned a unit without an exact source quotation")
    elif instruction == prompts.LOINC_RANGES:
        for row in value["ranges"]:
            _range_guard(row, evidence.get("page_excerpt", ""))
    elif instruction == prompts.STUDY_BOUNDS:
        _range_guard(value, evidence.get("abstract", ""))
    elif instruction == prompts.UNIT_CANDIDATES:
        documented = {row["unit"] for row in evidence.get("documented_examples", [])}
        if any(row["unit"] not in documented for row in value["units"]):
            raise ValueError("Model suggested an undocumented unit")
    elif instruction == prompts.TERMINOLOGY_CLAIMS:
        excerpts = evidence.get("excerpts", [])
        for row in value["claims"]:
            if not any(row["excerpt"] in source for source in excerpts) or row["claim"] not in row["excerpt"]:
                raise ValueError("Model returned a statement without an exact source quotation")
