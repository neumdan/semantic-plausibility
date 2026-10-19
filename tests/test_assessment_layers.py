"""Conditional matches and separate QC scopes; no invented service responses."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_batch, verify_audit
from semantic_plausibility.laboratory_assessment import assessment_layers, conditional_support
from semantic_plausibility.laboratory_report_table import laboratory_fields
from semantic_plausibility.report_comparison import Field, field_table_html
from semantic_plausibility.review_feedback import report_details
from semantic_plausibility.tabular_report import project
from test_batch import ROOT, configuration
from test_missing_metadata import NAME, input_config


class AssessmentContracts(unittest.TestCase):
    def test_missing_metadata_offline_does_not_become_a_conditional_match(self):
        with tempfile.TemporaryDirectory() as name:
            config, data = input_config(Path(name))
            before = data.read_bytes()
            result = run_batch(config)
            row = report_details(result.csv, hydrate=True)[0][0]
            layers = assessment_layers(row)
            self.assertEqual(layers['profile_qc']['state'], 'incomplete')
            self.assertEqual(layers['identity_set']['support_level'], 'none')
            self.assertTrue(all(layer['state'] not in {'supported', 'conditional'} for layer in layers.values()))
            self.assertEqual(data.read_bytes(), before)
            self.assertEqual(verify_audit(result.log)['status'], 'verified')

    def test_present_but_invalid_number_is_not_confused_with_missing_number(self):
        with tempfile.TemporaryDirectory() as name:
            config, _ = input_config(Path(name), unit_system='http://unitsofmeasure.org', value='invalid')
            row = report_details(run_batch(config).csv, hydrate=True)[0][0]
            layers = assessment_layers(row)
            self.assertTrue(layers['quantity_set']['complete'])
            self.assertEqual(layers['quantity_set']['state'], 'error')
            self.assertEqual(layers['quantity_set']['numeric_format'], 'invalid')
            self.assertEqual(layers['complete_set']['state'], 'error')

    def test_conditions_and_review_reasons_are_visible_and_escaped(self):
        field = Field('Code', '2823-3', ['Exact lookup'], 'Confirm', '2823-3',
                      source_state='conditional', proposal_state='conditional',
                      condition='If system = <LOINC>', reason='Confirm <source> metadata')
        page = field_table_html([field], prefix='test')
        self.assertIn('Conditional match', page)
        self.assertIn('Confirm condition', page)
        self.assertIn('If system = &lt;LOINC&gt;', page)
        self.assertIn('Why: Confirm &lt;source&gt; metadata', page)
        self.assertNotIn('<source>', page)


@unittest.skipUnless(os.getenv('SQC_LIVE_TESTS') == '1', 'Real LOINC and NLM UCUM services required')
class LiveAssessmentMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        config = configuration(folder, connected=True)
        with (ROOT / 'examples/laboratory.csv').open(newline='') as stream:
            reader = csv.DictReader(stream)
            headers, base = reader.fieldnames, next(reader)
        base.update(code='2823-3', code_system='http://loinc.org', analyte_name=NAME,
                    source_test=NAME, value='4', unit='mmol/L', unit_code='mmol/L',
                    unit_system='http://unitsofmeasure.org', specimen='Ser/Plas',
                    reference_low='3.5', reference_high='5.1', reference_unit='mmol/L',
                    reference_unit_system='http://unitsofmeasure.org', count='1')
        # These are illustrative input cases, not asserted clinical ranges.
        cases = {
            'missing_systems': dict(code_system='', unit_system='', specimen=''),
            'declared_complete': {},
            'invalid_unit': dict(unit='mmHg', unit_code='mmHg'),
            'invalid_number': dict(value='invalid'),
            'incompatible_unit': dict(unit='g/dL', unit_code='g/dL'),
            'wrong_name': dict(analyte_name='Hemoglobin', source_test='Hemoglobin'),
            'wrong_specimen': dict(specimen='Urine'),
            'candidate_wrong_specimen': dict(code_system='', unit_system='', specimen='Urine'),
            'missing_name': dict(code_system='', unit_system='', analyte_name='', source_test=''),
            'unit_display_only_match': dict(code_system='', unit_system='', unit_code='mmoll', unit='mmol/L'),
            'explicit_wrong_system': dict(code_system='urn:local:codes'),
            'reversed_bounds': dict(reference_low='5.1', reference_high='3.5'),
        }
        data = folder / 'matrix.csv'
        with data.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['id', *headers])
            writer.writeheader()
            for identifier, changes in cases.items():
                writer.writerow({**base, **changes, 'id': identifier})
        before = data.read_bytes()
        text = config.read_text().replace(str(ROOT / 'examples/laboratory.csv'), str(data))
        text = text.replace('granularity = "distinct"', 'granularity = "individual"')
        text = text.replace('mode = "distinct"', 'mode = "items"')
        text = text.replace('[input.columns]', '[input.columns]\nsource_id = "id"')
        text = text.replace('count = "count"', '')
        config.write_text(text)
        cls.result = run_batch(config)
        cls.rows = {row['source_id']: row for row in report_details(cls.result.csv, hydrate=True)[0]}
        cls.summary = json.loads(cls.result.summary.read_text())
        assert data.read_bytes() == before
        assert len(cls.rows) == len(cases)

    def layers(self, key):
        return assessment_layers(self.rows[key], self.summary)

    def test_literal_code_name_unit_are_conditionally_supported(self):
        row = self.rows['missing_systems']
        support = conditional_support(row)
        self.assertEqual(support['level'], 'code_name_unit')
        self.assertEqual(support['signals'], ['exact_code', 'matching_name', 'compatible_unit'])
        self.assertFalse(row['code_system'])
        self.assertFalse(row['unit_system'])
        self.assertEqual(int(row['detected_error_count']), 0)
        fields = {field.name: field for field in laboratory_fields(row, self.summary)}
        for name in ('Code', 'Analyte / name', 'Unit code / display'):
            self.assertEqual(fields[name].source_state, 'conditional')
            self.assertEqual(fields[name].proposal_state, 'conditional')
            self.assertTrue(fields[name].condition)
            self.assertTrue(fields[name].reason)
        for name in ('Code system', 'Unit system'):
            self.assertEqual(fields[name].source_state, 'yellow')
            self.assertEqual(fields[name].proposal_state, 'conditional')
        layers = self.layers('missing_systems')
        self.assertEqual(layers['profile_qc']['state'], 'incomplete')
        for name in ('identity_set', 'quantity_set', 'code_quantity_fit', 'complete_set'):
            self.assertEqual(layers[name]['state'], 'conditional')
            self.assertFalse(layers[name]['complete'])

    def test_complete_terminology_checks_do_not_establish_clinical_evidence(self):
        layers = self.layers('declared_complete')
        for name in ('profile_qc', 'identity_set', 'quantity_set', 'code_quantity_fit'):
            self.assertEqual(layers[name]['state'], 'supported', layers[name])
            self.assertTrue(layers[name]['complete'])
        self.assertEqual(layers['complete_set']['state'], 'needs_evidence')
        self.assertTrue(layers['complete_set']['complete'])
        self.assertEqual(layers['complete_set']['evidence_state'], 'not_searched')
        self.assertEqual(layers['complete_set']['clinical_correctness'], 'requires_clinician_review')

    def test_invalid_supplied_data_stays_error(self):
        for key in ('invalid_unit', 'invalid_number'):
            with self.subTest(key=key):
                layers = self.layers(key)
                self.assertEqual(layers['quantity_set']['state'], 'error')
                self.assertTrue(layers['quantity_set']['complete'])
                self.assertEqual(layers['complete_set']['state'], 'error')
        self.assertEqual(self.layers('reversed_bounds')['complete_set']['reference_state'], 'error')

    def test_valid_unit_does_not_prove_code_unit_compatibility(self):
        layers = self.layers('incompatible_unit')
        self.assertEqual(layers['quantity_set']['state'], 'supported')
        self.assertEqual(layers['code_quantity_fit']['state'], 'unverified')
        self.assertEqual(layers['complete_set']['state'], 'needs_review')

    def test_conflicting_name_and_specimen_are_distinguished(self):
        self.assertEqual(self.layers('wrong_name')['identity_set']['state'], 'unverified')
        self.assertEqual(self.layers('wrong_name')['identity_set']['support_level'], 'code_only')
        self.assertEqual(self.layers('wrong_specimen')['identity_set']['state'], 'error')
        candidate = self.layers('candidate_wrong_specimen')['identity_set']
        self.assertEqual(candidate['state'], 'needs_review')
        self.assertEqual(candidate['support_level'], 'code_name')
        self.assertEqual(self.layers('missing_name')['identity_set']['state'], 'incomplete')
        self.assertEqual(self.layers('missing_name')['identity_set']['support_level'], 'code_only')

    def test_declared_different_namespace_is_not_replaced_by_a_loinc_guess(self):
        row = self.rows['explicit_wrong_system']
        self.assertEqual(row['code_system'], 'urn:local:codes')
        self.assertFalse(conditional_support(row)['code_condition'])
        # The MII code binding permits additional local coding. Its membership is
        # unresolved here; a local namespace is not automatically a profile error.
        self.assertEqual(self.layers('explicit_wrong_system')['profile_qc']['state'], 'unverified')

    def test_unit_display_candidate_does_not_validate_the_different_input_code(self):
        row = self.rows['unit_display_only_match']
        support = conditional_support(row)
        self.assertFalse(support['unit_condition'])
        self.assertEqual(support['level'], 'code_name')
        fields = {field.name: field for field in laboratory_fields(row, self.summary)}
        self.assertEqual(fields['Unit code / display'].proposal_state, 'yellow')
        self.assertNotEqual(self.layers('unit_display_only_match')['code_quantity_fit']['state'], 'conditional')

    def test_csv_html_and_structured_projections_agree(self):
        for identifier, row in self.rows.items():
            with self.subTest(identifier=identifier):
                columns = project(row, {}, summary=self.summary)
                for name, layer in self.layers(identifier).items():
                    self.assertEqual(columns[f'qc.{name}.state'], layer['state'])
                    self.assertEqual(columns[f'qc.{name}.complete'], layer['complete'])
                for field in laboratory_fields(row, self.summary):
                    import re
                    key = re.sub(r'[^a-z0-9]+', '_', field.name.lower()).strip('_')
                    self.assertEqual(columns[f'assessment.{key}.source_status'], field.source_state)
                    self.assertEqual(columns[f'assessment.{key}.proposal_status'], field.proposal_state)
        self.assertEqual(verify_audit(self.result.log)['status'], 'verified')
