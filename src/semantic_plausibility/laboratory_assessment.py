"""Explainable laboratory assessment layers from recorded evidence only.

No network, inferred source metadata, probability score or clinical auto-approval.
The same projection is used for CSV and HTML.
"""
from html import escape

from .fhir_quality_pipeline import number
from .laboratory_suggestions import existing_code_conflicts
from .profile_config import missing_metadata_only
from .report_sources import document

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"


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



def selected_candidate(row):
    suggestions = document(row.get("laboratory_suggestions_json"))
    if blocked_selection(row):
        return {}
    return next((c for c in suggestions.get("coding_candidates", [])
        if c.get("code") == suggestions.get("selected_code") and c.get("selectable")), {})


def verified_unit_candidate(row):
    """A missing source system stays missing; validation supports a review target."""
    candidates = document(row.get("laboratory_suggestions_json")).get("unit_interpretations", [])
    verified = [candidate for candidate in candidates if candidate.get("state") == "valid"
                and candidate.get("system") == "http://unitsofmeasure.org" and candidate.get("source")]
    return verified[0] if len({candidate["unit"] for candidate in verified}) == 1 else {}



def conditional_support(row):
    """Separate a literal code match from a proposed change of analyte/code."""
    evidence = document(row.get('evidence_json'))
    item = next(iter(evidence.get('report', {}).get('measurements', [])), {})
    suggestions = document(row.get('laboratory_suggestions_json'))
    literal = [c for c in suggestions.get('coding_candidates', [])
               if c.get('code') == row.get('code') and c.get('target_verified') and c.get('source')]
    candidate = literal[0] if len(literal) == 1 else {}
    unit = verified_unit_candidate(row)
    code_verified = row.get('code_check') == 'valid' and not item.get('multiple_loinc_codes') and not item.get('multiple_loinc_versions')
    code_match = code_verified or bool(candidate)
    names = {check.get('state') for check in item.get('name_checks', [])}
    name_match = bool(code_verified and names and names <= {'exact_alias', 'component_label'}) or bool(candidate.get('source_name_matches'))
    fit = candidate.get('unit_fit', {}) if candidate else item.get('mapping', {})
    unit_literal = bool(unit and unit['unit'] == row.get('unit_code'))
    unit_match = fit.get('class') in {'correct', 'normalization_verified', 'conversion_verified'} and (
        row.get('unit_check') == 'valid' or unit_literal)
    conflicts = list(candidate.get('context_conflicts', []))
    if not candidate:
        conflicts += [key for key in ('system_fit', 'method_fit', 'scale_fit') if item.get(key) == 'mismatch']
        conflicts += [key for key, value in item.get('additional_axis_fits', {}).items() if value == 'mismatch']
    signals = [name for name, yes in (('exact_code', code_match), ('matching_name', name_match),
                                     ('compatible_unit', unit_match)) if yes]
    level = ('code_name_unit' if code_match and name_match and unit_match and not conflicts else
             'code_name' if code_match and name_match else 'code_only' if code_match else 'none')
    code_condition = f"If code_system = {LOINC}, code {row.get('code')} exists in that system." if candidate and not row.get('code_system') else ''
    unit_condition = f"If unit_system = {UCUM}, {unit['unit']} is a valid unit code." if unit_literal and not row.get('unit_system') else ''
    conditions = [condition for condition in (code_condition, unit_condition) if condition]
    return {'level': level, 'signals': signals, 'conditions': conditions,
            'code_condition': code_condition, 'unit_condition': unit_condition,
            'candidate': candidate, 'unit_candidate': unit, 'context_conflicts': conflicts,
            'name_match': name_match, 'unit_match': unit_match,
            'meaning': 'Ordered corroboration, not a probability or mapping approval'}


def _outcome(state, complete, reason, **extra):
    return {'state': state, 'complete': bool(complete), 'reason': reason, **extra}


