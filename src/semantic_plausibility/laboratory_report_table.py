"""Readable, offline comparison of input fields and each method's actual evidence.

This is a projection of recorded results. It performs no lookup/model calls and
does not turn a terminology hit, model response or publication into an approval.
"""
from html import escape

from .aggregate_statistics import quantities
from .fhir_quality_pipeline import number
from .laboratory_suggestions import existing_code_conflicts
from .report_sources import document
from .report_comparison import Field, field_table_html as render_table


def is_true(value):
    return value is True or value == "True" or value == "true"


def text(value):
    return escape(str(value)) if value not in (None, "") else "Not supplied"


def statistics_text(stats):
    parts = []
    for key in ("mean", "sd", "min", "max", "median", "iqr"):
        q = stats.get(key, {})
        value = number(q.get("value"))
        if value is not None:
            display = format(value, ".6g")
            if number(display) != value:
                display = "≈" + display
            parts.append(f"{key}: {display} {q.get('unit') or '(unit missing)'}")
    return "; ".join(parts) or "No finite numeric summary"


def references_text(ranges):
    result = []
    for index, reference in enumerate(ranges):
        parts = []
        for side in ("low", "high"):
            bound = reference.get(side, {})
            if bound:
                parts.append(f"{side}: {bound.get('comparator', '')}{bound.get('value', 'missing')} "
                    f"{bound.get('code') or bound.get('unit') or '(unit missing)'}")
        qualifiers = [str(reference[key]) for key in ("text", "type", "appliesTo", "age") if reference.get(key)]
        result.append(f"Range {index + 1}: " + "; ".join(parts + qualifiers))
    return "\n".join(result) or "Not supplied"


def blocked_selection(row):
    """Also flag conflicting selections in reports recorded before this guard existed."""
    suggestions = document(row.get("laboratory_suggestions_json"))
    selected = suggestions.get("selected_code")
    evidence = document(row.get("evidence_json"))
    item = next(iter(evidence.get("report", {}).get("measurements", [])), {})
    if not selected or selected == item.get("loinc_code"):
        return []
    candidate = next((c for c in suggestions.get("coding_candidates", []) if c.get("code") == selected), None)
    if not candidate:
        return ["selected_code_without_verified_candidate"]
    conflicts = list(candidate.get("context_conflicts", []))
    conflicts += existing_code_conflicts(item.get("loinc_axes", {}), candidate.get("axes", {}))
    if not candidate.get("selectable") and not conflicts:
        conflicts.append("candidate_not_selectable")
    return sorted(set(conflicts))


def approval_html(row):
    ready = is_true(row.get("approved_mapping_ready"))
    state = row.get("mapping_review_state") or "not_recorded"
    note = ("A scoped clinician decision was reverified. Applying it and validating the resulting FHIR remain separate steps."
        if ready else "No currently reusable, fully supported clinician-approved mapping for this set.")
    if state == "not_configured":
        note = "No clinician review registry configured. Lookup matches and AI suggestions do not count as approval."
    approval = document(row.get("clinician_mapping_json"))
    target = approval.get("target", {})
    target_html = ("<br>Clinician target: " + text(target.get("code")) + " · " + text(target.get("name"))
        + " · " + text(target.get("unit")) + "; factor " + text(target.get("factor"))) if target else ""
    return '<p class="approval"><strong>Approved mapping ready: ' + ("Yes" if ready else "No") + '</strong> · ' + text(state) + '<br>' + text(note) + target_html + '</p>'


def transformation_text(plan):
    target = plan.get("target_unit")
    if not target:
        return "Unresolved; no transformation"
    if plan.get("state") == "verified_linear" and number(plan.get("factor")) is not None:
        return f"Retain {target}" if plan.get("kind") == "identity" else f"{target}; value × {plan['factor']}"
    return f"Candidate {target}; no verified factor"


def selected_candidate(row):
    suggestions = document(row.get("laboratory_suggestions_json"))
    if blocked_selection(row):
        return {}
    return next((c for c in suggestions.get("coding_candidates", [])
        if c.get("code") == suggestions.get("selected_code") and c.get("selectable")), {})


def reference_state(row, item):
    states = {r.get("state") for r in item.get("reference_checks", [])}
    if not states:
        states = set(str(row.get("reference_check") or "unknown").split("; "))
    if states & {"invalid_bounds", "policy_invalid", "unit_invalid"}:
        return "red"
    return "green" if states == {"bounds_valid"} else "yellow"


