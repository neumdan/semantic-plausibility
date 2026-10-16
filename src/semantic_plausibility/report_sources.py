"""Project and render source sets, linked terminology and review proposals."""

from html import escape
import json
from urllib.parse import urlsplit

from .audit import encoded
from .aggregate_statistics import quantities
from .fhir_quality_pipeline import number


SET_FIELDS = ["source_data_json", "linked_data_json", "recommendation_set_json",
              "matched_analyte", "suggested_analyte", "suggested_code_version", "lookup_attempts_json"]


def lookup_attempts(item, unit=None):
    """Compact trace retaining every attempted source without repeating whole concepts."""
    rows = []
    checks = list(item.get('source_code_checks', [])) + [{**item.get('unit_check', {}), **(unit or {})}]
    checks += [{**check.get('terminology', {}),
                **{key: check[key] for key in ('system', 'code', 'version') if key in check}}
               for check in item.get('code_checks', [])]
    seen = set()
    for check in checks:
        for attempt in check.get('attempts', []) or [check]:
            if not attempt:
                continue
            metadata = attempt.get('metadata') or {}
            row = {key: attempt[key] for key in ('alias', 'type', 'role', 'state', 'source', 'source_sha256', 'error_type', 'reason', 'errors', 'availability') if key in attempt}
            row.update(system=check.get('system', ''), code=check.get('code', ''),
                       version=metadata.get('version', check.get('version', '')),
                       artifact=metadata.get('artifact', {}))
            identity = encoded(row)
            if identity not in seen:
                rows.append(row)
                seen.add(identity)
    return rows