def assessment_layers(row, summary=None):
    """Assess field QC, identity, quantity, code–quantity fit, and the whole set."""
    summary = summary or {}
    item = next(iter(document(row.get('evidence_json')).get('report', {}).get('measurements', [])), {})
    support = conditional_support(row)
    candidate = support['candidate']
    source = document(row.get('source_data_json'))
    axes = candidate.get('axes') or item.get('loinc_axes') or {}
    policies = [*item.get('code_checks', []), item.get('quantity_policy', {})]
    policies += [p for reference in item.get('reference_checks', []) for p in reference.get('policies', [])]
    conflicting = [p for p in policies if p.get('state') == 'invalid' and not missing_metadata_only(p)]
    missing = [p for p in policies if missing_metadata_only(p)]
    bindings = [c for c in item.get('code_checks', []) if c.get('binding_strength') == 'required'
                and not c.get('concept_binding_satisfied')]
    binding_error = any((c.get('value_set_membership') or {}).get('state') == 'not_member' for c in bindings)
    binding_unknown = any((c.get('value_set_membership') or {}).get('state') not in {'member', 'not_member'} for c in bindings)
    required_coding_missing = item.get('code_policy_compatible') is False and all(c.get('state') == 'compatible' for c in item.get('code_checks', []))
    policy_complete = bool(policies) and not missing and not required_coding_missing
    terminology_error = row.get('code_check') == 'invalid' or row.get('unit_check') == 'invalid'
    terminology_unknown = row.get('code_check') != 'valid' or row.get('unit_check') not in {'valid', 'not_applicable'}
    profile = _outcome('error' if conflicting or binding_error or terminology_error else 'incomplete' if not policy_complete else
                       'unverified' if binding_unknown or terminology_unknown else 'supported', policy_complete,
                       'Resolve conflicting profile elements, codes or bindings.' if conflicting or binding_error or terminology_error else
                       'Supply required metadata; formal FHIR requirements remain recorded.' if not policy_complete else
                       'Complete terminology/binding verification.' if binding_unknown or terminology_unknown else 'Implemented element and terminology checks are satisfied.',
                       full_resource_validation=row.get('fhir_validation_state') or summary.get('fhir_validation', {}).get('state', 'not_recorded'),
                       scope='Configured element rules; full-resource validation is reported separately')
    identity_missing = [name for name in ('code', 'code_system', 'analyte_name') if not row.get(name)]
    if axes.get('system') and not (source.get('specimen') or row.get('specimen')):
        identity_missing.append('specimen')
    if axes.get('method') and not (source.get('method') or row.get('method')):
        identity_missing.append('method')
    identity_complete = not identity_missing
    identity_error = row.get('code_check') == 'invalid' or (row.get('code_check') == 'valid' and bool(support['context_conflicts']))
    candidate_conflict = bool(support['context_conflicts']) and not identity_error
    source_identity_verified = row.get('code_check') == 'valid' and support['name_match']
    identity = _outcome('error' if identity_error else 'needs_review' if candidate_conflict else 'conditional' if support['code_condition'] and support['name_match'] else
                        'incomplete' if not identity_complete else 'supported' if source_identity_verified else 'unverified',
                        identity_complete,
                        'Resolve code/name/context conflict.' if identity_error else
                        'Source context conflicts with the candidate definition; confirm the intended coding.' if candidate_conflict else
                        'Confirm the intended code system and actual specimen.' if support['code_condition'] else
                        'Supply: ' + ', '.join(identity_missing) + '.' if identity_missing else
                        'Code and label agree; clinical identity still requires source context.' if source_identity_verified else 'Verify code and name against the source catalogue.',
                        missing_fields=identity_missing, support_level=support['level'], signals=support['signals'],
                        conditions=support['conditions'])
    numeric = row.get('value_numeric_ready') in (True, 'True', 'true')
    unit_complete = bool(row.get('unit_code') and row.get('unit_system'))
    number_supplied = numeric or bool(number(row.get('n_invalid'))) or any(row.get(key) not in (None, '')
        for key in ('value', 'value_mean', 'value_min', 'value_max', 'value_median'))
    quantity_complete = number_supplied and unit_complete
    quantity_error = bool(number(row.get('n_invalid'))) or row.get('unit_check') == 'invalid'
    quantity = _outcome('error' if quantity_error else 'conditional' if numeric and support['unit_condition'] else
                        'incomplete' if not quantity_complete else 'supported' if numeric and row.get('unit_check') == 'valid' else 'unverified',
                        quantity_complete,
                        'Correct invalid numeric data or declared unit code.' if quantity_error else
                        'Confirm UCUM as the intended system; preserve the numbers.' if numeric and support['unit_condition'] else
                        'Supply numeric values and unit code/system.' if not quantity_complete else
                        'Numeric format and unit membership checked; clinical range is separate.',
                        numeric_format='valid' if numeric else 'invalid' if number(row.get('n_invalid')) else 'missing',
                        condition=support['unit_condition'])
    fit = item.get('mapping', {}).get('class') or row.get('code_unit_fit')
    fit_complete = bool(row.get('code') and row.get('code_system') and quantity_complete)
    fit_error = quantity_error or identity_error or row.get('code_check') == 'invalid'
    conditional_fit = bool(support['conditions'] and support['level'] == 'code_name_unit' and numeric)
    fit_state = ('error' if fit_error else 'conditional' if conditional_fit else
                 'transformation_required' if fit in {'normalization_verified', 'conversion_verified'} and fit_complete else
                 'supported' if fit == 'correct' and fit_complete else 'incomplete' if not fit_complete else 'unverified')
    code_quantity = _outcome(fit_state, fit_complete,
        'Resolve the conflicting code, context or quantity.' if fit_error else
        'Code/name and unit agree under the proposed systems; confirm those systems.' if conditional_fit else
        'Review the verified conversion and transform values and compatible bounds together.' if fit_state == 'transformation_required' else
        'Terminology supports this code–unit combination; numeric format checked.' if fit_state == 'supported' else
        'Establish source systems and code–unit compatibility.',
        conditions=support['conditions'], support_level=support['level'],
        unit_relation=(candidate.get('unit_fit') or item.get('mapping') or {}).get('class', 'unresolved'),
        sources=[{key: record[key] for key in ('source', 'version') if record.get(key)}
                 for record in [candidate, support['unit_candidate'], {'source': item.get('loinc_source')}, {'source': item.get('ucum_source')}]
                 if record.get('source')], scope='Terminology compatibility, not physiological value-range evidence')
    references = source.get('reference_ranges') or document(row.get('reference_ranges_json')) or []
    reference_results = item.get('reference_checks', [])
    reference_error = any(r.get('state') in {'invalid_bounds', 'unit_invalid', 'policy_invalid'} for r in reference_results)
    reference_complete = bool(references)
    for interval in references:
        bounds = [interval.get(side) for side in ('low', 'high') if interval.get(side)]
        reference_complete &= bool(bounds) and all(isinstance(bound, dict) and bound.get('value') not in (None, '')
            and (bool(bound.get('code') and bound.get('system')) or
                 not (bound.get('code') or bound.get('unit') or bound.get('system')) and unit_complete) for bound in bounds)
    reference_supported = bool(reference_results) and all(r.get('state') == 'bounds_valid' for r in reference_results)
    records = [r for r in document(row.get('study_evidence_json')) or []
               if r.get('status') == 'candidate' and r.get('source') and r.get('excerpt')
               and (not r.get('candidate_loinc') or r['candidate_loinc'] == (candidate.get('code') or item.get('loinc_code')))]
    values = [r for r in records if r.get('evidence_type') in {'reported_measurement', 'documented_extreme'}
              and any(number(r.get(key)) is not None for key in ('reported_value', 'low', 'high'))]
    intervals = [r for r in records if r.get('evidence_type') == 'reference_interval'
                 and any(number(r.get(key)) is not None for key in ('low', 'high'))]
    analytical = [r for r in records if r.get('evidence_type') in {'analytical_limit', 'analytical_interval'}]
    study_run = summary.get('research', {}).get('scope') == 'studies' and summary.get('operation') == 'research-evidence'
    evidence_state = 'evidence_for_review' if values or intervals else 'no_eligible_evidence' if study_run else 'not_searched'
    complete = identity_complete and quantity_complete and reference_complete
    technical_support = identity['state'] == quantity['state'] == profile['state'] == fit_state == 'supported' and reference_supported
    whole = _outcome('error' if fit_error or reference_error or profile['state'] == 'error' else
                      'conditional' if conditional_fit else 'incomplete' if not complete else
                      'needs_review' if not technical_support else
                      'evidence_for_review' if values or intervals else 'needs_evidence', complete,
        'Resolve demonstrated conflicts before adopting a mapping.' if fit_error or reference_error or profile['state'] == 'error' else
        'Confirm missing metadata and reference units; evaluate study applicability.' if not complete else
        'Resolve pending terminology, context or conversion checks.' if not technical_support else
        'Review study population, specimen and method before judging plausibility.' if values or intervals else
        'Terminology agreement alone does not establish physiological ranges.',
        code_quantity_state=fit_state, reference_state='error' if reference_error else 'incomplete' if not reference_complete else 'supported' if reference_supported else 'unverified',
        evidence_state=evidence_state, quoted_value_records=len(values), quoted_reference_records=len(intervals),
        analytical_records=len(analytical), conditions=support['conditions'],
        clinical_correctness='requires_clinician_review')
    return {'profile_qc': profile, 'identity_set': identity, 'quantity_set': quantity,
            'code_quantity_fit': code_quantity, 'complete_set': whole}


LABELS = {'profile_qc': 'Profile element QC', 'identity_set': 'Code + system + name + context',
          'quantity_set': 'Values + unit + unit system', 'code_quantity_fit': 'Code–quantity compatibility',
          'complete_set': 'Whole set + reference/evidence'}


def assessment_table_html(layers):
    rows = []
    for key, layer in layers.items():
        state = layer['state'].replace('_', ' ')
        extra = (' Evidence: ' + layer['evidence_state'].replace('_', ' ') + '.' if key == 'complete_set' else '')
        if key == 'profile_qc':
            extra = ' Full resource validation: ' + layer['full_resource_validation'].replace('_', ' ') + '.'
        rows.append('<tr><th scope="row">' + escape(LABELS[key]) + '</th><td>' + escape(state)
                    + '</td><td>' + ('Complete' if layer['complete'] else 'Incomplete') + '</td><td>'
                    + escape(layer['reason'] + extra) + '</td></tr>')
    return '<div class="scroll"><table class="assessment-table"><caption>Assessment by scope</caption><thead><tr><th>Scope</th><th>Finding</th><th>Supplied set</th><th>Why / next step</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
