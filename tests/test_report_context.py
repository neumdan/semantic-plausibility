"""Report provenance and secret handling using real input adapters, without lookup doubles."""
import json
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility.review_feedback import report_details
from semantic_plausibility import run_plausibility_check, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.deployment import environment
from semantic_plausibility.report_context import run_context, item_context, item_context_html, safe_location
from test_batch import configuration
from test_medication import configuration as medication_configuration


class ReportContextTests(unittest.TestCase):
    def test_lab_record_matches_run_and_does_not_copy_credentials(self):
        with tempfile.TemporaryDirectory() as name:
            path = configuration(Path(name))
            text = path.read_text().replace('[lookups.loinc]', '[lookups.loinc]\nusername_env="REPORT_TEST_USER"\npassword_env="REPORT_TEST_PASSWORD"')
            path.write_text(text)
            with environment({'REPORT_TEST_USER': 'private-user-marker', 'REPORT_TEST_PASSWORD': 'private-password-marker'}):
                result = run_plausibility_check(path)
            summary = json.loads(result.summary.read_text())
            context = summary['run_context']
            self.assertEqual(context['input']['columns']['code'], 'code')
            self.assertEqual(context['level'], 'A1')
            self.assertIsNone(context['research']['model'])
            self.assertEqual(context['input']['granularity'], 'distinct')
            rows, _ = report_details(result.csv, hydrate=True)
            for row in rows:
                item = json.loads(row['assessment_context_json'])
                self.assertEqual(item['configuration_sha256'], summary['configuration_sha256'])
                self.assertEqual(item['tuple_id'], row['tuple_id'])
                self.assertNotIn('source_id', item['input'])
                self.assertEqual(item['output']['status'], row['status'])
                self.assertFalse(item['output']['automatic_changes'])
                self.assertTrue(item['methods'])
            for output in (result.csv, result.report, result.summary, result.log):
                body = output.read_text()
                self.assertNotIn('private-password-marker', body)
                self.assertNotIn('private-user-marker', body)
            html = result.report.read_text()
            for title in ('Configuration and methods', 'How this item was assessed', 'Assumptions and limits', 'Output and improvement step'):
                self.assertIn(title, html)
            self.assertEqual(verify_audit(result.log)['status'], 'verified')

    def test_medication_keeps_source_assumptions_and_profile(self):
        with tempfile.TemporaryDirectory() as name:
            result = run_plausibility_check(medication_configuration(Path(name), 'administration'))
            summary = json.loads(result.summary.read_text())
            self.assertEqual(summary['run_context']['resource_type'], 'MedicationAdministration')
            self.assertEqual(summary['run_context']['target']['IG_VERSION'], '2026.0.1')
            rows, _ = report_details(result.csv, hydrate=True)
            for row in rows:
                item = json.loads(row['assessment_context_json'])
                self.assertIn('Constant uninterrupted delivery is explicitly asserted by the source.', item['assumptions_and_limits'])
                self.assertTrue(item['lookup_evidence'])
                self.assertTrue(item['output']['next_step'])
                self.assertIn('run-level', result.report.read_text())
            self.assertEqual(verify_audit(result.log)['status'], 'verified')

    def test_research_record_identifies_original_input_and_preserved_verdict(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder)
            result = run_plausibility_check(path)
            summary = json.loads(result.summary.read_text())
            # Build the research context without invoking a model or inventing responses.
            path.write_text(path.read_text().replace('mode = "offline"', 'mode = "lookups"'))
            config = BatchConfiguration(path, research=True, research_scope='studies')
            with config.deployment.prepare(research=True, source_access=False) as prepared:
                context = run_context(config, prepared, output_path=folder/'research.csv', source_report=result.csv, source_summary=summary)
            self.assertEqual(context['level'], 'A3')
            self.assertEqual(context['input']['original_input'], summary['run_context']['input'])
            self.assertFalse(context['input']['hospital_data_requeried'])
            self.assertTrue(context['output']['path'].endswith('/research.csv'))
            self.assertIn('europe_pmc', context['research']['study_providers'])
            row = report_details(result.csv, hydrate=True)[0][0]
            detail = json.loads(result.csv.with_suffix('.evidence.jsonl').read_text().splitlines()[0])
            item = item_context(context, row, detail)
            self.assertEqual(item['check_result_basis'], 'original_A1_report')
            self.assertEqual(item['output']['status'], row['status'])
            self.assertTrue(any('inherited' in x for x in item['assumptions_and_limits']))

    def test_urls_are_redacted_and_source_input_is_escaped(self):
        self.assertEqual(safe_location('https://user:secret@example.org/fhir?api_key=hidden#fragment'), 'https://example.org/fhir')
        with tempfile.TemporaryDirectory() as name:
            result = run_plausibility_check(configuration(Path(name)))
            row = report_details(result.csv, hydrate=True)[0][0]
            item = json.loads(row['assessment_context_json'])
            item['assumptions_and_limits'].append('<script>unexpected-source-text</script>')
            row['assessment_context_json'] = json.dumps(item)
            rendered = item_context_html(row)
            self.assertNotIn('<script>', rendered)
            self.assertIn('&lt;script&gt;', rendered)


if __name__ == '__main__':
    unittest.main()