def set_decisions(row, summary):
    """Translate recorded checks into scoped actions; unknown is never a match."""
    source = document(row.get("source_data_json"))
    linked = document(row.get("linked_data_json"))
    evidence = document(row.get("evidence_json"))
    item = next(iter(evidence.get("report", {}).get("measurements", [])), {})
    complete = document(row.get("recommendation_set_json"))
    term = linked.get("code", {})
    selected = selected_candidate(row) if summary.get("operation") == "research-evidence" else {}
    conflicts = blocked_selection(row)
    plan = (complete.get("quantity", {}).get("transformation", {}) if selected else
        evidence.get("recommendation", {}).get("transformation", {}))
    verified = plan.get("state") == "verified_linear" and number(plan.get("factor")) is not None
    changed_unit = verified and plan.get("kind") != "identity"
    code_valid = row.get("code_check") == "valid" and not term.get("ambiguous_identity")
    source_code = row.get("code") or "Missing code"
    decisions = {}
    if conflicts:
        decisions["Code"] = ("Review", source_code, "Conflicting AI selection blocked; do not switch the analyte/code.")
    elif selected:
        decisions["Code"] = ("Review candidate", selected["code"], "Exactly looked up; confirm analyte, specimen and method before mapping.")
    elif code_valid:
        decisions["Code"] = ("Keep", source_code, "Code membership verified; identity of the measured analyte still needs context.")
    else:
        decisions["Code"] = ("Correct source" if row.get("code_check") == "invalid" else "Review",
            source_code, "No verified replacement code; resolve source catalogue/mapping.")
    system = selected.get("system") or row.get("code_system")
    decisions["Code system"] = ("Review candidate" if selected else "Keep" if item.get("code_policy_compatible") and code_valid else "Review",
        system or "Missing code system", "System belongs to the proposed code." if selected else "Target-profile compatibility: " + str(item.get("code_policy_compatible", "not recorded")))
    name = selected.get("display") or (term.get("canonical_name") if code_valid else None)
    supplied_name = source.get("name") or row.get("analyte_name")
    decisions["Analyte / name"] = (("Keep" if name == supplied_name else "Normalize label", name,
        "Terminology label for the chosen code; preserve the original local name. Confirm the candidate code first." if selected else
        "Terminology label for the existing code; not evidence of an incorrectly measured analyte.") if name and not conflicts else
        ("Review", supplied_name or "Missing name", "No non-conflicting, verified canonical name established."))
    axes = selected.get("axes", {}) or term.get("axes", {}) or item.get("loinc_axes") or {}
    for field, key, fit_key, axis in (("Specimen", "specimen", "system_fit", "system"), ("Method", "method", "method_fit", "method")):
        value = source.get(key) or row.get(key)
        fit = item.get(fit_key)
        expected = axes.get(axis)
        decisions[field] = (("Keep", value, "Matches the checked code context.") if value and fit == "match" and not selected else
            ("Not required by code", "Not supplied", "The checked code has no method-specific requirement.") if not value and key == "method" and fit == "not_applicable" and not selected else
            ("Complete" if not value else "Review", value or "Not supplied", "Confirm actual source context" + (f"; code describes {expected}." if expected else "; no specific context established.")))
    unit = row.get("unit_code") or row.get("unit") or "Missing unit"
    if changed_unit:
        decisions["Unit code / display"] = ("Convert", transformation_text(plan), "Proposal only; scale numeric values and each compatible reference bound together.")
    elif verified and (row.get("code_unit_fit") == "correct" or selected):
        decisions["Unit code / display"] = ("Keep" if not selected else "Review candidate", plan.get("target_unit") or unit,
            "Compatible in the recorded check; candidate code still requires review." if selected else "Syntax and code–unit compatibility established in the recorded check.")
    else:
        decisions["Unit code / display"] = ("Review", transformation_text(plan), "Do not infer a missing denominator or apply an unverified factor.")
    policy = item.get("quantity_policy", {})
    decisions["Unit system"] = ("Keep" if policy.get("state") == "compatible" and row.get("unit_check") == "valid" else "Review",
        row.get("unit_system") or "Missing unit system", "Profile-compatible unit system." if policy.get("state") == "compatible" else "Unit-system compatibility has not been established.")
    numeric = is_true(row.get("value_numeric_ready"))
    if not numeric:
        decisions["Value / statistics"] = ("Correct source" if number(row.get("n_invalid")) else "Complete", "Obtain finite numeric source values", "No numerical replacement is inferred from a code or reference interval.")
    elif changed_unit:
        decisions["Value / statistics"] = ("Convert", statistics_text(quantities(row, number(plan["factor"]), plan.get("target_unit"))), "Deterministic rescaling, not correction of the biological result.")
    else:
        decisions["Value / statistics"] = ("Keep source values", "No numerical change proposed", "Numeric format checked; clinical plausibility remains separate. An out-of-range value is not automatically erroneous.")
    ranges = source.get("reference_ranges") or document(row.get("reference_ranges_json")) or []
    converted = "; ".join(f"{op['path']}: {op['converted_bound']} {op.get('target_unit', '')}"
        for op in plan.get("operations", []) if op.get("path", "").startswith("referenceRange") and op.get("converted_bound") is not None)
    decisions["Reference ranges"] = (("Complete", "Obtain the source laboratory interval and context", "Do not fill a local interval from an article automatically.") if not ranges else
        ("Convert", converted, "Rescale the recorded bounds only; population, specimen and method remain unchanged.") if changed_unit and converted else
        ("Keep source ranges", "No replacement interval established", "Numeric bounds checked; clinical applicability is not established by this check.") if reference_state(row, item) == "green" else
        ("Review", "Resolve bounds, units and reference context", "No supported automatic replacement interval."))
    if summary.get("research", {}).get("scope") == "studies":
        study_code = selected.get("code") or item.get("loinc_code")
        intervals = [r for r in document(row.get("study_evidence_json")) or []
            if (not r.get("candidate_loinc") or r["candidate_loinc"] == study_code)
            and r.get("status") == "candidate" and r.get("excerpt") and r.get("source")
            and r.get("evidence_type") == "reference_interval"
            and (number(r.get("low")) is not None or number(r.get("high")) is not None)]
        if intervals:
            action, value, reason = decisions["Reference ranges"]
            decisions["Reference ranges"] = ("Review evidence" if action == "Keep source ranges" else action, value,
                reason + f" {len(intervals)} quoted reference-interval candidate(s): assess population, specimen and method before any replacement.")
    return decisions


