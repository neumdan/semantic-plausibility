"""Text-processing contracts; no simulated terminology or model responses."""
import csv
import json
import os
from pathlib import Path
import unittest

from semantic_plausibility.study_sources import study_text, unit_key, unit_tokens, source_unit, ucum_spelling, quantity_exclusion, provider_query, ambiguous_analyte_clause
from semantic_plausibility.model_schemas import _analyte_guard, validate_grounding, guard_study_parts, resolve_sentences, ground_study_units


class StudyTextTests(unittest.TestCase):
    def test_markup_preserves_powers_and_unit_case(self):
        for text in ('10<sup>3</sup>/µL', '10&lt;sup&gt;3&lt;/sup&gt;/µL'):
            self.assertEqual(study_text(text), '10^3/µL')
        self.assertEqual(study_text('103/µL'), '103/µL')
        self.assertEqual(study_text('mg<script>ignore</script>/dL'), 'mg/dL')

    def test_typography_matches_retain_the_literal_source_span(self):
        text = 'A quantity in × 10^3/µL was printed.'
        for claim in ('×10³/μL', '10*3/uL', '10^3/µL'):
            self.assertEqual(source_unit(claim, text), '× 10^3/µL')
        self.assertEqual(ucum_spelling('× 10^3/µL'), '10*3/uL')
        self.assertEqual(unit_key('mg·L⁻¹'), unit_key('mg/L'))
        for reciprocal in ('mg L^-1', 'mg L-1', 'mg L⁻¹'):
            self.assertEqual(source_unit('mg/L', reciprocal), reciprocal)
            self.assertEqual(ucum_spelling(reciprocal), 'mg/L')

    def test_scale_denominator_and_case_cannot_be_invented(self):
        for claim, source in [('10^3/uL', '103/uL'), ('10^3/uL', '10^6/uL'),
                              ('10^3/uL', '10^9/L'), ('mg/dL', 'Mg/dL'),
                              ('g/dL', 'mg/dL'), ('g/L', 'g/dL'), ('10^3/uL', '10^3')]:
            with self.subTest(claim=claim, source=source):
                self.assertIsNone(source_unit(claim, source))

    def test_unit_extraction_does_not_join_unrelated_text(self):
        self.assertEqual(unit_tokens('A list: g/dL; × 10^3/µL; 250 participants.'), ['g/dL', '× 10^3/µL'])

    def test_numbers_cannot_be_borrowed_from_another_named_clause(self):
        # Parser contract, not a terminology/model response.
        row = {'excerpt': 'Analyte A: 1–2 mg/L; analyte B: 3–4 mg/L.'}
        evidence = {'analyte_definition': {'component': 'Analyte A'}}
        _analyte_guard(row, evidence, ['1', '2', 'mg/L'])
        with self.assertRaises(ValueError):
            _analyte_guard(row, evidence, ['3', '4', 'mg/L'])
        row['excerpt'] = row['excerpt'].replace(';', ',')
        with self.assertRaises(ValueError):
            _analyte_guard(row, evidence, ['3', '4', 'mg/L'])
        row['excerpt'] = 'Analyte A <1 mg/L.'
        with self.assertRaises(ValueError):
            _analyte_guard(row, evidence, ['1', 'mg/L'])

    def test_differences_and_thresholds_are_not_measurements(self):
        self.assertTrue(quantity_exclusion('mean difference of 0.8 g/dL', 'g/dL', 'MCnc'))
        self.assertTrue(quantity_exclusion('cut-point of 13.5 g/dL', 'g/dL', 'MCnc'))
        self.assertTrue(quantity_exclusion('minimum incidence at 95–105 g/L', 'g/L', 'MCnc'))
        self.assertTrue(quantity_exclusion('median fraction of 72.2 %', '%', 'MCnc'))
        self.assertFalse(quantity_exclusion('reference interval below a threshold', 'g/dL', 'MCnc', bounds=True))
        self.assertFalse(quantity_exclusion('median of 13.90 g/dL', 'g/dL', 'MCnc'))

    def test_queries_restrict_quoted_terms_to_title_and_abstract(self):
        query = '("Analyte A") AND ("reference interval")'
        self.assertEqual(provider_query(query, 'europe_pmc'), '(TITLE_ABS:"Analyte A") AND (TITLE_ABS:"reference interval")')
        self.assertEqual(provider_query(query, 'pubmed'), '("Analyte A"[Title/Abstract]) AND ("reference interval"[Title/Abstract])')

    def test_conjoined_analyte_numbers_are_not_assigned_by_guessing(self):
        self.assertTrue(ambiguous_analyte_clause('total haemoglobin and HbF concentrations', ['Hemoglobin', 'Haemoglobin']))
        self.assertTrue(ambiguous_analyte_clause('HbF and total haemoglobin concentrations', ['Hemoglobin', 'Haemoglobin']))
        self.assertTrue(ambiguous_analyte_clause('WBC and ANC values', ['Leukocytes', 'WBC']))
        self.assertFalse(ambiguous_analyte_clause('WBC: 2.43–7.05 × 10^3/µL', ['Leukocytes', 'WBC']))


