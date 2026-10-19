"""Separate article retrieval from sourced extraction; one readable study evidence table."""
import csv
import json
from html import escape


def counts(candidates):
    return {'articles_retrieved': len({r.get('source') for r in candidates}),
            'extracted_measurements': sum(r.get('evidence_type') == 'reported_measurement' and r.get('reported_value') is not None for r in candidates),
            'extracted_analytical_limits': sum(r.get('evidence_type') == 'analytical_limit' and r.get('reported_value') is not None for r in candidates),
            'extracted_intervals': sum(r.get('evidence_type') in {'reference_interval', 'analytical_interval', 'documented_extreme'} and (r.get('low') is not None or r.get('high') is not None) for r in candidates),
            'extracted_unclassified_ranges': sum(r.get('evidence_type') == 'unclassified_numeric_range' for r in candidates),
            'unit_only_records': sum(r.get('evidence_type') == 'reported_measurement' and bool(r.get('unit')) and r.get('reported_value') is None for r in candidates),
            'articles_without_supported_extraction': len({r.get('source') for r in candidates if r.get('evidence_type') == 'unclassified_discovery'}),
            'articles_with_failed_extraction': len({r.get('source') for r in candidates if r.get('status') == 'extraction_unavailable'})}


def write_studies(path, rows):
    fields = ['loinc', 'tuple_ids', 'evidence_type', 'status', 'value_kind', 'reported_value', 'low', 'high', 'unit', 'unit_ucum', 'unit_validation_state', 'unit_validation_source', 'source_text_rendering', 'population', 'specimen', 'method', 'source_title', 'source', 'doi', 'excerpt', 'source_sha256', 'retrieved_at']
    entries = {}
    for row in rows:
        for item in json.loads(row.get('study_evidence_json') or '[]'):
            key = (item.get('candidate_loinc') or row.get('code'), json.dumps(item, sort_keys=True))
            entry = entries.setdefault(key, {**item, 'loinc': key[0], 'tuple_ids': []})
            entry['tuple_ids'].append(row['tuple_id'])
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        from .support import _csv_text
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows({k: _csv_text(';'.join(v) if k=='tuple_ids' else v) if isinstance(v, (str, list)) else v for k,v in r.items() if k in fields} for r in entries.values())
    return {**counts(list(entries.values())), 'records': len(entries),
            'scope': 'Distinct article counts are global; multiple statistics or LOINCs can refer to one article. Retrieval is not a supported extraction or clinical approval.'}


def html(summary, csv_name):
    if summary.get('operation') != 'research-evidence':
        return ''
    from pathlib import Path
    research = summary.get('research', {})
    data = research.get('evidence_counts', {})
    name = Path(csv_name).with_suffix('.studies.csv').name
    labels = {'articles_retrieved': 'Distinct articles retrieved', 'extracted_measurements': 'Quoted numerical measurements',
        'extracted_analytical_limits': 'Quoted analytical limits (LoD / LoQ / LoB)',
        'extracted_intervals': 'Quoted intervals', 'extracted_unclassified_ranges': 'Unclassified ranges',
        'unit_only_records': 'Unit-only evidence', 'articles_without_supported_extraction': 'Articles without supported extraction',
        'articles_with_failed_extraction': 'Articles with failed/rejected extraction', 'records': 'Article/LOINC evidence records'}
    table = '<table><thead><tr><th>Measure</th><th>Count</th></tr></thead><tbody>' + ''.join(
        '<tr><th scope="row">' + label + '</th><td>' + escape(str(data.get(key, 'not recorded'))) + '</td></tr>'
        for key, label in labels.items()) + '</tbody></table>'
    return '<section><h2>Study evidence: retrieval versus extraction</h2><p>' + escape(data.get('scope', 'No study retrieval in lookup-only mode.')) + '</p><p><a href="'+escape(name, quote=True)+'">Open study evidence CSV: statistics, units, quotations and sources</a></p>' + table + '<p>Input means and SDs are descriptive summaries. Published intervals and study statistics remain context-dependent review evidence; no pooled population reference interval is inferred.</p></section>'
