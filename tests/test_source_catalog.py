"""Configuration contracts and optional real UCUM service tests; no fake lookup replies."""

from copy import deepcopy
import csv
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.source_catalog import read_catalog, resolve_catalog
from semantic_plausibility.observation_services import NlmUcumClient

ROOT = Path(__file__).resolve().parents[1]


class SourceCatalogTests(unittest.TestCase):
    def test_template_api_file_and_combined_selections_resolve_without_inventing_data(self):
        from test_batch import configuration
        with tempfile.TemporaryDirectory() as name:
            path = configuration(Path(name), connected=True)
            template = path.read_text().replace('source = "loinc-public-test"', 'source = "loinc-api"')
            for api, local in ((True, False), (False, True), (True, True)):
                for strategy in ('fallback', 'all'):
                    with self.subTest(api=api, local=local, strategy=strategy):
                        selected = template.replace('[lookups.loinc]\nenabled = true',
                                                    '[lookups.loinc]\nenabled = ' + str(api).lower())
                        selected = selected.replace('[lookups.loinc_local]\nenabled = false',
                                                    '[lookups.loinc_local]\nenabled = ' + str(local).lower())
                        selected = selected.replace('version = ""', 'version = "2.83"')
                        selected = selected.replace('strategy = "fallback"', 'strategy = "' + strategy + '"')
                        path.write_text(selected)
                        # Parse/routing configuration only; no fabricated file/API replies.
                        config = BatchConfiguration(path)
                        self.assertEqual(config.lookup_strategy, strategy)
                        self.assertEqual(config.data['lookups']['loinc']['enabled'], api)
                        self.assertEqual(config.data['lookups']['loinc']['type'], 'loinc')
                        file_source = config.data['lookups']['loinc_local']
                        self.assertEqual(file_source['enabled'], local)
                        self.assertEqual(file_source['type'], 'loinc_csv')
                        self.assertEqual(file_source['systems'], ['http://loinc.org'])
                        self.assertNotIn('password_env', file_source)

    def test_enabling_the_file_requires_the_operator_to_fill_its_edition(self):
        from test_batch import configuration
        with tempfile.TemporaryDirectory() as name:
            path = configuration(Path(name))
            path.write_text(path.read_text().replace('[lookups.loinc_local]\nenabled = false',
                                                     '[lookups.loinc_local]\nenabled = true'))
            with self.assertRaisesRegex(ValueError, 'downloaded release version'):
                BatchConfiguration(path)

    def test_default_api_local_file_and_container_routes_are_explicit(self):
        config = BatchConfiguration(ROOT / 'pipeline.example.toml')
        self.assertEqual(config.data['lookups']['loinc']['endpoint'], 'https://fhir.loinc.org')
        self.assertEqual(config.data['lookups']['loinc']['username_env'], 'LOINC_FHIR_USERNAME')
        self.assertEqual(config.data['lookups']['ucum']['endpoint'], 'https://ucum.nlm.nih.gov/ucum-service/v1')
        data = {'catalog': {'path': str(ROOT / 'catalog.csv')}, 'lookups': {
            'loinc': {'source': 'loinc-file', 'enabled': True, 'version': '2.83', 'complete': True},
            'ucum': {'source': 'ucum-local', 'enabled': True}}}
        native = deepcopy(data)
        with patch.dict(os.environ, {'SQC_IN_CONTAINER': '0'}):
            resolve_catalog(native, ROOT)
        with patch.dict(os.environ, {'SQC_IN_CONTAINER': '1'}):
            resolve_catalog(data, ROOT)
        self.assertEqual(native['lookups']['ucum']['endpoint'], 'http://127.0.0.1:8084/ucum-service/v1')
        self.assertEqual(data['lookups']['ucum']['endpoint'], 'http://ucum:8080/ucum-service/v1')
        self.assertEqual(data['lookups']['loinc']['path'], 'local/terminologies/loinc/Loinc.csv')
        self.assertNotIn('username_env', data['lookups']['loinc'])

    def test_selected_api_credentials_fail_explicitly_when_missing(self):
        from semantic_plausibility.terminology_lookup import lookup_terminology
        with patch.dict(os.environ, {'LOINC_FHIR_USERNAME': '', 'LOINC_FHIR_PASSWORD': ''}):
            with self.assertRaisesRegex(ValueError, 'Missing username'):
                lookup_terminology(ROOT / 'pipeline.example.toml', 'loinc', code='718-7')

    def test_file_secret_overrides_catalogue_environment_reference(self):
        data = {'catalog': {'path': str(ROOT / 'catalog.csv')}, 'lookups': {
            'loinc': {'source': 'loinc-api', 'enabled': True, 'username_file': 'local/secrets/user',
                      'password_file': 'local/secrets/password'}}}
        resolve_catalog(data, ROOT)
        self.assertNotIn('username_env', data['lookups']['loinc'])
        self.assertNotIn('password_env', data['lookups']['loinc'])
        self.assertEqual(data['lookups']['loinc']['password_file'], 'local/secrets/password')

    def test_unknown_and_duplicate_source_ids_fail_before_network_access(self):
        with self.assertRaisesRegex(ValueError, 'Unknown source'):
            resolve_catalog({'lookups': {'loinc': {'source': 'misspelled-source'}}}, ROOT)
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'catalog.csv'
            text = (ROOT / 'catalog.csv').read_text()
            path.write_text(text + text.splitlines()[1] + '\n')
            with self.assertRaisesRegex(ValueError, 'unique'):
                read_catalog(path)

    def test_catalogue_rejects_embedded_credentials(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'catalog.csv'
            path.write_text((ROOT / 'catalog.csv').read_text().replace('https://fhir.loinc.org', 'https://user:password@fhir.loinc.org'))
            with self.assertRaisesRegex(ValueError, 'embedded credentials'):
                read_catalog(path)

    def test_aggregate_example_contains_numbers_and_a_single_measurement_unit(self):
        with (ROOT / 'examples/lab/aggregated.csv').open(newline='') as stream:
            reader = csv.DictReader(stream)
            self.assertFalse(set(reader.fieldnames) & {'mean_unit', 'sd_unit', 'min_unit', 'max_unit'})
            for row in reader:
                for field in ('count', 'mean', 'sd', 'min', 'max'):
                    self.assertTrue(Decimal(row[field]).is_finite())
                self.assertTrue(row['unit_code'])

    def test_public_tree_contains_no_downloaded_definitions(self):
        for folder in ('src', 'examples'):
            for path in (ROOT / folder).rglob('*.json'):
                resource = json.loads(path.read_text())
                self.assertNotIn(resource.get('resourceType') if isinstance(resource, dict) else '',
                                 {'StructureDefinition', 'CodeSystem', 'ValueSet'}, str(path))
        self.assertFalse((ROOT / 'src/semantic_plausibility/terminologies').exists())
        self.assertEqual(sorted(p.name for p in ROOT.glob('compose*.yaml')), ['compose.yaml'])


@unittest.skipUnless(os.getenv('SQC_TEST_UCUM_URL'), 'Set SQC_TEST_UCUM_URL to the actual local UCUM service')
class LiveLocalUcumTests(unittest.TestCase):
    def test_real_grammar_substitution_and_conversion_guards(self):
        client = NlmUcumClient(os.environ['SQC_TEST_UCUM_URL'])
        for code in ('g/dL', '10*3/uL', 'mg/dL', 'mg/mL'):
            self.assertIs(client.is_valid(code), True, code)
        for code in ('not-a-unit', 'Gauss'):
            self.assertIs(client.is_valid(code), False, code)
        self.assertEqual(client.factor('g/dL', 'g/L'), Decimal('10'))
        self.assertLess(abs(client.factor('mg/mL', 'g/L') - 1), Decimal('1e-12'))
        self.assertIsNone(client.factor('g/dL', 'mmol/L'))
        self.assertIsNone(client.factor('Cel', 'K'))