def laboratory_fields(row, summary):
    """Project evidence into one row per field; field colours never imply set approval."""
    source = document(row.get("source_data_json"))
    linked = document(row.get("linked_data_json"))
    evidence = document(row.get("evidence_json"))
    item = next(iter(evidence.get("report", {}).get("measurements", [])), {})
    term = linked.get("code", {})
    research = summary.get("operation") == "research-evidence"
    selected = selected_candidate(row) if research else {}
    conflicts = blocked_selection(row)
    decisions = set_decisions(row, summary)
    plan = (document(row.get("recommendation_set_json")).get("quantity", {}).get("transformation", {})
        if selected else evidence.get("recommendation", {}).get("transformation", {}))
    code_valid = row.get("code_check") == "valid" and not term.get("ambiguous_identity")
    code_state = "green" if code_valid else "red" if row.get("code_check") == "invalid" else "yellow"
    term_sources = term.get("sources", [])
    candidate_sources = [selected, selected.get("retrieval", {})] if selected else []
    unit_sources = [linked.get("unit", {}), {"source": item.get("ucum_source")}]
    axes = selected.get("axes") or term.get("axes") or item.get("loinc_axes") or {}
    ranges = source.get("reference_ranges") or document(row.get("reference_ranges_json")) or []
    stats = statistics_text(source.get("statistics") or quantities(row))
    if row.get("value") not in (None, ""):
        stats = str(row["value"]) + " " + str(row.get("unit_code") or row.get("unit") or "(unit missing)") + "\n" + stats
    if number(row.get("n_invalid")):
        stats += f"\nInvalid values: {row['n_invalid']} (raw text not retained in this aggregate)"
    values = {"Code": row.get("code"), "Code system": row.get("code_system"),
        "Analyte / name": source.get("name") or row.get("analyte_name"),
        "Specimen": source.get("specimen") or row.get("specimen"), "Method": source.get("method") or row.get("method"),
        "Value / statistics": stats, "Unit code / display": row.get("unit_code") or row.get("unit"),
        "Unit system": row.get("unit_system"), "Reference ranges": references_text(ranges)}
    if row.get("unit") and row.get("unit_code") and row["unit"] != row["unit_code"]:
        values["Unit code / display"] += "\nDisplay: " + row["unit"]
    fields = {}
    for name, value in values.items():
        action, recommendation, reason = decisions[name]
        fields[name] = Field(name, value, [], action, recommendation, reason=reason)

    def put(name, state, findings, sources=()):
        field = fields[name]
        field.source_state, field.findings, field.sources = state, findings, list(sources)
        if state == "green" and field.action.startswith("Keep"):
            field.proposal_state = "green"

    put("Code", code_state, ["Lookup: " + (term.get("canonical_name") or "no verified definition found")], term_sources)
    if conflicts:
        fields["Code"].findings.append("AI: conflicting selection blocked; no code switch.")
        if code_valid:
            fields["Code"].action, fields["Code"].recommendation, fields["Code"].proposal_state = "Keep checked code", row["code"], "green"
    elif selected:
        fields["Code"].findings.append("AI + exact lookup: candidate " + selected["code"] + ". Confirm measured analyte.")
        fields["Code"].proposal_state = "yellow"
        fields["Code"].sources += candidate_sources
    elif research:
        model_used = document(row.get("laboratory_suggestions_json")).get("model_used")
        fields["Code"].findings.append("AI-assisted search: no code change selected." if model_used else
            "AI code selection not needed; existing lookup retained." if code_valid else "AI: no code selection recorded.")
    policy_ok = is_true(item.get("code_policy_compatible"))
    invalid_policy = any(c.get("state") == "invalid" for c in item.get("code_checks", [])) or (code_valid and item.get("code_policy_compatible") is False)
    put("Code system", "red" if invalid_policy else "green" if code_valid and policy_ok else "yellow",
        ["Profile check: " + ("declared system accepted for this coding." if code_valid and policy_ok else "compatibility not established for a verified code.")],
        term_sources + candidate_sources)
    if selected:
        fields["Code system"].proposal_state = "yellow"
        fields["Code system"].findings.append("AI + lookup: target system belongs to candidate code.")
    name_states = set(str(row.get("name_check") or "").split("; "))
    name_ok = bool(name_states) and name_states <= {"exact_alias", "component_label"} and code_valid
    name_result = ("matched component label; full definition below." if name_states == {"component_label"} and name_ok else
        "matched terminology label." if name_ok else "name not verified against this code.")
    put("Analyte / name", "green" if name_ok else "yellow", ["Lookup: " + name_result], term_sources + candidate_sources)
    if fields["Analyte / name"].action == "Normalize label" or selected and not conflicts:
        fields["Analyte / name"].proposal_state = "yellow"
        fields["Analyte / name"].action = "Use canonical label (review)"
    for name, fit_key, axis in (("Specimen", "system_fit", "system"), ("Method", "method_fit", "method")):
        fit, expected = item.get(fit_key), axes.get(axis)
        state = "green" if fit == "match" and code_valid else "red" if fit == "mismatch" else "yellow"
        put(name, state, ["Code context: " + (str(expected) if expected else "no specific requirement established") + "; "
            + ("matches input." if state == "green" else "confirm source context.")], term_sources + candidate_sources)
        if not values[name] and name == "Method" and fit == "not_applicable" and code_valid and not selected:
            fields[name].source_state = fields[name].proposal_state = "grey"
            fields[name].action, fields[name].recommendation = "Not required", "No method specified by this code"
            fields[name].findings = ["Code context: no method-specific requirement."]
        elif name == "Method" and selected and "method" in axes and not expected:
            fields[name].proposal_state = "yellow"
            fields[name].action = "Review candidate context"
            fields[name].recommendation = "Candidate has no method-specific requirement; confirm the code mapping"
            fields[name].findings = ["Candidate definition: no method-specific requirement. Source coding remains unresolved."]
        elif state != "green" or selected:
            fields[name].action = "Confirm source context"
            fields[name].recommendation = "Verify against " + str(expected) if expected else "Obtain source documentation"
            fields[name].proposal_state = "yellow" if expected else "red"
    numeric = is_true(row.get("value_numeric_ready"))
    put("Value / statistics", "green" if numeric else "red" if number(row.get("n_invalid")) else "yellow",
        ["Numeric check: finite values." if numeric else "Numeric check: missing or nonnumeric values.",
         "Range check: " + str(row.get("value_reference_check") or "not assessed").replace("_", " ") + ". Not a clinical error verdict."])
    if fields["Value / statistics"].action == "Convert":
        fields["Value / statistics"].proposal_state = "yellow"
        fields["Value / statistics"].findings.append("Unit conversion: rescale by " + str(plan.get("factor")) + ".")
        fields["Value / statistics"].sources += unit_sources
    elif numeric:
        fields["Value / statistics"].action, fields["Value / statistics"].recommendation = "Keep numeric values", "Unchanged; clinical plausibility not established"
    fit = row.get("code_unit_fit")
    unit_ok = row.get("unit_check") == "valid" and fit in {"correct", "normalization_verified", "conversion_verified"}
    unit_label = item.get("unit_label_check") or {}
    if unit_label.get("state") in {"unverified", "alias_version_unverified"}:
        unit_ok = False
    put("Unit code / display", "green" if unit_ok else "red" if row.get("unit_check") == "invalid" else "yellow",
        ["Unit lookup: " + str(row.get("unit_check") or "unknown") + ".", "Code–unit check: "
         + {"correct": "compatible.", "conversion_verified": "verified conversion available.", "needs_refinement": "meaning or conversion unresolved."}.get(fit, "compatibility unresolved.")],
        unit_sources + term_sources + candidate_sources)
    if fields["Unit code / display"].action in {"Convert", "Review candidate"} or plan.get("target_unit"):
        if fields["Unit code / display"].action != "Keep":
            fields["Unit code / display"].proposal_state = "yellow"
    if unit_label.get("state") in {"unverified", "alias_version_unverified"}:
        fields["Unit code / display"].findings.append("Display label is not verified for the unit code; clarify source wording.")
        if fields["Unit code / display"].action == "Keep":
            fields["Unit code / display"].action = "Verify unit display"
            fields["Unit code / display"].proposal_state = "yellow"
    spelling = item.get("unit_spelling") or {}
    if spelling.get("model_used"):
        fields["Unit code / display"].findings.append("AI spelling: " + str(spelling.get("selected_unit") or "abstained") + "; equivalence needs verification.")
        fields["Unit code / display"].sources.append({"source": spelling.get("catalogue_source"), "version": spelling.get("catalogue_version")})
    unit_policy = item.get("quantity_policy", {})
    put("Unit system", "red" if unit_policy.get("state") == "invalid" else "green" if unit_policy.get("state") == "compatible" and row.get("unit_check") == "valid" else "yellow",
        ["Profile + lookup: " + ("accepted unit system." if unit_policy.get("state") == "compatible" and row.get("unit_check") == "valid" else "not verified.")], unit_sources)
    put("Reference ranges", reference_state(row, item),
        ["Bounds check: " + ("ordered numeric bounds; clinical applicability unverified." if reference_state(row, item) == "green" else
            "not supplied." if not ranges else str(row.get("reference_check") or "unresolved").replace("_", " ") + ".")])
    if fields["Reference ranges"].action == "Convert":
        fields["Reference ranges"].proposal_state = "yellow"
        fields["Reference ranges"].findings.append("Unit conversion: rescale recorded bounds together with values.")
        fields["Reference ranges"].sources += unit_sources
    if research and summary.get("research", {}).get("scope") == "studies":
        study_code = selected.get("code") or item.get("loinc_code")
        records = [r for r in document(row.get("study_evidence_json")) or [] if not r.get("candidate_loinc") or r["candidate_loinc"] == study_code]
        supported = [r for r in records if r.get("status") == "candidate" and r.get("excerpt") and r.get("source")
            and any(number(r.get(k)) is not None for k in ("reported_value", "low", "high"))]
        for name, kinds in (("Value / statistics", {"reported_measurement", "documented_extreme"}),
                            ("Reference ranges", {"reference_interval", "analytical_interval"})):
            extracted = [r for r in supported if r.get("evidence_type") in kinds]
            fields[name].findings.append(f"Studies: {len({r['source'] for r in records if r.get('source')})} articles; {len(extracted)} quoted numeric records.")
            failed = len({r['source'] for r in records if r.get('source') and r.get('status') == 'extraction_unavailable'})
            if failed:
                fields[name].findings.append(f"{failed} article extraction(s) rejected/unavailable; no numerical proposal from those articles. See AI trace.")
            for record in extracted:
                quantity = str(record["reported_value"]) if number(record.get("reported_value")) is not None else f"{record.get('low')} … {record.get('high')}"
                fields[name].findings.append(str(record.get("value_kind") or record["evidence_type"]).replace("_", " ") + ": " + quantity + " " + str(record.get("unit") or "(unit missing)") + "; applicability unverified.")
            fields[name].sources += extracted
            if extracted and fields[name].proposal_state != "yellow":
                fields[name].proposal_state = "yellow"
                fields[name].action = "Review published evidence"
                fields[name].recommendation = "Retain source pending context review; no automatic replacement"
    return list(fields.values())


def field_table_html(row, summary):
    return render_table(laboratory_fields(row, summary), prefix="item-" + str(row.get("tuple_id") or "unknown"))
