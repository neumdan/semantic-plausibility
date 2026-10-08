"""Real HTTP integration checks. No fixtures, mock servers or invented verdicts."""
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_batch, verify_audit
from semantic_plausibility.observation_services import NlmUcumClient, LoincFhirClient
from test_batch import configuration


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
