"""Recorded medication sets and checks as input → finding → scoped action.

No lookup, model call, new dose calculation or inferred patient instruction.
"""
import re

from .report_comparison import Field


def readable(value):
    """Retain all instructions, codings, quantity systems and timing constraints."""
    if value is None or value == "" or value == {} or value == []:
        return "Not supplied"
    if isinstance(value, list):
        return "; ".join(readable(item) for item in value)
    if isinstance(value, dict):
        if "value" in value and any(key in value for key in ("code", "unit", "system")):
            text = f"{value.get('comparator', '')}{value['value']} {value.get('code') or value.get('unit') or '(unit missing)'}"
            text += "; unit system: " + str(value.get("system") or "Not supplied")
            if value.get("unit") and value.get("code") and value["unit"] != value["code"]:
                text += "; display: " + str(value["unit"])
            return text
        return "; ".join(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key).lower() + ": " + readable(item) for key, item in value.items())
    return str(value)


def path_matches(field_path, evidence_path):
    """A parent finding affects its children, without matching e.g. dosage[1] to [10]."""
    def under(path, parent):
        return path == parent or path.startswith(parent + ".") or path.startswith(parent + "[")
    return under(field_path, evidence_path) or under(evidence_path, field_path)


def medication_fields(row, detail):
    """One source field and its scoped evidence; no inferred dose or daily schedule."""
    data, terms, findings, proposals = detail['source_set'], detail['terminology'], detail['findings'], detail['recommendations']
    product = data['medication']
    fields = []
    covered_findings = set()

    def add_field(label, path, value):
        matched = [t for t in terms if path_matches(path, t['path'])]
        affected = [(i, f) for i, f in enumerate(findings) if path_matches(path, f['path'])]
        covered_findings.update(i for i, _ in affected)
        errors = [f for _, f in affected if f['state'] == 'error']
        reviews = [f for _, f in affected if f['state'] == 'review']
        verified = bool(matched) and all(t.get('state') == 'valid' and not t.get('conflict') for t in matched)
        state = 'red' if errors or any(t.get('state') == 'invalid' for t in matched) else 'green' if verified and not reviews else 'yellow'
        messages = []
        for term in matched:
            term_label = (term.get('metadata') or {}).get('display') or term.get('code') or 'missing code'
            messages.append('Lookup: ' + str(term_label) + ' — ' + str(term.get('state') or 'unknown') + ('. Unit membership only.' if term.get('kind') == 'quantity' else '. Code membership only.'))
        messages += ['Consistency: ' + f['message'] for _, f in affected if f['state'] in {'error', 'review'}]
        if not messages:
            messages = ['Source assertion; no field-specific verification recorded.']
        field = Field(
            name=label, value=readable(value), findings=messages,
            action='Keep checked coding' if state == 'green' else 'Clarify source',
            recommendation='Unchanged within membership checks' if state == 'green' else 'No supported replacement; review source record',
            source_state=state, proposal_state='green' if state == 'green' else 'red',
            reason=path + ': Membership does not verify product identity, source strength, form, route or clinical dose.',
            sources=matched + [f for _, f in affected])
        fields.append(field)
        return field

    name = Field('Product name', row.get('product_name') or 'Not supplied', ['Lookup: no verified canonical label found.'],
        'Clarify product identity', 'No supported label proposal', reason='A code label cannot establish the actual formulation or ingredient strength.')
    labels = [p for p in proposals if p['type'] == 'canonical_name' and p.get('path') == 'Medication.code']
    if labels:
        name.findings = ['Lookup: canonical label for ' + str(p.get('code')) + '.' for p in labels]
        name.action, name.recommendation = 'Use canonical label (review)', '\n'.join(p['value'] for p in labels)
        name.proposal_state, name.sources = 'yellow', labels
        if all(p['value'] == row.get('product_name') for p in labels):
            name.source_state = name.proposal_state = 'green'
            name.action = 'Keep label'
    fields.append(name)
    add_field('Product code / system', 'Medication.code', product.get('code', {}).get('coding'))
    add_field('Form', 'Medication.form', product.get('form'))
    for index, ingredient in enumerate(product.get('ingredient', [])):
        for key, value in ingredient.items():
            path = f'Medication.ingredient[{index}].{key}'
            if key == 'strength' and isinstance(value, dict):
                for side, quantity in value.items():
                    add_field(f'Strength {index + 1} · {side}', path + '.' + side, quantity)
            else:
                add_field(f'Ingredient {index + 1} · ' + re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', key).lower(), path, value)
    for index, dosage in enumerate(data.get('dosages', [])):
        for key, value in dosage.items():
            path = f'dosage[{index}].{key}'
            entries = [(f'{path}[{n}].{k}', f'Dosage {index + 1}.{n + 1} · {k}', v)
                for n, entry in enumerate(value) for k, v in entry.items()] if key == 'doseAndRate' and isinstance(value, list) else [(path, f'Dosage {index + 1} · {key}', value)]
            for element, label, content in entries:
                field = add_field(re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', label).lower().capitalize(), element, content)
                # A parent doseAndRate proposal belongs to its dose, never to its rate.
                conversions = [p for p in proposals if p['type'] == 'strength_conversion'
                    and (element == p.get('path') or element == str(p.get('path')) + '.doseQuantity')]
                if conversions and field.source_state != 'red':
                    field.action = 'Review conditional conversion'
                    field.recommendation = '\n'.join(f"{p['proposed_value']} {p['target_unit']}; factor {p['factor']}; ingredient {p['ingredient_index'] + 1}" for p in conversions)
                    field.findings.append('Calculation: uses declared strength; verify actual product and ingredient.')
                    field.proposal_state = 'yellow'
                    field.reason += ' Not a replacement dose. Source strength is not an independently verified drug label.'
                    field.sources += conversions + [{'source': p.get('strength_source')} for p in conversions]
    if not data.get('dosages'):
        not_applicable = data['resource_type'] == 'Medication'
        fields.append(Field('Dosage', 'Not supplied', ['Standalone product; no dose required.' if not_applicable else 'No structured dose supplied.'],
            'Not applicable' if not_applicable else 'Obtain dosage', 'No prescription or administration implied' if not_applicable else 'No dose inferred',
            'grey' if not_applicable else 'yellow', 'grey' if not_applicable else 'red'))
    if data.get('duration'):
        add_field('Duration', 'duration', data['duration'])
        add_field('Constant delivery', 'constant_rate', data.get('constant_rate'))
    if data.get('do_not_perform') is not False:
        add_field('Do not perform', 'do_not_perform', data.get('do_not_perform'))
    if data.get('context'):
        add_field('Context', 'context', data['context'])
    comparisons = [p for p in proposals if p['type'] == 'infusion_comparison']
    for index, proposal in enumerate(comparisons, 1):
        consistent = proposal['matches'] and proposal.get('constant_rate_asserted')
        fields.append(Field(f'Rate × duration comparison {index}', f"Recorded {proposal['recorded_value']} {proposal['unit']}",
            [f"Arithmetic: calculated {proposal['expected_value']} {proposal['unit']}; " + ('consistent within tolerance.' if consistent else 'discrepancy or unconfirmed constant delivery.')],
            'Keep recorded arithmetic' if consistent else 'Review discrepancy',
            'No numerical change; clinical dose unassessed' if consistent else 'Verify dose, rate, duration and interruptions; no replacement dose',
            'green' if consistent else 'red' if not proposal['matches'] else 'yellow', 'green' if consistent else 'red',
            'Do not replace the dose with the calculated quantity. Constant uninterrupted delivery: ' + str(proposal.get('constant_rate_asserted')),
            [proposal]))
    # Do not hide findings that the normalized source has no corresponding field for.
    for index, finding in enumerate(findings):
        if index not in covered_findings and finding['state'] in {'error', 'review'}:
            fields.append(Field(finding['path'], 'See source details', ['Consistency: ' + finding['message']], 'Clarify source',
                'No supported replacement', 'red' if finding['state'] == 'error' else 'yellow', 'red', sources=[finding]))
    return fields
