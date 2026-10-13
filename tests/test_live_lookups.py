"""Real HTTP integration checks. No fixtures, mock servers or invented verdicts."""
from decimal import Decimal
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_batch, verify_audit
from semantic_plausibility.observation_services import NlmUcumClient, LoincFhirClient
from semantic_plausibility.name_checks import check_name
from test_batch import configuration, ROOT


@unittest.skipUnless(os.getenv("SQC_LIVE_TESTS") == "1", "Set SQC_LIVE_TESTS=1 to contact real terminology services")
class LiveLookupTests(unittest.TestCase):
    def test_ucum_syntax_and_linear_conversion_against_nlm(self):
        client = NlmUcumClient()
        self.assertTrue(client.is_valid('g/dL'))
        self.assertTrue(client.is_valid('10*3/uL'))
        self.assertTrue(client.is_valid('10*6'))
        self.assertFalse(client.is_valid('mmHg'))
        self.assertEqual(client.factor('g/L', 'g/dL'), Decimal('0.1'))
        self.assertIsNone(client.factor('Cel', 'K'))
        self.assertIsNone(client.factor('10*6', '10*3/uL'))

    def test_real_fhir_loinc_metadata_units_and_requested_version(self):
        client = LoincFhirClient('https://tx.fhir.org/r4')
        for code, unit in [('718-7', 'g/dL'), ('6690-2', '10*3/uL')]:
            term = client.lookup(code)
            self.assertIsNotNone(term)
            self.assertEqual(term.code, code)
            self.assertTrue(term.version)
            self.assertIn(unit, term.example_ucum_units)
            self.assertEqual(term.system, 'Bld')
            self.assertEqual(term.scale, 'Qn')
            self.assertEqual(term.time_aspect, 'Pt')
            self.assertTrue(term.component)
            self.assertIn('COMPONENT', term.axis_sources)
            self.assertEqual(term.axis_sources['COMPONENT']['version'], term.version)
            self.assertTrue(term.source.startswith('https://tx.fhir.org/'))
            self.assertEqual(client.lookup(code, term.version).version, term.version)

    def test_connected_export_and_capture_of_real_responses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = configuration(root, connected=True)
            pack = root / 'lookups.json'
            config.write_text(config.read_text().replace('[output]', '[output]\nlookup_pack=' + json.dumps(str(pack))))
            result = run_batch(config)
            self.assertEqual(result.distinct_tuples, 7)
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary['evidence_kind'], 'configured_lookup_services')
            self.assertEqual(summary['lookup_errors'], [])
            self.assertEqual(verify_audit(result.log)['status'], 'verified')
            evidence = json.loads(pack.read_text())
            self.assertEqual(evidence['evidence_kind'], 'captured_lookup_responses')
            self.assertEqual(evidence['ucum']['g/dL'], True)
            self.assertEqual(len(evidence['loinc']), 2)
            self.assertEqual(summary['loinc_editions'], sorted({term['version'] for term in evidence['loinc']}))
            self.assertEqual(summary['lookup_sources']['loinc'], 'https://tx.fhir.org/r4')
            # Re-use only this captured real evidence; no artificial responses.
            text = config.read_text().replace('mode = "lookups"', 'mode = "offline"')
            text = text.replace('[input]', '[input]\nsnapshot=' + json.dumps(str(pack)))
            text = text.replace(str(root / 'review.csv'), str(root / 'saved.csv'))
            text = text.replace('lookup_pack=' + json.dumps(str(pack)), '')
            config.write_text(text)
            saved = run_batch(config)
            self.assertEqual(saved.statuses, result.statuses)
            self.assertEqual(verify_audit(saved.log)['status'], 'verified')

    def test_real_german_loinc_aliases_and_unresolved_name(self):
        term = LoincFhirClient('https://tx.fhir.org/r4').lookup('718-7')
        self.assertTrue(term.designations)
        self.assertEqual(check_name('Hämoglobin', 'http://loinc.org', '718-7', term=term)['state'], 'exact_alias')
        self.assertEqual(check_name('Hb', 'http://loinc.org', '718-7', term=term)['state'], 'exact_alias')
        self.assertEqual(check_name('Glucose', 'http://loinc.org', '718-7', term=term)['state'], 'unverified')

    def test_analyte_name_column_reaches_actual_batch_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = configuration(root, connected=True)
            with (ROOT / 'examples/laboratory.csv').open(newline='') as handle:
                reader = csv.DictReader(handle)
                fields, row = reader.fieldnames, next(reader)
            input_file = root / 'named.csv'
            row['analyte_name'] = 'Hämoglobin'
            with input_file.open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=[*fields, 'analyte_name'])
                writer.writeheader()
                writer.writerow(row)
            text = config.read_text().replace(str(ROOT / 'examples/laboratory.csv'), str(input_file))
            text = text.replace('analyte_name = "source_test"', 'analyte_name = "analyte_name"')
            config.write_text(text)
            result = run_batch(config)
            with result.csv.open(newline='') as handle:
                report = next(csv.DictReader(handle))
            self.assertEqual(report['analyte_name'], 'Hämoglobin')
            self.assertEqual(report['name_check'], 'exact_alias')
            self.assertEqual(report['code'], '718-7')
            evidence = json.loads(report['name_evidence_json'])
            self.assertTrue(evidence['analyte'][0]['matched_aliases'])
            source = json.loads(report['source_data_json'])
            linked = json.loads(report['linked_data_json'])
            proposal = json.loads(report['recommendation_set_json'])
            self.assertEqual(source['name'], 'Hämoglobin')
            self.assertTrue(source['reference_ranges'])
            self.assertEqual(linked['code']['code'], '718-7')
            self.assertEqual(linked['code']['canonical_name'], report['canonical_name'])
            self.assertTrue(linked['code']['sources'][0]['source'].startswith('https://tx.fhir.org/'))
            self.assertEqual(proposal['code']['code'], '718-7')
            self.assertTrue(proposal['code']['version'])
            self.assertFalse(proposal['automatic_changes'])
            self.assertIn('Source set', result.report.read_text())
            self.assertIn('Linked terminology', result.report.read_text())
            self.assertIn('Target profile and resources', result.report.read_text())
            self.assertEqual(verify_audit(result.log)['status'], 'verified')
