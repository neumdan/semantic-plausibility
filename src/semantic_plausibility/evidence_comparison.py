"""Descriptive, local evidence comparisons; no learned thresholds or clinical verdict."""

from decimal import Decimal

from .fhir_quality_pipeline import number, serial
from .laboratory_assessment import conditional_support

UCUM = "http://unitsofmeasure.org"


def compare_numbers(stats, ranges, evidence, scale):
    """All inputs already share an explicit, verified positive linear unit scale."""
    result = []
    if not scale.is_finite() or scale <= 0:
        return result
    kind = evidence.get("evidence_type")
    if kind == "reported_measurement":
        role = evidence.get("value_kind")
        key = {"min": "minimum", "max": "maximum"}.get(role, role)
        if role not in {"mean", "sd", "min", "max", "median"}:
            return result
        local, published = number(stats.get(key)), number(evidence.get("reported_value"))
        if local is not None and published is not None:
            if role == "sd" and (local < 0 or published < 0):
                return result
            published *= scale
            result.append({"field": "values", "statistic": role, "input_value": serial(local),
                "published_value": serial(published), "difference": serial(local - published),
                "relation": "same_numeric_value" if local == published else "lower" if local < published else "higher"})
    elif kind == "reference_interval":
        low, high = number(evidence.get("low")), number(evidence.get("high"))
        if low is None or high is None or low > high:
            return result
        low, high = low * scale, high * scale
        for name in ("minimum", "mean", "maximum"):
            value = number(stats.get(name))
            if value is not None:
                result.append({"field": "values", "statistic": name, "input_value": serial(value),
                    "published_low": serial(low), "published_high": serial(high),
                    "relation": "below_reference" if value < low else "above_reference" if value > high else "within_reference"})
        for interval in ranges:
            a, b = interval["low"], interval["high"]
            result.append({"field": "reference_ranges", "reference_index": interval["index"],
                "input_low": serial(a), "input_high": serial(b),
                "published_low": serial(low), "published_high": serial(high),
                "lower_difference": serial(a - low), "upper_difference": serial(b - high),
                "relation": "same_numeric_bounds" if (a, b) == (low, high) else
                            "disjoint_bounds" if a > high or b < low else "overlapping_bounds"})
    return result


def compare_published_evidence(row, item, payload, stats, candidates, ucum):
    support = conditional_support(row)
    condition = list(support["conditions"])
    code = item.get("loinc_code")
    if not code and support["level"] == "code_name_unit" and not support["context_conflicts"]:
        code = support["candidate"]["code"]
    unit = payload["quantity"].get("code")
    unit_ok = item.get("ucum_valid") is True or (
        not payload["quantity"].get("system") and support["unit_candidate"].get("unit") == unit)
    outcome = {"state": "insufficient_comparable_evidence", "condition": " ".join(condition),
               "context": "Population, specimen and assay applicability require review.",
               "clinical_plausibility": "not_established", "comparisons": [], "excluded": []}
    if not code or not unit_ok or not unit or stats.get("invalid") or payload["quantity"].get("comparator") or support["context_conflicts"]:
        outcome["reason"] = "Resolve analyte identity, unit interpretation or exact numeric input before comparison."
        return outcome

    def factor(source):
        if not source or not ucum.is_valid(source):
            return None
        value = Decimal(1) if source == unit else ucum.factor(source, unit)
        return value if value is not None and value.is_finite() and value > 0 else None

    local_ranges = []
    for i, interval in enumerate(payload.get("reference_ranges", [])):
        bounds = {}
        for side in ("low", "high"):
            bound = interval.get(side) or {}
            value = number(bound.get("value"))
            source = bound.get("code") or bound.get("unit") or unit
            if value is None or bound.get("comparator") or bound.get("system") not in (None, "", UCUM):
                break
            try:
                scale = factor(source)
            except (OSError, ValueError, RuntimeError):
                scale = None
            if scale is None:
                break
            bounds[side] = value * scale
        if len(bounds) == 2 and bounds["low"] <= bounds["high"]:
            local_ranges.append({"index": i + 1, **bounds})
    for candidate in candidates:
        if candidate.get("candidate_loinc") != code or candidate.get("status") != "candidate":
            continue
        kind = candidate.get("evidence_type")
        if kind not in {"reported_measurement", "reference_interval"}:
            continue
        if candidate.get("unit_validation_state") != "valid":
            outcome["excluded"].append({"source": candidate["source"], "reason":
                f"Study unit {candidate.get('unit')!r} has no verified UCUM interpretation"})
            continue
        try:
            scale = factor(candidate.get("unit_ucum"))
        except (OSError, ValueError, RuntimeError):
            scale = None
        if scale is None:
            outcome["excluded"].append({"source": candidate["source"], "reason": "No verified linear unit conversion"})
            continue
        for comparison in compare_numbers(stats, local_ranges, candidate, scale):
            outcome["comparisons"].append({**comparison, "unit": unit, "factor": serial(scale),
                "source": candidate["source"], "source_location": candidate.get("source_location", ""),
                "source_sha256": candidate.get("source_sha256", ""), "excerpt": candidate["excerpt"],
                "evidence_type": kind, "population": candidate.get("population", ""),
                "specimen": candidate.get("specimen", ""), "method": candidate.get("method", ""),
                "applicability": "unverified", "recommendation": "Retain source; review population, specimen and assay."})
    if outcome["comparisons"]:
        outcome.update(state="supported_comparison",
            reason="Source-checked evidence supports numerical comparisons; differences are review findings, not data errors.")
    else:
        outcome["reason"] = "No compatible published statistic or interval for the supplied input; absence is not implausibility."
    return outcome


def comparison_findings(comparison, field):
    entries = [r for r in comparison.get("comparisons", []) if r["field"] == field]
    if not entries:
        return [comparison.get("reason", "No comparable numerical evidence.")]
    findings = []
    for entry in entries:
        if field == "reference_ranges":
            value = f"Range {entry['reference_index']}: {entry['input_low']}–{entry['input_high']} vs published {entry['published_low']}–{entry['published_high']}"
        elif "published_value" in entry:
            value = f"{entry['statistic']}: input {entry['input_value']} vs published {entry['published_value']} (difference {entry['difference']})"
        else:
            value = f"Input {entry['statistic']} {entry['input_value']} vs published reference {entry['published_low']}–{entry['published_high']}"
        findings.append(f"{value} {entry['unit']}: {entry['relation'].replace('_', ' ')}; context unverified. {entry['source_location']}")
    distinct = list(dict.fromkeys(findings))
    return distinct[:8] + ([f"{len(distinct) - 8} further comparisons in the CSV evidence_comparison columns."] if len(distinct) > 8 else [])
