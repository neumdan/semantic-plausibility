"""Fixed extraction schemas and source guards; no general JSON Schema engine."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from copy import deepcopy
import re
from typing import Any

from . import research_prompts as prompts
from .study_sources import (analyte_clauses, source_unit, quantity_exclusion, ambiguous_analyte_clause,
                            ANALYTICAL_LIMITS, analytical_limit_matches, statistic_context, unit_tokens)


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
_CONTEXT_MARKERS = {
    "population": r"\b(?:participants?|subjects?|volunteers?|patients?|adults?|children|newborns?|neonates?|cohort)\b",
    "specimen": r"\b(?:serum|plasma|urine|whole blood|blood samples?|blood specimens?|cerebrospinal fluid)\b",
    "method": r"\b(?:assay|analy[sz]er|chromatography|spectrometry|spectrophotometry|immunoassay|HPLC|LC-MS|flow cytometry|measurement method)\b",
}

SCHEMAS = {
    prompts.NAME_QUERIES: _object({"queries": _array(_text(120, nonempty=True), 2)}),
    prompts.LOINC_REVIEW: _object({
        "selected_code": _text(20, nullable=True, nonempty=True),
        "selected_name": _text(300, nullable=True, nonempty=True),
        "reason": _text(1000),
    }),
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
        "evidence_type": {"type": ["string", "null"], "enum": [None, "reference_interval", "analytical_interval", "documented_extreme"]},
        "low": _BOUND, "high": _BOUND, "unit": _text(80, nullable=True, nonempty=True),
        **_CONTEXT, "excerpt": _text(6000),
    }),
    prompts.STUDY_MEASUREMENTS: _object({"measurements": _array(_object({
        "reported_value": _BOUND,
        "value_kind": {"type": "string", "enum": ["individual_value", "mean", "sd", "min", "max", "median", "iqr", "summary_statistic", "unit_only", "unspecified", *ANALYTICAL_LIMITS]},
        "unit": _text(80, nonempty=True), **_CONTEXT, "excerpt": _text(6000, nonempty=True),
    }), 3)}),
    prompts.TERMINOLOGY_CLAIMS: _object({
        "claims": _array(_object({"claim": _text(300, nonempty=True), "excerpt": _text(1200, nonempty=True)}), 10),
    }),
}

SCHEMAS[prompts.STUDY_EXTRACT] = _object({"bounds": SCHEMAS[prompts.STUDY_BOUNDS], "measurements": {**SCHEMAS[prompts.STUDY_MEASUREMENTS]["properties"]["measurements"], "maxItems": 4}})


def response_schema(instruction: str) -> dict[str, Any]:
    try:
        return SCHEMAS[instruction]
    except KeyError:
        raise ValueError("Model task has no registered response schema") from None


def evidence_schema(instruction: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Constrain study quotations to actual source sentences, including abstention."""
    if instruction == prompts.LOINC_REVIEW:
        schema = deepcopy(response_schema(instruction))
        rows = evidence.get("candidates", [])
        allowed = [row for row in rows if row.get("selectable") and sum(other["code"] == row["code"] for other in rows) == 1]
        schema["properties"]["selected_code"]["enum"] = [None] + [row["code"] for row in allowed]
        schema["properties"]["selected_name"]["enum"] = [None] + list(dict.fromkeys(name for row in allowed for name in row["names"]))
        return schema
    if instruction == prompts.STUDY_EXTRACT:
        schema = deepcopy(response_schema(instruction))
        sentences = source_sentences(evidence)
        for index, properties in enumerate((schema["properties"]["bounds"]["properties"], schema["properties"]["measurements"]["items"]["properties"])):
            properties["excerpt"] = {"type": "string", "enum": ([""] if index == 0 else []) + list(sentences)}
            for field in _CONTEXT:
                properties[field] = {"type": "string", "enum": [""] + [key for key, text in sentences.items()
                    if len(text) <= 512 and re.search(_CONTEXT_MARKERS[field], text, re.I)]}
        if not sentences:
            schema["properties"]["measurements"]["maxItems"] = 0
            schema["properties"]["measurements"]["items"]["properties"]["excerpt"] = _text(6000, nonempty=True)
        intervals = printed_reference_intervals(evidence)
        if intervals:
            schema["properties"]["bounds"] = {"type": "string", "enum": ["", *intervals]}
        return schema
    schema = deepcopy(response_schema(instruction))
    excerpts = evidence.get("excerpt_candidates")
    if excerpts and instruction in {prompts.STUDY_BOUNDS, prompts.STUDY_MEASUREMENTS}:
        target = (schema["properties"]["excerpt"] if instruction == prompts.STUDY_BOUNDS
                  else schema["properties"]["measurements"]["items"]["properties"]["excerpt"])
        target["enum"] = ([""] if instruction == prompts.STUDY_BOUNDS else []) + excerpts
        properties = (schema["properties"] if instruction == prompts.STUDY_BOUNDS
                      else schema["properties"]["measurements"]["items"]["properties"])
        for name in _CONTEXT:
            properties[name]["enum"] = [""] + [part for part in excerpts if len(part) <= 512]
    return schema


