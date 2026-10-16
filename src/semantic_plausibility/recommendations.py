"""Explicit transformation plans and local comparisons; never clinical approval."""

from decimal import Decimal
from typing import Any

from .fhir_quality_pipeline import number, serial

UCUM = "http://unitsofmeasure.org"
VERIFIED = {"correct", "normalization_verified", "conversion_verified"}


def transformation(mapping: dict, quantity: dict, ranges: list, candidate: str, pipeline: Any) -> dict:
    source = quantity.get("code") or quantity.get("unit") or ""
    target = mapping.get("target") if mapping.get("class") in VERIFIED else candidate
    factor = number(mapping.get("factor")) if mapping.get("class") in VERIFIED else None
    operations = []

    def operation(path, source_unit, scale, raw_value=None):
        scale = scale if scale is not None and scale > 0 else None
        return {
            "path": path,
            "source_unit": source_unit,
            "target_unit": target or None,
            "state": "verified_linear" if scale is not None else "unresolved",
            "factor": serial(scale),
            "offset": "0" if scale is not None else None,
            "formula": f"y = x * {serial(scale)}" if scale is not None else None,
            "converted_bound": serial(number(raw_value) * scale)
            if number(raw_value) is not None and scale is not None
            else None,
        }

    operations.append(operation("valueQuantity", source, factor))
    for index, interval in enumerate(ranges):
        for side in ("low", "high"):
            bound = interval.get(side)
            if not isinstance(bound, dict):
                continue
            bound_unit = bound.get("code") or bound.get("unit") or source
            bound_factor = None
            declared = bool(bound.get("code") or bound.get("unit"))
            eligible = not bound.get("comparator") and (
                not declared
                and bound.get("system") in {None, "", UCUM}
                or bound.get("code")
                and bound.get("system") == UCUM
            )
            if factor is not None and eligible:
                try:
                    # Each bound has its own unit: never multiply an already converted bound twice.
                    if bound_unit == source:
                        bound_factor = factor
                    elif pipeline.ucum.is_valid(bound_unit):
                        bound_factor = (
                            Decimal(1) if bound_unit == target else pipeline._factor(bound_unit, target)
                        )
                except Exception:
                    pass
            operations.append(
                operation(f"referenceRange[{index}].{side}", bound_unit, bound_factor, bound.get("value"))
            )
    return {
        "state": "verified_linear"
        if factor is not None and all(op["state"] == "verified_linear" for op in operations)
        else "unresolved",
        "source_unit": source,
        "target_unit": target or None,
        "factor": serial(factor),
        "offset": "0" if factor is not None else None,
        "formula": operations[0]["formula"],
        "operations": operations,
        "local_mapping_approved": bool(mapping.get("approved")),
        "approval_required": mapping.get("class") != "correct" and not mapping.get("approved"),
        "kind": "identity"
        if mapping.get("class") == "correct"
        else "normalization"
        if mapping.get("class") == "normalization_verified"
        else "linear_conversion"
        if mapping.get("class") == "conversion_verified"
        else "candidate_only",
        "reason": "Verified linear factors; review code/context and each reference bound before application"
        if factor is not None
        else "Candidate only; no verified executable transformation",
    }


def _relation(value, low, high):
    if value is None:
        return None
    return (
        "below"
        if low is not None and value < low
        else "above"
        if high is not None and value > high
        else "inside"
    )


def _extrema(stats, low, high):
    minimum, maximum = number(stats.get("minimum")), number(stats.get("maximum"))
    return {
        "minimum_relation": _relation(minimum, low, high),
        "maximum_relation": _relation(maximum, low, high),
    }


def value_checks(item: dict, ranges: list, stats: dict, quantity: dict, pipeline: Any) -> dict:
    local = []
    exact_values = not quantity.get("comparator") and not stats.get("invalid")
    for interval in ranges:
        bounds_ok = interval["state"] == "bounds_valid" and exact_values
        relations = (
            _extrema(stats, number(interval.get("low")), number(interval.get("high"))) if bounds_ok else {}
        )
        local.append(
            {
                "index": interval["index"],
                **relations,
                "state": "outside_reported_range"
                if any(v in {"below", "above"} for v in relations.values())
                else "inside_reported_range"
                if relations.get("minimum_relation")
                else "unresolved",
                "population_qualified": interval["population_qualified"],
                "clinical_classification": "not_assessed",
            }
        )
    evidence = []
    for candidate in item.get("study_candidates", []):
        kind = candidate.get("evidence_type")
        if kind not in {"reference_interval", "analytical_interval", "documented_extreme"}:
            continue
        source_unit = candidate.get("unit_ucum") if candidate.get("unit_validation_state") == "valid" else candidate.get("unit")
        low, high = number(candidate.get("low")), number(candidate.get("high"))
        entry = {
            "source": candidate.get("source"),
            "evidence_type": kind,
            "population": candidate.get("population", ""),
            "specimen": candidate.get("specimen", ""),
            "method": candidate.get("method", ""),
            "context_verified": False,
            "state": "unresolved",
            "reference_relation": "not_comparable",
        }
        scale = None
        if item.get("ucum_valid") and source_unit and (low is not None or high is not None):
            try:
                if pipeline.ucum.is_valid(source_unit):
                    scale = (
                        Decimal(1)
                        if source_unit == item["source_unit"]
                        else pipeline._factor(source_unit, item["source_unit"])
                    )
            except Exception:
                pass
        if scale is not None and not (low is not None and high is not None and low > high):
            low = low * scale if low is not None else None
            high = high * scale if high is not None else None
            relations = _extrema(stats, low, high) if exact_values else {}
            outside = any(v in {"below", "above"} for v in relations.values())
            entry.update(
                {
                    **relations,
                    "low": serial(low),
                    "high": serial(high),
                    "unit": item["source_unit"],
                    "state": "outside_documented_example"
                    if outside and kind == "documented_extreme"
                    else "potential_implausibility_context_unverified"
                    if outside and kind == "analytical_interval"
                    else "outside_candidate_reference_context_unverified"
                    if outside
                    else "within_candidate_bounds_context_unverified"
                    if relations.get("minimum_relation")
                    else "unresolved",
                }
            )
            if kind == "reference_interval":
                comparisons = []
                for interval in ranges:
                    if interval["state"] != "bounds_valid":
                        continue
                    a, b = number(interval.get("low")), number(interval.get("high"))
                    disjoint = (
                        a is not None
                        and high is not None
                        and a > high
                        or b is not None
                        and low is not None
                        and b < low
                    )
                    comparisons.append(
                        {
                            "index": interval["index"],
                            "relation": "disjoint_context_unverified"
                            if disjoint
                            else "different_bounds_context_unverified"
                            if a != low or b != high
                            else "same_numeric_bounds_context_unverified",
                        }
                    )
                entry["local_reference_comparisons"] = comparisons
                entry["reference_relation"] = ";".join(c["relation"] for c in comparisons) or "not_comparable"
        evidence.append(entry)
    return {
        "local_reference": local,
        "evidence": evidence,
        "scope": "Local extrema against reported/candidate bounds; no patient population selection or clinical approval",
    }


