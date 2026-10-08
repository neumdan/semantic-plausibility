"""Run synthetic LIS, FHIR and mapped-database exports through the actual library."""
import argparse
from pathlib import Path

from semantic_plausibility.batch import run_batch
from semantic_plausibility.study_compare import compare_reports
from semantic_plausibility.synthetic_study import config_text, create_examples, create_hub_database

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", type=Path, default=Path("output/study-demo"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
folder = args.out.resolve()
if folder.exists():
    parser.error("Choose a new output folder; the demo never deletes existing data")
rows = create_examples(folder, root / "examples/studies/reference-pack.synthetic.json")
create_hub_database(folder / "hub.sqlite", rows)
reports = []
for source, kind in (("lis", "csv"), ("fhir", "fhir_file"), ("cds-hub-demo", "sqlite")):
    config = folder / (source + ".toml")
    config.write_text(config_text(folder, kind=kind, source=source, output=folder / (source + ".review.csv")))
    result = run_batch(config)
    reports.append(result.csv)
    print(f"{source}: {result.input_records} source rows -> {result.distinct_tuples} groups; {result.report}")
compare_reports(reports[0], reports[1:], folder / "comparison")
print(f"Study comparison: {folder / 'comparison.html'}")
