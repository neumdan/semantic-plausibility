#!/usr/bin/env python3
"""Create/reuse the supplied synthetic SQLite source; never replace different data."""
import csv
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parents[1]
target = root / 'local/examples/lab.sqlite'
target.parent.mkdir(parents=True, exist_ok=True)
with (root / 'examples/lab/aggregated.csv').open(newline='') as stream:
    reader = csv.DictReader(stream)
    fields, rows = reader.fieldnames, list(reader)
values = [tuple(row[key] for key in fields) for row in rows]
if target.exists():
    with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as db:
        actual_fields = [row[1] for row in db.execute('PRAGMA table_info(laboratory_summary)')]
        actual = db.execute('SELECT * FROM laboratory_summary').fetchall() if actual_fields == fields else []
    if actual_fields != fields or sorted(actual) != sorted(values):
        raise SystemExit('Existing example database differs; retain it and select another input path')
    print(f'Reusing unchanged synthetic example: {target}')
else:
    with sqlite3.connect(target) as db:
        db.execute('CREATE TABLE laboratory_summary (' + ','.join('"' + key + '" TEXT' for key in fields) + ')')
        db.executemany('INSERT INTO laboratory_summary VALUES (' + ','.join('?' for _ in fields) + ')', values)
    print(f'Created synthetic source: {target}')
