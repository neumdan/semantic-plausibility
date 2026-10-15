"""Domain configuration and opt-in real terminology services; no HTTP fixtures."""
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import lookup_terminology, run_plausibility_check, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.deployment_checks import check_configuration

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'terminology.example.toml'


class DomainConfigurationTests(unittest.TestCase):
    def test_all_medication_recipes_select_an_actual_resource_and_pinned_guide(self):
        for path in (ROOT / 'examples/medication').glob('*.toml'):
            with self.subTest(path=path.name):
                config = BatchConfiguration(path)
                self.assertIn(config.kind, {'Medication', 'MedicationRequest', 'MedicationAdministration', 'MedicationStatement'})
                self.assertEqual(config.data['specification']['IG_VERSION'], '2026.0.1')
                self.assertTrue(config.path_value(config.input['path']).is_file())
        self.assertFalse(BatchConfiguration(ROOT / 'examples/medication/terminology.toml').validation['enabled'])

    def test_mtls_configuration_preserves_relative_paths_and_rejects_partial_credentials(self):
        base = BatchConfiguration(CONFIG)
        row = dict(base.data['lookups']['mii_termserv'], enabled=True)
        transformed = base.lookup_settings({'mii': row})['sources'][0]
        self.assertEqual(transformed['client_key'], 'local/secrets/mii-client.key')
        for override in ({'client_key': ''}, {'client_cert': ''}, {'type': 'csv'}):
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, 'mTLS requires both'):
                base.lookup_settings({'mii': {**row, **override}})
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'pipeline.toml'
            text = (ROOT / 'pipeline.example.toml').read_text().replace('"examples/', '"' + str(ROOT / 'examples') + '/')
            text += '\n[lookups.mii]\nenabled=true\ntype="fhir"\nendpoint="https://ontoserver.mii-termserv.de/fhir"\nsystems=["http://snomed.info/sct"]\nclient_cert="local/secrets/missing.crt"\nclient_key="local/secrets/missing.key"\n'
            path.write_text(text)
            # Readiness fails locally. No network call and no invented certificate.
            report = check_configuration(path)
            self.assertFalse(report['passed'])
            check = next(c for c in report['checks'] if c['component'] == 'client_certificate:mii')
            self.assertEqual(check['state'], 'unavailable')
            self.assertEqual(check['error_type'], 'FileNotFoundError')
            with BatchConfiguration(path).deployment.prepare(action='validate', source_access=False) as prepared:
                entries = json.loads(prepared.audit.terminology_sources.read_text())['sources']
                entry = next(e for e in entries if e['alias'] == 'mii')
                self.assertEqual(entry['client_cert'], str((path.parent / 'local/secrets/missing.crt').resolve()))
                self.assertEqual(entry['client_key'], str((path.parent / 'local/secrets/missing.key').resolve()))


@unittest.skipUnless(os.getenv('SQC_LIVE_TESTS') == '1', 'Opt in to actual public terminology services')
class PublicDomainTerminologyTests(unittest.TestCase):
    def check_real(self, alias, code):
        result = lookup_terminology(CONFIG, alias, code=code)
        self.assertEqual(result['state'], 'valid', result)
        self.assertTrue(result['metadata']['display'])
        self.assertTrue(result['metadata']['version'])
        self.assertTrue(result['source'].startswith('https://tx.fhir.org/'))
        return result

    def test_who_atc(self): self.check_real('atc_who', 'N02BE01')
    def test_snomed_ingredient(self): self.check_real('snomed', '387517004')
    def test_rxnorm_ingredient(self): self.check_real('rxnorm', '161')
    def test_unii_ingredient(self): self.check_real('unii', '362O9ITL9D')
    def test_edqm_form(self): self.check_real('edqm', '10219000')

    def test_complete_medication_example_keeps_sources_in_csv_and_html(self):
        with tempfile.TemporaryDirectory() as name:
            result = run_plausibility_check(ROOT / 'examples/medication/terminology.toml', output=Path(name) / 'report.csv')
            self.assertEqual(result.distinct_tuples, 2)
            with result.csv.open(newline='') as f:
                rows = list(csv.DictReader(f))
            for row in rows:
                self.assertIn('https://tx.fhir.org/r4', row['sources_json'])
            evidence = [json.loads(line) for line in result.csv.with_suffix('.evidence.jsonl').read_text().splitlines()]
            verified = {(item['system'], item['code']) for row in evidence for item in row['terminology'] if item['state'] == 'valid'}
            for alias, code in [('atc_who', 'N02BE01'), ('snomed', '387517004'), ('rxnorm', '161'), ('unii', '362O9ITL9D'), ('edqm', '10219000')]:
                system = BatchConfiguration(CONFIG).data['lookups'][alias]['systems'][0]
                self.assertIn((system, code), verified)
            self.assertEqual(result.statuses, {'needs_review': 2})
            report = result.report.read_text()
            for text in ('161', '362O9ITL9D', '10219000', 'https://tx.fhir.org/r4'):
                self.assertIn(text, report)
            self.assertEqual(verify_audit(result.log)['status'], 'verified')


@unittest.skipUnless(os.getenv('SQC_MII_TERMINOLOGY_CONFIG'), 'Supply a private TOML with authorised MII client certificate; no public substitute')
class MiiTerminologyTests(unittest.TestCase):
    def test_configured_national_medication_sources(self):
        # These are real codes used in the published MII Medication guide.
        for system, code in [('http://fhir.de/CodeSystem/bfarm/atc', 'N02BE01'),
                             ('http://fhir.de/CodeSystem/ask', '00002'),
                             ('http://fhir.de/CodeSystem/ifa/pzn', '06312077')]:
            with self.subTest(system=system):
                result = lookup_terminology(Path(os.environ['SQC_MII_TERMINOLOGY_CONFIG']), 'mii_termserv', system=system, code=code)
                self.assertEqual(result['state'], 'valid', result)
                self.assertTrue(result['metadata']['display'])
