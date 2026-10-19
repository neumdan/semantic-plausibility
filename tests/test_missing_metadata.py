"""Missing metadata versus contradictory data; service checks use real endpoints."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_batch
from semantic_plausibility.fhir_quality_pipeline import FhirQualityPipeline
from semantic_plausibility.observation_services import NlmUcumClient, LoincFhirClient
from semantic_plausibility.profile_config import assess_quantity, load_profile, missing_metadata_only
from semantic_plausibility.review_feedback import report_details
from semantic_plausibility.laboratory_report_table import laboratory_fields
from test_batch import ROOT, configuration

NAME = 'Potassium [Moles/volume] in Serum or Plasma'


def input_config(folder, *, connected=False, unit_system='', value='4'):
    config = configuration(folder, connected=connected)
    with (ROOT / 'examples/laboratory.csv').open(newline='') as stream:
        reader = csv.DictReader(stream)
        headers, row = reader.fieldnames, next(reader)
    # Synthetic input only. No fabricated terminology response or verdict.
    row.update(code='2823-3', code_system='', analyte_name=NAME, source_test=NAME,
               value=value, unit='mmol/L', unit_code='mmol/L', unit_system=unit_system,
               specimen='', reference_low='', reference_high='', reference_unit='', reference_unit_system='')
    data = folder / 'input.csv'
    with data.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerow(row)
    config.write_text(config.read_text().replace(str(ROOT / 'examples/laboratory.csv'), str(data)))
    return config, data


class MissingMetadataTests(unittest.TestCase):
    def test_formal_fhir_requirement_is_retained_as_missing_metadata(self):
        profile = load_profile('mii-kds-lab')
        policy = assess_quantity(profile, 'Observation.valueQuantity', {'code': 'mmol/L', 'unit': 'mmol/L'})
        self.assertEqual(policy['state'], 'invalid')  # qty-3 remains a formal conformance violation.
        self.assertTrue(missing_metadata_only(policy))
        conflicting = assess_quantity(profile, 'Observation.valueQuantity', {'code': 'mmol/L', 'unit': 'mmol/L', 'system': 'urn:local:units'})
        self.assertFalse(missing_metadata_only(conflicting))

    def test_offline_missing_metadata_is_review_not_definite_error(self):
        with tempfile.TemporaryDirectory() as name:
            config, data = input_config(Path(name))
            original = data.read_bytes()
            result = run_batch(config)
            row = report_details(result.csv, hydrate=True)[0][0]
            self.assertEqual(row['status'], 'unclear')
            self.assertEqual(row['metadata_status'], 'incomplete')
            self.assertEqual(int(row['detected_error_count']), 0)
            self.assertFalse(row['code_system'])
            self.assertFalse(row['unit_system'])
            fields = {field.name: field for field in laboratory_fields(row, {})}
            self.assertEqual(fields['Unit system'].source_state, 'yellow')
            self.assertEqual(fields['Unit system'].proposal_state, 'red')  # No real service evidence offline.
            self.assertEqual(data.read_bytes(), original)

    def test_explicit_system_conflict_and_invalid_number_stay_errors(self):
        for unit_system, value, field in [('urn:local:units', '4', 'Unit system'), ('', 'invalid', 'Value / statistics')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as name:
                config, _ = input_config(Path(name), unit_system=unit_system, value=value)
                row = report_details(run_batch(config).csv, hydrate=True)[0][0]
                self.assertEqual(row['status'], 'error')
                self.assertGreater(int(row['detected_error_count']), 0)
                self.assertEqual(next(f for f in laboratory_fields(row, {}) if f.name == field).source_state, 'red')


@unittest.skipUnless(os.getenv('SQC_LIVE_TESTS') == '1', 'Real LOINC and NLM UCUM services required')
class LiveMetadataTests(unittest.TestCase):
    def test_verified_candidates_in_lookup_only_report_with_source_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            config, data = input_config(Path(name), connected=True)
            original = data.read_bytes()
            result = run_batch(config)
            row = report_details(result.csv, hydrate=True)[0][0]
            self.assertEqual(row['status'], 'unclear')
            self.assertEqual(row['suggested_code'], '2823-3')
            self.assertEqual(row['suggested_code_system'], 'http://loinc.org')
            self.assertEqual(row['suggested_unit'], 'mmol/L')
            self.assertEqual(row['suggested_name'], NAME)
            self.assertFalse(row['code_system'])
            self.assertFalse(row['unit_system'])
            self.assertFalse(row['specimen'])
            self.assertFalse(row['suggested_factor'])
            suggestions = json.loads(row['laboratory_suggestions_json'])
            self.assertEqual(suggestions['selection_method'], 'exact_code_name_unit')
            self.assertFalse(suggestions['model_used'])
            fields = {field.name: field for field in laboratory_fields(row, {})}
            for key in ('Code', 'Code system', 'Unit code / display', 'Unit system', 'Specimen'):
                self.assertEqual(fields[key].proposal_state, 'yellow' if key == 'Specimen' else 'conditional')
            self.assertEqual(fields['Unit system'].recommendation, 'http://unitsofmeasure.org')
            self.assertIn('Ser/Plas', fields['Specimen'].recommendation)
            self.assertTrue(any((s.get('source') or '').startswith('https://ucum.nlm.nih.gov/') for s in fields['Unit system'].sources))
            self.assertTrue(any((s.get('source') or '').startswith('https://tx.fhir.org/') for s in fields['Code'].sources))
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary['metrics']['loinc_candidate_verified']['numerator'], 1)
            self.assertEqual(summary['metrics']['ucum_candidate_verified']['numerator'], 1)
            self.assertEqual(summary['metrics']['approved_mapping_ready']['numerator'], 0)
            self.assertEqual(data.read_bytes(), original)

    def test_exact_name_guard_and_cache_include_source_code(self):
        pipeline = FhirQualityPipeline(NlmUcumClient(), LoincFhirClient('https://tx.fhir.org/r4'))
        for code, name, selected in [('2823-3', NAME, '2823-3'), ('6298-4', NAME, None), ('2823-3', 'Hemoglobin', None)]:
            with self.subTest(code=code, name=name):
                observation = {'resourceType': 'Observation', 'code': {'coding': [{'code': code, 'display': name}]},
                               'valueQuantity': {'value': 4, 'code': 'mmol/L', 'unit': 'mmol/L'}}
                item = pipeline.check(observation)['measurements'][0]
                self.assertEqual(item['laboratory_suggestions']['selected_code'], selected)
                self.assertIsNone(item['ucum_valid'])
                self.assertEqual(item['quantity_policy']['state'], 'invalid')
                self.assertNotIn('system', observation['valueQuantity'])