def review_set_fields(row, evidence):
    """Use existing lookup evidence only; this projection performs no new lookups."""
    item = next(iter(evidence.get("report", {}).get("measurements", [])), {})
    metadata = evidence.get("metadata", {})
    suggestions = item.get("laboratory_suggestions") or {}
    code = next((check for check in item.get("code_checks", [])
                 if check.get("system") == "http://loinc.org" and check["terminology"]["state"] == "valid"), {})
    term = code.get("terminology", {})
    canonical = (term.get("metadata") or {}).get("display", "") or row.get("canonical_name", "")
    labels = [alias for check in item.get("name_checks", []) for alias in check.get("aliases", [])]
    labels += suggestions.get("name_candidates", [])
    selected = next((candidate for candidate in suggestions.get("coding_candidates", [])
                     if candidate.get("code") == row.get("suggested_code") and candidate.get("selectable")), {})
    approval = document(row.get("clinician_mapping_json"))
    approved = approval.get("target", {}) if isinstance(approval, dict) and approval.get("state") == "reused_clinician_approval" else {}
    ambiguous = item.get("multiple_loinc_codes") or item.get("multiple_loinc_versions")
    target_code = approved.get("code") or selected.get("code") or row.get("suggested_code") or (code.get("code", "") if not ambiguous else "")
    target_system = approved.get("code_system") or selected.get("system") or row.get("suggested_code_system") or code.get("system", "")
    edition = approved.get("code_version") or selected.get("version") or row.get("suggested_code_version") or (item.get("loinc_version") if target_code == code.get("code") else "")
    target_name = approved.get("name") or row.get("suggested_name") or selected.get("display") or (canonical if target_code == code.get("code") else "")
    axes = item.get("loinc_axes") or {}
    target_axes = selected.get("axes") or (axes if target_code == code.get("code") else {})
    matched_analyte = axes.get("component", "")
    proposed_analyte = target_axes.get("component", "")
    source = {"code": metadata.get("code", {"coding": [{"code": row.get("code"), "system": row.get("code_system")}]}),
              "name": row.get("analyte_name", ""),
              "quantity": metadata.get("quantity", {"unit": row.get("unit"), "code": row.get("unit_code"), "system": row.get("unit_system")}),
              "values": {key: row.get(key, "") for key in ("value", "value_min", "value_max", "value_mean", "value_sd", "value_se", "value_median", "value_iqr")},
              "statistics": quantities(row), "statistics_basis": row.get("statistics_availability", ""),
              "reference_ranges": metadata.get("reference_ranges", document(row.get("reference_ranges_json"))),
              "context": metadata.get("context", {}), "method": row.get("method", ""), "specimen": row.get("specimen", ""),
              "occurrence_count": row.get("occurrence_count"), "frequency_known": row.get("frequency_known"),
              "source_id": row.get("source_id", ""), "source_ref": row.get("source_ref", ""),
              "status": metadata.get("status", ""), "measurement_path": metadata.get("path", row.get("measurement_path", "")),
              "modifier_extensions": metadata.get("modifier_extensions", []),
              "source_value_types": metadata.get("source_value_types", [])}
    code_sources = [{"source": term.get("source"), "version": item.get("loinc_version", "")}] if term.get("source") else []
    if selected:
        code_sources = [selected, selected.get("retrieval", {})]
    elif approved:
        code_sources = [{"source": approval.get("verification", {}).get("code_source"), "version": edition}, {"source": approval.get("evidence")}]
    elif target_code and target_code != code.get("code"):
        mappings = document(row.get("coding_proposal_json"))
        code_sources = [{"source": mapping.get("conceptmap_url"), "version": mapping.get("conceptmap_version")}
                        for mapping in mappings if mapping.get("target_code") == target_code] if isinstance(mappings, list) else []
    name_sources = [label for label in (*labels, *selected.get("name_evidence", []))
                    if (label.get("name") or label.get("value")) == target_name]
    if not name_sources and target_name == canonical:
        name_sources = code_sources
    transform = document(row.get("transformation_json"))
    unit = row.get("suggested_unit") or row.get("unit_code", "")
    unit_system = approved.get("unit_system") or ("http://unitsofmeasure.org" if row.get("suggested_unit") else row.get("unit_system", ""))
    linked = {"code": {"code": code.get("code", ""), "system": code.get("system", ""),
                       "version": item.get("loinc_version", ""), "canonical_name": canonical,
                       "analyte": matched_analyte, "axes": axes, "state": term.get("state", "unknown"), "sources": [{"source": term.get("source"), "version": item.get("loinc_version", "")}]},
              "unit": item.get("unit_check", {}), "name_labels": labels,
              "source_code_checks": item.get("source_code_checks", []),
              "source_code_unit_fit": item.get("source_code_unit_fit", []),
              "coding_candidates": suggestions.get("coding_candidates", [])}
    linked['lookup_attempts'] = lookup_attempts(item, unit={"system": row.get("unit_system", ""), "code": row.get("unit_code", "")})
    linked["code"]["ambiguous_identity"] = bool(ambiguous)
    proposal = {"kind": "complete_set_review_proposal", "target_profile": row.get("ig_canonical") or row.get("profile"),
                "code": {"code": target_code, "system": target_system, "version": edition,
                         "action": "clinician_mapping" if approved else "retain_verified_code" if code and target_code == code.get("code") and not selected else "review_candidate" if target_code else "unresolved",
                         "reason": suggestions.get("reason") or "Exact code membership does not establish that the original analyte was mapped correctly.",
                         "sources": code_sources},
                "analyte": {"value": proposed_analyte, "action": "linked_definition" if proposed_analyte else "unresolved",
                            "reason": "LOINC component of the proposed code; specimen, property, time, scale and method must also agree.",
                            "sources": [target_axes.get("axis_sources", {}).get("COMPONENT", {})] + code_sources},
                "canonical_name": {"value": target_name, "action": "sourced_name" if target_name else "unresolved",
                                   "reason": "Terminology name conditional on the chosen code; do not reinterpret the measured analyte from its value.", "sources": name_sources},
                "quantity": {"unit": unit, "system": unit_system, "factor": row.get("suggested_factor") or transform.get("factor"),
                             "transformation": transform, "source_values": source["values"],
                             "source_statistics": quantities(row),
                             "converted_statistics": quantities(row, number(transform.get("factor")), transform.get("target_unit")) if transform.get("state") == "verified_linear" and number(transform.get("factor")) is not None else {},
                             "reason": "Use the verified transformation for values and each bound; an unresolved spelling/interpretation has no executable factor."},
                "values": {"comparison_loinc": item.get("loinc_code"),
                           "local_reference": evidence.get("check", {}).get("values", {}).get("local_reference", []),
                           "evidence_comparisons": evidence.get("check", {}).get("values", {}).get("evidence", []),
                           "context_verified": False},
                "reference_ranges": {"source": source["reference_ranges"], "candidates": [candidate for candidate in (document(row.get("reference_candidates_json")) or [])
                                     if not candidate.get("candidate_loinc") or candidate["candidate_loinc"] == target_code],
                                     "reason": "Published intervals are review evidence. Retain source ranges and their population/method context until adjudication."},
                "context": source["context"], "review_required": True, "automatic_changes": False}
    return {"source_data_json": encoded(source), "linked_data_json": encoded(linked),
            "lookup_attempts_json": encoded(linked['lookup_attempts']),
            "recommendation_set_json": encoded(proposal), "matched_analyte": matched_analyte,
            "suggested_analyte": proposed_analyte, "suggested_code_version": edition or "",
            "canonical_name": canonical,
            "suggested_name": row.get("suggested_name") or (target_name if target_name and row.get("name_check") != "exact_alias" else "")}