@unittest.skipUnless(os.getenv('SQC_STUDY_REPORT'), 'Provide a real completed Ollama research CSV')
class RecordedStudyTests(unittest.TestCase):
    def test_real_extractions_keep_quotes_units_and_review_state(self):
        from semantic_plausibility import verify_audit
        from semantic_plausibility.laboratory_report_table import laboratory_fields
        path = Path(os.environ['SQC_STUDY_REPORT'])
        summary = json.loads(path.with_suffix('.summary.json').read_text())
        trace = [json.loads(line) for line in path.with_suffix('.trace.jsonl').read_text().splitlines()]
        self.assertEqual(verify_audit(path.with_suffix('.audit.jsonl'))['status'], 'verified')
        self.assertGreater(summary['research']['evidence_counts']['extracted_measurements'], 0)
        self.assertGreater(summary['research']['evidence_counts']['extracted_intervals'], 0)
        for call in trace:
            if call.get('kind') == 'model' and call.get('state') == 'accepted_by_source_guards':
                validate_grounding(call['instruction'], call['response'], call['evidence'])
                if call.get('task') == 'STUDY_EXTRACT':
                    grounded, _ = ground_study_units(resolve_sentences(call['model_response'], call['evidence']))
                    accepted, rejected = guard_study_parts(grounded, call['evidence'])
                    self.assertEqual(accepted, call['response'])
                    self.assertEqual(rejected, call['extraction_rejections'])
        with path.open(encoding='utf-8-sig') as stream:
            for row in csv.DictReader(stream):
                self.assertEqual(row['approved_mapping_ready'], 'False')
                fields = {f.name: f for f in laboratory_fields(row, summary)}
                if row.get('value_numeric_ready') == 'False':
                    self.assertEqual(fields['Value / statistics'].source_state, 'red')
                    self.assertEqual(fields['Value / statistics'].proposal_state, 'red')
                records = json.loads(row['study_evidence_json'])
                for record in records:
                    if record.get('status') != 'candidate':
                        continue
                    self.assertTrue(record['source'].startswith('https://'))
                    self.assertEqual(len(record['source_sha256']), 64)
                    self.assertTrue(record['excerpt'])
                    if record.get('unit_ucum'):
                        self.assertEqual(record['unit_validation_state'], 'valid')
                        self.assertEqual(record['unit_ucum'], ucum_spelling(record['unit']))
                        self.assertTrue(record['unit_validation_source'])
                if any(r.get('status') == 'candidate' and r.get('evidence_type') == 'reference_interval' for r in records):
                    self.assertEqual(fields['Reference ranges'].proposal_state, 'yellow')


@unittest.skipUnless(os.getenv('SQC_STUDY_REGRESSION_TRACE'), 'Provide the actual earlier Ollama diagnostic trace')
class RecordedGuardRegressionTests(unittest.TestCase):
    def test_real_bias_and_fraction_responses_are_now_rejected(self):
        from semantic_plausibility import research_prompts
        calls = [json.loads(line) for line in Path(os.environ['SQC_STUDY_REGRESSION_TRACE']).read_text().splitlines()]
        checked = 0
        for call in calls:
            if call.get('task') != 'STUDY_EXTRACT' or call.get('state') != 'accepted_by_source_guards':
                continue
            response, evidence = call['response'], call['evidence']
            if not any(quantity_exclusion(r['excerpt'], r['unit'], evidence['analyte_definition']['property'])
                       for r in response['measurements']):
                continue
            with self.subTest(article=evidence['title']), self.assertRaises(ValueError):
                validate_grounding(research_prompts.STUDY_EXTRACT, response, evidence)
            checked += 1
        self.assertGreaterEqual(checked, 2)

    def test_partial_guards_preserve_only_independently_supported_claims(self):
        from semantic_plausibility import research_prompts
        calls = [json.loads(line) for line in Path(os.environ['SQC_STUDY_REGRESSION_TRACE']).read_text().splitlines()]
        partial = 0
        for call in calls:
            if call.get('task') != 'STUDY_EXTRACT' or not call.get('model_response'):
                continue
            evidence = call['evidence']
            grounded, _ = ground_study_units(resolve_sentences(call['model_response'], evidence))
            accepted, rejected = guard_study_parts(grounded, evidence)
            validate_grounding(research_prompts.STUDY_EXTRACT, accepted, evidence)
            partial += bool(rejected and (accepted['measurements'] or accepted['bounds']['low'] is not None))
        self.assertGreater(partial, 0)


if __name__ == '__main__':
    unittest.main()