def source_reference_checks(payload, stats, unit_check):
    """Compare numbers in an exact verified source unit, independent of target IG rules."""
    q = payload["quantity"]
    code, system = q.get("code"), q.get("system")
    results = []
    for index, interval in enumerate(payload.get("reference_ranges", [])):
        comparable = bool(code and system and unit_check.get("state") == "valid"
                          and not q.get("comparator") and not stats.get("invalid"))
        bounds = {}
        for side in ("low", "high"):
            bound = interval.get(side)
            bounds[side] = number(bound.get("value")) if isinstance(bound, dict) else None
            if isinstance(bound, dict):
                # Omitted bound unit inherits only the declared measurement unit.
                declared = bound.get("code") or bound.get("unit")
                comparable &= not bound.get("comparator") and (not declared or declared == code) and bound.get("system", system) == system
                comparable &= bounds[side] is not None
        low, high = bounds["low"], bounds["high"]
        ordered = (low is not None or high is not None) and not (low is not None and high is not None and low > high)
        results.append({"index": index, "state": "numeric_bounds_valid_in_source_units" if comparable and ordered else "unresolved",
            "unit_code": code, "unit_system": system, "context_qualified": bool(interval.get("age") or interval.get("appliesTo")),
            **(_extrema(stats, low, high) if comparable and ordered else {}), "semantic_correctness": "not_inferred"})
    return results


def display_checks(checks: dict) -> dict[str, str]:
    states = {c["state"] for c in checks["local_reference"]}
    qualified = len(checks["local_reference"]) > 1 or any(
        c["population_qualified"] for c in checks["local_reference"]
    )
    local = (
        "außerhalb mindestens eines angegebenen Referenzkontexts; Kontextzuordnung offen"
        if "outside_reported_range" in states and qualified
        else "außerhalb des angegebenen Referenzbereichs; keine automatische Datenfehlerbewertung"
        if "outside_reported_range" in states
        else "innerhalb aller angegebenen numerischen Grenzen; Kontextzuordnung offen"
        if states == {"inside_reported_range"} and qualified
        else "innerhalb der angegebenen Grenzen"
        if states == {"inside_reported_range"}
        else "nicht beurteilbar"
        if states
        else "kein Referenzbereich angegeben"
    )
    evidence = checks["evidence"]
    states = {c["state"] for c in evidence}
    text = (
        "möglicherweise unplausibel: außerhalb eines analytischen Kandidatenbereichs; Kontext fachlich prüfen"
        if "potential_implausibility_context_unverified" in states
        else "außerhalb eines Referenzkandidaten; Kontext fachlich prüfen"
        if "outside_candidate_reference_context_unverified" in states
        else "außerhalb eines dokumentierten Beispiels; kein allgemeiner Grenzwert"
        if "outside_documented_example" in states
        else "innerhalb belegter Kandidatengrenzen; Kontext offen"
        if "within_candidate_bounds_context_unverified" in states
        else "Evidenz nicht numerisch vergleichbar"
        if evidence
        else "keine Bereichsevidenz vorhanden"
    )
    refs = [r["relation"] for c in evidence for r in c.get("local_reference_comparisons", [])]
    ref_text = (
        "Referenzbereiche überlappen nicht; Kontext fachlich prüfen"
        if "disjoint_context_unverified" in refs
        else "abweichende Referenzgrenzen; Kontext offen"
        if "different_bounds_context_unverified" in refs
        else "gleiche numerische Grenzen; Kontext offen"
        if refs
        else "keine vergleichbare Referenzevidenz"
    )
    return {
        "werte_referenz_pruefung": local,
        "werte_evidenz_pruefung": text,
        "referenz_evidenz_pruefung": ref_text,
    }