def source_sentences(evidence):
    return {"s" + str(i + 1): text for i, text in enumerate(evidence.get("excerpt_candidates", []))}


def printed_reference_intervals(evidence):
    """Literal sex-stratified ranges: offer whole pairs, never independent extrema."""
    aliases = evidence.get("analyte_definition", {}).get("aliases", [])
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
    pattern = r"\b(?:male|female|men|women|boys|girls)\b\s*:?\s*\(\s*(" + number + r")\s*[-–−—]\s*(" + number + r")\s*\)"
    result = {}
    for sentence in source_sentences(evidence).values():
        if not supports_evidence_type("reference_interval", sentence, evidence.get("abstract", "")):
            continue
        for clause in analyte_clauses(sentence, aliases):
            units = unit_tokens(clause)
            if len(units) != 1:
                continue
            for match in re.finditer(pattern, clause, re.I):
                result["i" + str(len(result) + 1)] = {"evidence_type": "reference_interval",
                    "low": match[1], "high": match[2], "unit": units[0], "excerpt": sentence,
                    "population": match[0], "specimen": "", "method": ""}
                if len(result) == 8:
                    return result
    return result


def model_evidence(instruction, evidence):
    if instruction != prompts.STUDY_EXTRACT:
        return evidence
    sentences = source_sentences(evidence)
    intervals = printed_reference_intervals(evidence)
    choices = {key: {**row, "excerpt": next(k for k, text in sentences.items() if text == row["excerpt"])}
               for key, row in intervals.items()}
    cells = {key: {name: cell[name] for name in ("measurements", "bounds")}
             for key, sentence in sentences.items()
             if (cell := evidence.get("table_cells", {}).get(sentence))}
    return {**{k: v for k, v in evidence.items() if k not in {"abstract", "excerpt_candidates", "table_cells", "source_locations"}},
            "sentences": sentences, **({"table_cells": cells} if cells else {}),
            **({"printed_bounds": choices} if choices else {})}


def resolve_sentences(value, evidence):
    """Resolve only enumerated source IDs, then subject restored quotations to normal guards."""
    result = deepcopy(value)
    sentences = {"": "", **source_sentences(evidence)}
    rows = list(result["measurements"])
    if isinstance(result["bounds"], str):
        choices = {"": {**dict.fromkeys(("evidence_type", "low", "high", "unit")),
                        **dict.fromkeys((*_CONTEXT, "excerpt"), "")}, **printed_reference_intervals(evidence)}
        if result["bounds"] not in choices:
            raise ValueError("Model returned an unknown printed interval ID")
        result["bounds"] = choices[result["bounds"]]
    else:
        rows.insert(0, result["bounds"])
    for row in rows:
        for field in ("excerpt", *_CONTEXT):
            try:
                row[field] = sentences[row[field]]
            except KeyError:
                raise ValueError("Model returned an unknown source sentence ID") from None
    return result


def ground_study_units(value):
    """Restore source spelling for unambiguous typography; preserve the raw reply in trace."""
    result = deepcopy(value)
    changes = []
    for row in [result["bounds"], *result["measurements"]]:
        claimed = row.get("unit")
        if claimed:
            literal = source_unit(claimed, row["excerpt"])
            if literal and literal != claimed:
                changes.append({"model_unit": claimed, "source_unit": literal,
                                "rule": "typography_only_no_scale_conversion"})
                row["unit"] = literal
    return result, changes