def document(value):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def source_html(source, *, version="", title="", excerpt="", sha256="", retrieved_at=""):
    """Only HTTP(S) citations are links; source text and quotes are always escaped."""
    text = str(source or "Source not recorded; requires review")
    try:
        url = urlsplit(text)
        linked = url.scheme in {"http", "https"} and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        linked = False
    label = str(title or text)
    rendered = (f'<a href="{escape(text, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f'{escape(label)}</a>') if linked else f"<code>{escape(text)}</code>"
    details = [f"edition {version}" if version else "", f"retrieved {retrieved_at}" if retrieved_at else "",
               f"SHA-256 {sha256}" if sha256 else ""]
    rendered += f'<span class="source-meta">{escape(" · ".join(filter(None, details)))}</span>'
    if excerpt:
        rendered += f"<blockquote>{escape(str(excerpt))}</blockquote>"
    return rendered


def citation_html(record):
    rendered = source_html(record.get("source"), version=record.get("version", ""),
        title=record.get("source_title", ""), excerpt=record.get("excerpt", ""),
        sha256=record.get("source_sha256", ""), retrieved_at=record.get("retrieved_at", ""))
    if record.get("source_url") and record["source_url"] != record.get("source"):
        rendered += source_html(record["source_url"], title="Published terminology resource",
                                version=record.get("artifact_version", record.get("supplement_version", "")))
    return rendered


def source_set_html(row):
    data = document(row.get("source_data_json"))
    values = data.get("values", {})
    text = {"name": data.get("name", row.get("analyte_name", "")),
            "coding": data.get("code", {"code": row.get("code"), "system": row.get("code_system")}),
            "quantity": data.get("quantity", {"code": row.get("unit_code"), "system": row.get("unit_system")}),
            "value / bounds": values or {"min": row.get("value_min"), "max": row.get("value_max")},
            "statistics (each with unit)": data.get("statistics", {}),
            "statistics basis": data.get("statistics_basis", ""),
            "reference ranges": data.get("reference_ranges", document(row.get("reference_ranges_json"))),
            "context": data.get("context", {}), "specimen": row.get("specimen", ""), "method": row.get("method", ""),
            "source status": data.get("status"), "modifiers": data.get("modifier_extensions"),
            "count": row.get("occurrence_count"), "ID": row.get("source_id", "")}
    return "".join(f'<div><strong>{escape(key)}</strong><pre>{escape(json.dumps(value, ensure_ascii=False, indent=2))}</pre></div>'
                   for key, value in text.items() if value not in (None, "", {}, []))


def linked_data_html(row):
    data = document(row.get("linked_data_json"))
    term = data.get("code", {})
    text = {"LOINC": term.get("code"), "Canonical name": term.get("canonical_name") or row.get("canonical_name"),
            "Analyte / component": term.get("analyte"), "Axes": term.get("axes"),
            "Exact code lookup": term.get("state", row.get("code_check")), "Unit lookup": row.get("unit_check"),
            "Source code check": row.get("source_code_check"), "Source unit check": row.get("source_unit_check"),
            "Code–unit fit": row.get("code_unit_fit"), "Name check": row.get("name_check")}
    rendered = "".join(f'<div><strong>{escape(key)}</strong>: {escape(json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value))}</div>'
                       for key, value in text.items() if value not in (None, "", {}, []))
    sources = term.get("sources", [])
    sources += data.get("name_labels", [])
    definitions = [check for check in data.get("source_code_checks", []) if check.get("metadata")]
    if definitions:
        rendered += '<details><summary>Source catalogue definitions</summary><pre>' + escape(json.dumps(definitions, ensure_ascii=False, indent=2)) + '</pre></details>'
    if data.get('lookup_attempts'):
        rendered += '<details><summary>Lookup attempts, editions and unavailable sources</summary><pre>' + escape(json.dumps(data['lookup_attempts'], ensure_ascii=False, indent=2)) + '</pre></details>'
    for check in data.get("source_code_checks", []):
        if check.get("source"):
            sources.append({"source": check["source"], "version": check.get("version", ""), **(check.get("metadata") or {}).get("artifact", {})})
    if data.get("unit", {}).get("source"):
        sources.append(data["unit"])
    seen = set()
    for record in sources:
        identity = (record.get("source"), record.get("source_url"), record.get("version"))
        if identity in seen:
            continue
        seen.add(identity)
        rendered += '<p>' + citation_html(record) + '</p>'
    if term.get("code") and term.get("state") == "valid":
        from urllib.parse import quote
        rendered += source_html("https://loinc.org/" + quote(str(term["code"]), safe=""), title="LOINC documentation (website edition may differ)")
    return rendered or "No verified terminology match obtained."


def recommendation_html(row):
    detail = document(row.get("evidence_json"))
    item = next(iter(detail.get("report", {}).get("measurements", [])), {}) if isinstance(detail, dict) else {}
    proposals = document(row.get("laboratory_suggestions_json"))
    if not isinstance(proposals, dict):
        proposals = {}
    cards = []

    def add(label, sources, note=""):
        seen, citations = set(), []
        for source in sources:
            if not isinstance(source, dict):
                continue
            identity = (source.get("source"), source.get("version"), source.get("excerpt"))
            if identity in seen:
                continue
            seen.add(identity)
            citations.append(citation_html(source))
            if source.get("doi"):
                citations.append("DOI: " + escape(str(source["doi"])))
        if not citations:
            citations = ["Source not recorded; requires review"]
        cards.append(f'<div class="recommendation"><strong>{escape(str(label))}</strong>'
                     f'<p>{escape(str(note))}</p><ul>' + "".join(f"<li>{value}</li>" for value in citations) + "</ul></div>")

    complete = document(row.get("recommendation_set_json"))
    for field, label in (("code", "Target coding"), ("analyte", "Analyte definition"), ("canonical_name", "Canonical name")):
        proposal = complete.get(field, {})
        value = proposal.get("code") or proposal.get("value")
        if value:
            add(label + ": " + str(value), proposal.get("sources", []),
                str(proposal.get("action", "")) + ". " + str(proposal.get("reason", "")))
    if complete:
        quantity = complete.get("quantity", {})
        plan = quantity.get("transformation", {})
        cards.append('<details><summary>Complete proposed set and transformation</summary><pre>' + escape(json.dumps(complete, ensure_ascii=False, indent=2)) + '</pre></details>')
        cards.append('<p>' + escape(str(complete.get("reference_ranges", {}).get("reason", ""))) + '</p>')
        if plan.get("operations"):
            cards.append('<pre>' + escape(json.dumps(plan["operations"], ensure_ascii=False, indent=2)) + '</pre>')
    suggested_unit = row.get("suggested_unit")
    if suggested_unit:
        candidates = [candidate for candidate in item.get("unit_candidates", [])
                      if candidate.get("unit") == suggested_unit]
        sources = list(candidates)
        # A verified linear transformation is independently supported by the UCUM service.
        if row.get("suggested_factor") not in (None, ""):
            sources.append({"source": item.get("ucum_source")})
        if complete.get("code", {}).get("sources"):
            sources.extend(complete["code"]["sources"])
        elif item.get("loinc_source"):
            sources.append({"source": item["loinc_source"], "version": item.get("loinc_version")})
        factor = row.get("suggested_factor")
        add(f"Unit proposal: {suggested_unit}" + (f" · factor {factor}" if factor not in (None, "") else ""),
            sources, row.get("suggestion_basis", "Clinician review required"))

    code = row.get("suggested_code")
    candidates = proposals.get("coding_candidates", [])
    for candidate in candidates:
        if code and candidate.get("code") != code:
            continue
        if complete.get("code", {}).get("code") == candidate.get("code"):
            continue
        add(("Code proposal: " if code else "Code candidate: ") + str(candidate.get("code", "")),
            [candidate, candidate.get("retrieval", {})],
            "Exactly looked up; semantic correctness requires clinician review." if candidate.get("selectable") else
            "Not selectable: " + ", ".join(candidate.get("context_conflicts", []) or ["unit fit or complete definition not established"]))
    if code and not complete and not any(candidate.get("code") == code for candidate in candidates):
        mappings = document(row.get("coding_proposal_json"))
        matching = [candidate for candidate in mappings if candidate.get("target_code") == code] if isinstance(mappings, list) else []
        add(f"Code proposal: {code}", [{"source": c.get("conceptmap_url"), "version": c.get("conceptmap_version")}
                                      for c in matching], "Mapping proposal; clinician review required.")
    name = row.get("suggested_name")
    if name and not complete:
        labels = list(proposals.get("name_candidates", []))
        labels += [label for candidate in candidates if not code or candidate.get("code") == code
                   for label in candidate.get("name_evidence", [])]
        add(f"Name proposal: {name}", [label for label in labels if label.get("name") == name],
            "Selected from sourced terminology names; clinician review required.")

    spelling = item.get("unit_spelling") or {}
    for candidate in spelling.get("candidates", []):
        unit = candidate.get("unit") if isinstance(candidate, dict) else candidate
        if unit and unit != suggested_unit:
            add(f"Unit spelling candidate: {unit}", [{"source": spelling.get("catalogue_source")}],
                "Catalogue candidate; equivalence and conversion require review.")

    evidence = document(row.get("study_evidence_json"))
    if not isinstance(evidence, list):
        evidence = item.get("study_candidates", [])
    for candidate in evidence:
        kind = candidate.get("evidence_type", "unclassified_discovery")
        if kind == "reported_measurement":
            label = f"Reported {candidate.get('value_kind', 'measurement')}: {candidate.get('reported_value', '') or ''} {candidate.get('unit', '')}"
        elif kind in {"reference_interval", "analytical_interval", "documented_extreme"}:
            label = f"{kind.replace('_', ' ').capitalize()}: {candidate.get('low')}–{candidate.get('high')} {candidate.get('unit', '')}"
        else:
            label = "Retrieved study; " + ("model extraction unavailable" if candidate.get("status") == "extraction_unavailable" else "no supported interval extracted")
        context = "; ".join(f"{key}: {candidate[key]}" for key in ("population", "specimen", "method") if candidate.get(key))
        comparisons = [comparison for comparison in complete.get("values", {}).get("evidence_comparisons", [])
                       if comparison.get("source") == candidate.get("source")]
        if comparisons:
            context += "; comparisons for original LOINC " + str(complete["values"].get("comparison_loinc") or "unresolved") + ": "
            context += "; ".join(str(comparison.get("state", "unresolved")) + " / " + str(comparison.get("reference_relation", "not_comparable")) for comparison in comparisons)
        if candidate.get("unit_ucum"):
            context += "; source unit " + str(candidate.get("unit")) + " → validated UCUM " + candidate["unit_ucum"] + "; no numeric conversion"
        if candidate.get("candidate_loinc"):
            label += " · LOINC " + str(candidate["candidate_loinc"])
        add(label, [candidate], context or "Evidence of reported practice; applicability requires clinician review.")
    if proposals.get("model_used") and proposals.get("model"):
        cards.append(f'<p class="source-meta">Proposal model: {escape(str(proposals["model"]))}. '
                     'Supporting sources are listed with each proposal.</p>')
    review = document(row.get("clinician_mapping_json"))
    if isinstance(review, dict) and review.get("state") == "reused_clinician_approval":
        target = review.get("target", {})
        verification = review.get("verification", {})
        add(f"Reused clinician mapping: {target.get('code', '')} / {target.get('unit', '')} · factor {target.get('factor', '')}",
            [{"source": review.get("evidence")}, {"source": verification.get("code_source")},
             {"source": verification.get("unit_source")}],
            f"Reviewer: {review.get('reviewer', '')}; {review.get('reviewed_at', '')}; decision {review.get('decision_id', '')}")
    return "".join(cards) or '<span class="source-meta">No recommendation obtained.</span>'
