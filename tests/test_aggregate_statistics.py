"""Real adapters and arithmetic against independently calculated sample statistics."""
import csv
import json
from pathlib import Path
import sqlite3
import statistics
import tempfile
import unittest

from semantic_plausibility import run_plausibility_check, verify_audit
from semantic_plausibility.batch import BatchConfiguration
from semantic_plausibility.deployment import environment
from semantic_plausibility.aggregate_statistics import pool_summaries, supplied_statistics

ROOT = Path(__file__).resolve().parents[1]


class AggregateStatisticsTests(unittest.TestCase):
    def test_pooled_sample_sd_matches_individual_measurements(self):
        groups = [[1,3],[5,7]]
        result = pool_summaries([{'n':len(g),'mean':str(statistics.mean(g)), 'sd':str(statistics.stdev(g))} for g in groups])
        self.assertEqual(float(result['mean']), statistics.mean([1,3,5,7]))
        self.assertAlmostEqual(float(result['sd']), statistics.stdev([1,3,5,7]))
        self.assertIsNone(result['median'])
        self.assertIsNone(pool_summaries([{'n':None,'mean':'1'},{'n':None,'mean':'3'}])['mean'])

    def test_statistical_contract_rejects_ambiguous_or_inconsistent_summaries(self):
        for values in ({'value_mean':'2','value_min':'3'}, {'value_mean':'1','value_sd':'-1'},
                       {'value_mean':'NaN'}, {'value_sd':'1','value_sd_unit':'g/L'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                supplied_statistics(values, {'code':'g/dL'})
        with self.assertRaises(ValueError):
            pool_summaries([{'n':1,'mean':'2','sd':'1'}])

    def test_csv_and_sqlite_summaries_keep_same_units_counts_and_statistics(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            text=(ROOT/'tests/configurations/lab/aggregated.toml').read_text().replace('mode = "lookups"', 'mode = "offline"')
            text=text.replace('"../../../', '"'+str(ROOT)+'/')
            outputs=[]
            for kind in ('csv','sqlite'):
                selected=text
                if kind=='sqlite':
                    db_path=root/'lab.sqlite'
                    with (ROOT/'examples/lab/aggregated.csv').open(newline='') as f:
                        reader=csv.DictReader(f)
                        fields=reader.fieldnames
                        records=list(reader)
                    with sqlite3.connect(db_path) as db:
                        db.execute('CREATE TABLE lab ('+','.join('"'+key+'" TEXT' for key in fields)+')')
                        db.executemany('INSERT INTO lab VALUES ('+','.join('?' for _ in fields)+')',[[r[k] for k in fields] for r in records])
                    selected=selected.replace('type = "csv"','type = "sqlite"').replace(str(ROOT/'examples/lab/aggregated.csv'),str(db_path))+'\n[database]\nDB_TABLE="lab"\n'
                config=root/(kind+'.toml')
                config.write_text(selected)
                result=run_plausibility_check(config,output=root/(kind+'.csv'))
                with result.csv.open(encoding='utf-8-sig',newline='') as f:
                    rows=list(csv.DictReader(f))
                self.assertEqual(len(rows),4)
                for row in rows:
                    self.assertTrue(row['input.statistics.sd.unit'])
                    self.assertEqual(row['input.statistics.mean.unit'],row['unit_code'])
                    self.assertEqual(row['value_median'],'')
                self.assertEqual(verify_audit(result.log)['status'],'verified')
                outputs.append([(r['code'],r['value_mean'],r['value_sd'],r['occurrence_count']) for r in rows])
            self.assertEqual(outputs[0],outputs[1])

    def test_lab_examples_resolve_local_compose_model_without_network_access(self):
        for name in ("individual", "aggregated"):
            with self.subTest(name=name), environment({"SQC_IN_CONTAINER": "1"}):
                config = BatchConfiguration(ROOT / "tests/configurations/lab" / (name + ".toml"), research=True)
                with config.deployment.prepare(action="validate", research=True, source_access=False) as prepared:
                    model = json.loads(prepared.models_file.read_text())["models"][0]
                    self.assertEqual(model["base_url"], "http://ollama:11434/v1")