def _analyte_guard(row, evidence, tokens, *, bounds=False):
    definition = evidence.get("analyte_definition", {})
    aliases = definition.get("aliases") or [definition.get("component", "")]
    cell = evidence.get("table_cells", {}).get(row["excerpt"])
    if cell is not None:
        from .study_sources import mentions_analyte
        if not mentions_analyte(cell["row_label"] + " " + cell.get("alias_definition", ""), aliases) or row["unit"] != cell["unit"]:
            raise ValueError("Table evidence must match its analyte row and explicit unit")
        if quantity_exclusion(row["excerpt"], row["unit"], definition.get("property", ""), bounds=bounds):
            raise ValueError("Table evidence is not a compatible measured analyte quantity")
        if bounds:
            allowed = cell.get("bounds")
            if not allowed or any(row[k] != allowed[k] for k in ("evidence_type", "low", "high")):
                raise ValueError("Table interval must match one explicit cell and its headers")
        elif [row["value_kind"], row["reported_value"]] not in [list(v) for v in cell["measurements"]]:
            raise ValueError("Table statistic must match its labelled cell; CI/counts are not measurements")
        return [row["excerpt"]]
    clauses = analyte_clauses(row["excerpt"], aliases) if any(aliases) else [row["excerpt"]]
    matches = [clause for clause in clauses if all(explicit_token(token, clause) for token in tokens if token is not None)]
    if not matches:
        raise ValueError("Study evidence numbers/unit are not in a clause naming the verified analyte")
    eligible = [clause for clause in matches if not quantity_exclusion(
        clause, row.get("unit") or "", definition.get("property", ""), bounds=bounds,
        analytical=row.get("value_kind") in ANALYTICAL_LIMITS)]
    if not eligible:
        raise ValueError("Study evidence is a difference, interference, analytical limit, decision threshold or incompatible percentage; its category must match the source")
    eligible = [clause for clause in eligible if not ambiguous_analyte_clause(clause, aliases)]
    if not eligible:
        raise ValueError("Study evidence combines named analytes; number/unit association is unresolved")
    if not bounds and any(re.search(r"[<>≤≥]=?\s*" + re.escape(token) + r"(?![\d.])", clause)
                          for token in tokens if token and token != row.get("unit") for clause in eligible):
        raise ValueError("Study evidence omits a comparator; a threshold/censored value is not an exact measurement")
    return eligible


