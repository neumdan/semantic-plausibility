#!/usr/bin/env python3
"""Create two synthetic SQLite tables; reuse matching data and preserve existing data."""
import csv
from contextlib import closing
from pathlib import Path
import sqlite3


def prepare_database(root):
    target = root / 'local/examples/lab.sqlite'
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as database, database:
        for table, filename in (("laboratory_values", "individual.csv"),
                                ("laboratory_summary", "aggregated.csv")):
            with (root / 'examples/lab' / filename).open(newline='') as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames
                rows = [tuple(row[key] for key in fields) for row in reader]
            # Table and column names come only from the shipped example contract.
            existing = [row[1] for row in database.execute(f'PRAGMA table_info({table})')]
            if existing:
                actual = database.execute(f'SELECT * FROM {table}').fetchall() if existing == fields else []
                if existing != fields or sorted(actual) != sorted(rows):
                    raise SystemExit(f'Preserve the existing {table} data; choose another example directory')
                print(f'Reuse {table}: {len(rows)} matching source rows')
                continue
            database.execute(f'CREATE TABLE {table} (' + ','.join('"' + key + '" TEXT' for key in fields) + ')')
            database.executemany(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in fields) + ')', rows)
            print(f'Create {table}: {len(rows)} synthetic source rows')
    print(f'Example database ready: {target}')
    return target


if __name__ == '__main__':
    prepare_database(Path(__file__).resolve().parents[1])
