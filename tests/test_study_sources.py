"""Text-processing contracts; no simulated terminology or model responses."""
import csv
import json
import os
from pathlib import Path
import unittest

from semantic_plausibility.review_feedback import report_details
from semantic_plausibility.study_sources import study_text, unit_key, unit_tokens, source_unit, ucum_spelling, quantity_exclusion, provider_query, ambiguous_analyte_clause, analyte_clauses, analytical_limit_matches
from semantic_plausibility.model_schemas import _analyte_guard, validate_grounding, guard_study_parts, resolve_sentences, ground_study_units, evidence_schema, validate_response


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

    def test_parenthesized_units_and_sex_strata_stay_with_their_analyte(self):
        text = 'Analyte A (AA; mg/L): Male (1-2), Female (3-4); Analyte B: 5-6 mg/L.'
        clauses = analyte_clauses(text, ['Analyte A', 'AA'])
        self.assertEqual(clauses, [text.split('; Analyte B')[0]])
        self.assertNotIn('5-6', clauses[0])
        text = 'Reference intervals (AA: 1-2 mg/L; BB: 3-4 mg/L).'
        self.assertEqual(analyte_clauses(text, ['AA']), ['Reference intervals (AA: 1-2 mg/L'])

    def test_analytical_limits_require_their_label_and_correct_number(self):
        text = 'Analyte A LoD and LoQ were 0.05 mg/L and 0.1 mg/L.'
        self.assertTrue(quantity_exclusion(text, 'mg/L'))
        self.assertFalse(quantity_exclusion(text, 'mg/L', analytical=True))
        for role, correct, wrong in [('limit_of_detection', '0.05', '0.1'),
                                     ('limit_of_quantification', '0.1', '0.05')]:
            self.assertTrue(analytical_limit_matches(text, role, correct, 'mg/L'))
            self.assertFalse(analytical_limit_matches(text, role, wrong, 'mg/L'))
        self.assertFalse(analytical_limit_matches(text, 'limit_of_blank', '0.05', 'mg/L'))
        self.assertFalse(analytical_limit_matches('Analyte A LoD and LoQ: 0.05 mg/L.',
                                                'limit_of_detection', '0.05', 'mg/L'))
        self.assertFalse(analytical_limit_matches('Analyte A LoD and LoQ: 0.05 mg/L.',
                                                'limit_of_quantification', '0.05', 'mg/L'))
        self.assertTrue(analytical_limit_matches('Analyte A LoD = 0.05 mg/L, LoQ = 0.1 mg/L.',
                                               'limit_of_quantification', '0.1', 'mg/L'))

    def test_interference_quantities_stay_excluded_in_every_category(self):
        for bounds, analytical in [(False, False), (True, False), (False, True)]:
            self.assertTrue(quantity_exclusion('No interference from analyte A at 500 mg/L.',
                                              'mg/L', bounds=bounds, analytical=analytical))

    def test_reference_bounds_cannot_merge_population_strata(self):
        from semantic_plausibility.research_prompts import STUDY_BOUNDS
        # Text-parser contract; this is not a terminology or model service response.
        text = 'Reference intervals for analyte A (AA; mg/L): Male (1-2), Female (3-4).'
        evidence = {'abstract': text, 'analyte_definition': {'aliases': ['AA']}}
        bounds = {'evidence_type': 'reference_interval', 'low': '1', 'high': '2',
                  'unit': 'mg/L', 'excerpt': text, 'population': '', 'specimen': '', 'method': ''}
        validate_grounding(STUDY_BOUNDS, bounds, evidence)
        with self.assertRaisesRegex(ValueError, 'one printed interval'):
            validate_grounding(STUDY_BOUNDS, {**bounds, 'high': '4'}, evidence)

    def test_mean_in_analyte_name_does_not_change_statistic_role(self):
        from semantic_plausibility.research_prompts import STUDY_MEASUREMENTS
        text = 'Mean corpuscular hemoglobin concentration (MCHC) 314 g/L.'
        evidence = {'abstract': text, 'analyte_definition': {
            'aliases': ['MCHC', 'Mean Corpuscular Hemoglobin Concentration']}}
        row = {'reported_value': '314', 'unit': 'g/L', 'value_kind': 'individual_value',
               'excerpt': text, 'population': '', 'specimen': '', 'method': ''}
        validate_grounding(STUDY_MEASUREMENTS, {'measurements': [row]}, evidence)
        with self.assertRaisesRegex(ValueError, 'statistic role'):
            validate_grounding(STUDY_MEASUREMENTS, {'measurements': [{**row, 'value_kind': 'mean'}]}, evidence)

    def test_printed_sex_strata_are_selected_as_whole_source_pairs(self):
        from semantic_plausibility.research_prompts import STUDY_EXTRACT
        text = 'Reference intervals for analyte A (AA; mg/L): Male (1-2), Female (3-4).'
        evidence = {'abstract': text, 'excerpt_candidates': [text],
                    'analyte_definition': {'aliases': ['AA']}}
        schema = evidence_schema(STUDY_EXTRACT, evidence)
        self.assertEqual(schema['properties']['bounds']['enum'], ['', 'i1', 'i2'])
        selected = {'bounds': 'i2', 'measurements': []}
        validate_response(selected, schema)
        result = resolve_sentences(selected, evidence)
        self.assertEqual((result['bounds']['low'], result['bounds']['high']), ('3', '4'))
        self.assertEqual(result['bounds']['population'], 'Female (3-4)')
        self.assertEqual(result['bounds']['excerpt'], text)
        validate_grounding(STUDY_EXTRACT, result, evidence)
        with self.assertRaises(ValueError):
            resolve_sentences({**selected, 'bounds': 'invented'}, evidence)