def guard_study_parts(value, evidence):
    """Check each sourced claim independently; preserve rejected raw claims in trace."""
    result = deepcopy(value)
    rejected = []
    try:
        validate_grounding(prompts.STUDY_BOUNDS, result["bounds"], evidence)
    except ValueError as exc:
        rejected.append({"part": "bounds", "reason": str(exc)})
        result["bounds"] = {**dict.fromkeys(("evidence_type", "low", "high", "unit")),
                            **dict.fromkeys((*_CONTEXT, "excerpt"), "")}
    result["measurements"] = []
    seen = set()
    for index, row in enumerate(value["measurements"], 1):
        try:
            validate_grounding(prompts.STUDY_MEASUREMENTS, {"measurements": [row]}, evidence)
            key = tuple(row[k] for k in ("reported_value", "value_kind", "unit", "excerpt"))
            if key in seen:
                raise ValueError("Study evidence duplicates an already accepted statistic")
            seen.add(key)
            result["measurements"].append(row)
        except ValueError as exc:
            rejected.append({"part": f"measurement_{index}", "reason": str(exc)})
    return result, rejected


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
    if instruction == prompts.STUDY_EXTRACT:
        validate_grounding(prompts.STUDY_BOUNDS, value["bounds"], evidence)
        validate_grounding(prompts.STUDY_MEASUREMENTS, {"measurements": value["measurements"]}, evidence)
    elif instruction == prompts.NAME_QUERIES:
        if any(re.search(r"[\r\n<>]|\b\d+-\d\b|https?://", query) for query in value["queries"]):
            raise ValueError("Name queries must be short terminology search phrases, not codes or URLs")
    elif instruction == prompts.LOINC_REVIEW:
        rows = evidence.get("candidates", [])
        allowed = {row["code"]: row for row in rows if row.get("selectable")
                   and sum(other["code"] == row["code"] for other in rows) == 1}
        code, name = value["selected_code"], value["selected_name"]
        if code is None and name is not None or code is not None and code not in allowed:
            raise ValueError("Model selected an unverified or conflicting LOINC candidate")
        if code is not None and name is not None and name not in allowed[code]["names"]:
            raise ValueError("Model proposed a name absent from the selected code's sourced aliases")
    elif instruction == prompts.UNIT_SPELLING:
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
        if value["low"] is not None or value["high"] is not None:
            clauses = _analyte_guard(value, evidence, [value["low"], value["high"], value["unit"]], bounds=True)
            if value["low"] is not None and value["high"] is not None:
                pair = (r"(?<![\d.])" + re.escape(value["low"]) + r"\s*(?:[-–−—]|to|and)\s*"
                        + re.escape(value["high"]) + r"(?![\d.])")
                if not any(re.search(pair, clause, re.I) for clause in clauses):
                    raise ValueError("Study bounds must be one printed interval; do not mix population strata")
        kind = value["evidence_type"]
        if (value["low"] is not None or value["high"] is not None) and kind is None:
            raise ValueError("Study bounds need an explicit evidence type, not a search-query category")
        if kind is not None and not evidence.get("table_cells", {}).get(value["excerpt"]) and not supports_evidence_type(kind, value["excerpt"], evidence.get("abstract", "")):
            raise ValueError("Study evidence category is not explicitly supported by its quotation")
    elif instruction == prompts.STUDY_MEASUREMENTS:
        for row in value["measurements"]:
            _range_guard({**row, "low": row["reported_value"], "high": None}, evidence.get("abstract", ""))
            clauses = _analyte_guard(row, evidence, [row["reported_value"], row["unit"]])
            if row["value_kind"] in ANALYTICAL_LIMITS and (row["reported_value"] is None or not any(
                analytical_limit_matches(clause, row["value_kind"], row["reported_value"], row["unit"])
                for clause in clauses)):
                raise ValueError("Analytical limit label, value and unit must have an explicit source association")
            markers = {"mean": r"\bmean\b|average", "sd": r"\bSD\b|standard deviation", "min": r"minimum|lowest|\bmin\b", "max": r"maximum|highest|\bmax\b", "median": r"median", "iqr": r"interquartile|\bIQR\b"}
            definition = evidence.get("analyte_definition", {})
            contexts = [statistic_context(clause, definition.get("aliases") or [definition.get("component", "")])
                        for clause in clauses]
            if row["value_kind"] in markers and not any(re.search(markers[row["value_kind"]], clause, re.I) for clause in contexts):
                raise ValueError("Study evidence statistic role is not explicitly labelled")
            if row["value_kind"] == "individual_value" and any(re.search(
                r"\bmean\b|\bmedian\b|standard deviation|\bSD\b|\bIQR\b|interquartile", clause, re.I) for clause in contexts):
                raise ValueError("Study evidence labels a population statistic as an individual value")
            if row["value_kind"] == "individual_value" and supports_evidence_type(
                "reference_interval", row["excerpt"], evidence.get("abstract", "")):
                raise ValueError("Study evidence labels a reference interval as an individual value")
            if row["value_kind"] == "unit_only" and row["reported_value"] is not None:
                raise ValueError("A unit-only excerpt must not supply a measured value")
    elif instruction == prompts.UNIT_CANDIDATES:
        documented = {row["unit"] for row in evidence.get("documented_examples", [])}
        if any(row["unit"] not in documented for row in value["units"]):
            raise ValueError("Model suggested an undocumented unit")
    elif instruction == prompts.TERMINOLOGY_CLAIMS:
        excerpts = evidence.get("excerpts", [])
        for row in value["claims"]:
            if not any(row["excerpt"] in source for source in excerpts) or row["claim"] not in row["excerpt"]:
                raise ValueError("Model returned a statement without an exact source quotation")


def source_defined_reference_abbreviation(excerpt, source):
    """Accept only a reference-range abbreviation explicitly defined in this source."""
    aliases = re.findall(r"(?:reference|normal)\s+(?:intervals?|ranges?)\s*\(\s*([A-Za-z]{1,8})\s*\)", source, re.I)
    return any(explicit_token(alias, excerpt) for alias in aliases)


def supports_evidence_type(kind, excerpt, source):
    """Use the same category guard during extraction and candidate projection."""
    if kind == "documented_extreme" and re.search(
        r"\b(?:would|could|hypothetical|simulat\w*|propos\w*)\b|for example", excerpt, re.I):
        return False
    markers = {"reference_interval": r"(?:reference|normal)\s+(?:intervals?|ranges?)",
               "analytical_interval": r"analytical|reportable|measuring\s+(?:interval|range)",
               "documented_extreme": r"case\s+report|extreme|highest|lowest|maximum|minimum"}
    return bool(kind in markers and (re.search(markers[kind], excerpt, re.I)
        or kind == "reference_interval" and source_defined_reference_abbreviation(excerpt, source)))
