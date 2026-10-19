"""Cross-source grouping and evidence hand-off; no simulated terminology replies."""
import csv
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from semantic_plausibility import research_plan, run_plausibility_check, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.progress import phase_event

ROOT = Path(__file__).resolve().parents[1]


def individual_config(folder, filename="individual.toml"):
    text = (ROOT / 'tests/configurations/lab/individual.toml').read_text()
    text = text.replace('"../../../', '"' + str(ROOT) + '/')
    text = text.replace('mode = "lookups"', 'mode = "offline"')
    path = folder / filename
    path.write_text(text)
    return path


class InputWorkflowTests(unittest.TestCase):
    def test_individual_csv_fhir_and_database_have_same_aggregate_evidence_input(self):
        """Independent sources containing 12, 14 and 16 must yield one n=3, mean=14, SD=2 group."""
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = individual_config(folder)
            original = config.read_text()
            resources = []
            for index, value in enumerate((12, 14, 16), 1):
                quantity = {'unit': 'g/dL', 'code': 'g/dL', 'system': 'http://unitsofmeasure.org'}
                resources.append({'resourceType': 'Observation', 'id': f'example-{index}', 'status': 'final',
                    'code': {'coding': [{'code': '718-7', 'system': 'http://loinc.org'}], 'text': 'Hemoglobin'},
                    'valueQuantity': dict(quantity, value=value),
                    'referenceRange': [{'low': dict(quantity, value=12), 'high': dict(quantity, value=16)}]})
            bundle = folder / 'input.bundle.json'
            bundle.write_text(json.dumps({'resourceType': 'Bundle', 'type': 'collection',
                                          'entry': [{'resource': r} for r in resources]}))
            db_path = folder / 'input.sqlite'
            with (ROOT / 'examples/lab/individual.csv').open(newline='') as stream:
                reader = csv.DictReader(stream)
                fields, rows = reader.fieldnames, list(reader)
            with closing(sqlite3.connect(db_path)) as database, database:
                database.execute('CREATE TABLE lab (' + ','.join('"' + f + '" TEXT' for f in fields) + ')')
                database.executemany('INSERT INTO lab VALUES (' + ','.join('?' for _ in fields) + ')',
                                     [tuple(r[f] for f in fields) for r in rows])
                database.execute('CREATE TABLE resources (resource_json TEXT)')
                database.executemany('INSERT INTO resources VALUES (?)', [(json.dumps(r),) for r in resources])
            outputs = []
            for source in ('csv', 'fhir_file', 'sqlite', 'sqlite_json'):
                text = original
                if source != 'csv':
                    path = bundle if source == 'fhir_file' else db_path
                    text = text.replace('type = "csv"', 'type = "' + ('sqlite' if source == 'sqlite_json' else source) + '"')
                    text = text.replace(str(ROOT / 'examples/lab/individual.csv'), str(path))
                if source in ('fhir_file', 'sqlite_json'):
                    start, end = text.index('[input.columns]'), text.index('[specification]')
                    columns = '[input.columns]\nresource_json="resource_json"\n' if source == 'sqlite_json' else ''
                    text = text[:start] + columns + '[input.context]\nloinc_system="Bld"\n' + text[end:]
                if source.startswith('sqlite'):
                    text += '\n[database]\nDB_TABLE="' + ('resources' if source == 'sqlite_json' else 'lab') + '"\n'
                config.write_text(text)
                checked = run_plausibility_check(config, output=folder / (source + '.csv'))
                self.assertEqual((checked.input_records, checked.distinct_tuples), (3, 1))
                with checked.csv.open(encoding='utf-8-sig', newline='') as stream:
                    row = next(csv.DictReader(stream))
                self.assertNotIn('source_id', row)
                numbers = tuple(float(row[k]) for k in ('occurrence_count', 'value_mean', 'value_sd',
                                                        'value_min', 'value_max', 'value_median'))
                self.assertEqual(numbers, (3, 14, 2, 12, 16, 14))
                self.assertEqual(row['input.statistics.sd.unit'], 'g/dL')
                self.assertEqual(verify_audit(checked.log)['status'], 'verified')
                plan = research_plan(config, checked.csv, scope='studies')
                self.assertEqual(plan['checked_groups'], 1)
                self.assertEqual(len(plan['targets']), 1)
                outputs.append(numbers)
            self.assertTrue(all(row == outputs[0] for row in outputs))

    def test_research_requires_audited_grouping_even_after_configuration_change(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = individual_config(folder)
            grouped = config.read_text()
            variants = (
                grouped.replace('mode = "distinct"', 'mode = "items"'),
                grouped + '\n[aggregation]\ngroup_by=["code","code_system","unit","unit_system","reference_range","value"]\n',
            )
            for index, text in enumerate(variants):
                config.write_text(text)
                checked = run_plausibility_check(config, output=folder / f'separate-{index}.csv')
                self.assertEqual(checked.distinct_tuples, 3)
                config.write_text(grouped)
                with self.assertRaisesRegex(ValueError, 'Prepare the evidence input with output.mode'):
                    research_plan(config, checked.csv, scope='studies')
                config.write_text(text.replace('enabled = false\nscope', 'enabled = true\nscope'))
                with self.assertRaisesRegex(ValueError, 'For an AI/evidence batch'):
                    BatchConfiguration(config)

    def test_specimen_name_unit_reference_and_status_keep_groups_separate(self):
        from semantic_plausibility.audit import SourceRecord
        from semantic_plausibility.export_grouping import ExportGroups
        resource = json.loads((ROOT / 'examples/lab/observation.json').read_text())
        changes = [deepcopy(resource) for _ in range(5)]
        changes[0]['code']['text'] = 'Different source name'
        changes[1]['valueQuantity']['code'] = 'g/L'
        changes[2]['referenceRange'][0]['high']['value'] = 18
        changes[3]['status'] = 'preliminary'
        records = [SourceRecord(resource, {'loinc_system': 'Bld'}, 'original')]
        records.extend(SourceRecord(r, {'loinc_system': 'Ser' if i == 4 else 'Bld'}, str(i))
                       for i, r in enumerate(changes))
        with tempfile.TemporaryDirectory() as name:
            groups = ExportGroups(Path(name), mode='distinct', group_value=False)
            try:
                self.assertEqual(groups.ingest(records)['groups'], 6)
            finally:
                groups.close()

    def test_progress_explains_counts_and_withholds_arbitrary_details(self):
        with self.assertLogs('semantic_plausibility', level='INFO') as captured:
            phase_event('grouped', {'individual_records': 3, 'groups': 1, 'token': 'SECRET', 'value': 42})
        text = '\n'.join(captured.output)
        self.assertIn('semantic grouping completed', text)
        self.assertIn('individual records=3', text)
        self.assertIn('groups=1', text)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('42', text)

    def test_sqlite_example_setup_is_repeatable_and_preserves_existing_rows(self):
        spec = importlib.util.spec_from_file_location('prepare_example', ROOT / 'tools/prepare-lab-examples.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / 'examples/lab').mkdir(parents=True)
            for filename in ('individual.csv', 'aggregated.csv'):
                (folder / 'examples/lab' / filename).write_bytes((ROOT / 'examples/lab' / filename).read_bytes())
            target = module.prepare_database(folder)
            module.prepare_database(folder)
            with closing(sqlite3.connect(target)) as database, database:
                self.assertEqual(database.execute('SELECT count(*) FROM laboratory_values').fetchone()[0], 3)
                self.assertEqual(database.execute('SELECT count(*) FROM laboratory_summary').fetchone()[0], 6)
                database.execute("UPDATE laboratory_values SET value='99' WHERE id='example-1'")
            with self.assertRaises(SystemExit):
                module.prepare_database(folder)
            with closing(sqlite3.connect(target)) as database:
                self.assertEqual(database.execute("SELECT value FROM laboratory_values WHERE id='example-1'").fetchone()[0], '99')

    def test_every_native_fhir_recipe_has_explicit_research_setup(self):
        for name in ('fhir',):
            with self.subTest(name=name):
                config = BatchConfiguration(ROOT / 'tests/configurations/lab' / (name + '.toml'), research=True)
                self.assertEqual(config.output['mode'], 'distinct')
                self.assertEqual(config.data['research']['sources'], ['europe_pmc', 'pubmed'])
                self.assertIn('local', config.data['models'])