@unittest.skipUnless(os.getenv('SQC_STUDY_REPORT'), 'Provide a real completed Ollama research CSV')
class RecordedStudyTests(unittest.TestCase):
    def test_csv_and_html_fields_have_identical_decisions_and_sources(self):
        from semantic_plausibility.laboratory_report_table import laboratory_fields
        import re
        path = Path(os.environ['SQC_STUDY_REPORT'])
        summary = json.loads(path.with_suffix('.summary.json').read_text())
        with path.open(encoding='utf-8-sig', newline='') as handle:
            csv_rows = {row['tuple_id']: row for row in csv.DictReader(handle)}
        for row in report_details(path, hydrate=True)[0]:
            recorded = csv_rows[row['tuple_id']]
            for field in laboratory_fields(row, summary):
                prefix = 'assessment.' + re.sub(r'[^a-z0-9]+', '_', field.name.lower()).strip('_')
                with self.subTest(tuple_id=row['tuple_id'], field=field.name):
                    self.assertEqual(recorded[prefix + '.source_status'], field.source_state)
                    self.assertEqual(recorded[prefix + '.proposal_status'], field.proposal_state)
                    self.assertEqual(recorded[prefix + '.action'], field.action)
                    self.assertEqual(recorded[prefix + '.recommendation'], field.recommendation)
                    expected_sources = {s['source'] for s in field.sources if s.get('source')}
                    actual_sources = {v for k, v in recorded.items()
                                      if k.startswith(prefix + '.source.') and k.endswith('.source') and v}
                    self.assertEqual(actual_sources, expected_sources)

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
        for row in report_details(path, hydrate=True)[0]:
            self.assertIn(row['approved_mapping_ready'], (False, 'False'))
            fields = {f.name: f for f in laboratory_fields(row, summary)}
            if row.get('value_numeric_ready') in (False, 'False'):
                self.assertEqual(fields['Value / statistics'].source_state, 'red')
                self.assertEqual(fields['Value / statistics'].proposal_state, 'red')
            records = json.loads(row['study_evidence_json'])
            analytical = [r for r in records if r.get('evidence_type') == 'analytical_limit']
            if analytical:
                self.assertEqual(fields['Analytical limits'].proposal_state, 'yellow')
                for record in analytical:
                    self.assertNotIn(record, fields['Value / statistics'].sources)
                    self.assertNotIn(record, fields['Reference ranges'].sources)
                    self.assertNotIn(record, json.loads(row['reference_candidates_json']))
                    self.assertNotIn(record, json.loads(row['reported_measurements_json']))
                    self.assertIn(record, fields['Analytical limits'].sources)
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
    def test_real_misclassified_quantities_are_now_rejected(self):
        from semantic_plausibility import research_prompts
        calls = [json.loads(line) for line in Path(os.environ['SQC_STUDY_REGRESSION_TRACE']).read_text().splitlines()]
        checked = 0
        for call in calls:
            if call.get('task') != 'STUDY_EXTRACT' or call.get('state') != 'accepted_by_source_guards':
                continue
            response, evidence = call['response'], call['evidence']
            definition = evidence['analyte_definition']
            exclusions = {quantity_exclusion(clause, r['unit'], definition['property'])
                          for r in response['measurements']
                          for clause in analyte_clauses(r['excerpt'], definition['aliases'])}
            if not exclusions.intersection({'method_difference_is_not_an_analyte_result',
                                            'percentage_is_not_the_requested_concentration',
                                            'interference_experiment_is_not_an_analyte_result',
                                            'analytical_limit_requires_its_own_category'}):
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
