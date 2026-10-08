# Code, audit and development

## Public library

```python
from semantic_plausibility import run_batch, compare_reports, verify_audit

result = run_batch("pipeline.toml")
print(result.csv, result.report, result.summary, result.log)
print(result.input_records, result.distinct_tuples, result.statuses)
verify_audit(result.log)
# Matched declared cohort/period and distinct reports are required:
# compare_reports("lis.csv", ["fhir.csv", "hub.csv"], "output/comparison")
```

A successful `BatchResult` identifies complete local artifacts. Invalid configuration/input raises an error; failures during a run retain a redacted failure audit. The batch exits 0 when a report completes, including rows labelled `error`; exit 2 means the run failed.

## Follow the code

| Module | Responsibility |
| --- | --- |
| `batch.py` | TOML contract, role mapping, CSV/SQL selection and `run_batch` entry |
| `export_review.py` | Orchestration: prepare → standardize → group → check → publish |
| `export_grouping.py` | DuckDB grouping and frequency-aware decimal statistics |
| `audit.py` / `fhir_quality_pipeline.py` | Code/unit/profile checks and laboratory findings |
| `snapshot_services.py` / `terminology_router.py` | Local evidence and explicitly configured lookup adapters |
| `recommendations.py` | Review proposals, verified transformations and separate range findings |
| `export_report.py` | CSV projection, readiness definitions, HTML and audit log |
| `study_compare.py` | Distinct-report comparison and artifact integrity |
| `profile_import.py` | Selected rules from an installed resolved Observation snapshot |
| `observation_services.py` / `unit_catalog.py` / `model_schemas.py` | Optional retrieved evidence, catalogue candidates and grounded model replies |

## Stack and controls

Python **3.14** (checked 3.14.8); core **DuckDB 1.5.6**; optional **psycopg/psycopg-binary 3.3.6**. Development uses locked **Ruff 0.16.10**. Container: Python 3.14.8-slim, uv 0.11.24; optional Ollama 0.34.1. `pyproject.toml` defines the packages, `uv.lock` pins them, and each report records installed versions. No LangChain/JIT/compiler is needed for this workflow.

Database reads are read-only. DuckDB uses two threads and a 512 MB engine budget; extension downloads/loading are disabled. Frequencies are weighted without expanding repeated rows. Python histogram memory depends on unique numeric values per group. The HTML preview is limited to 2,000 rows; CSV remains complete.

Lookups are cached. A failed service abstains; three consecutive failures stop further calls to that connected source. Missing/partial evidence cannot prove invalidity. Model replies must satisfy schemas and source-quotation checks; candidates never erase deterministic errors. HTTP responses are bounded and redirects are not followed. Models are local/LAN only; secrets stay outside reports.

## Audit

The log records phases, versions, configuration/input/reference hashes, counters and redacted failure types. It avoids raw IDs/values/secrets. Detailed local CSV/evidence may contain sensitive measurement information. ID removal is not anonymisation.

```sh
plausibility verify output/review.audit.jsonl
```

Verification checks the hash chain and completed artifact hashes; it establishes integrity, not authorship or clinical truth. A prefix lock prevents concurrent writers. Temporary files are cleaned up; the summary publishes last as the completion marker. Edit expert decisions in a separate CSV copy.

## Test and publish

```sh
uv sync --locked --no-editable --extra postgres --group dev
uv run --no-sync ruff check src tests tools
uv run --no-sync python -m unittest discover -s tests -q
uv build --wheel
uv run --no-sync python tools/build_release.py
```

CI runs the same checks plus the offline example and three-source study. The public ZIP excludes private configuration/data and bundles the tested wheel. [Verification record](test-results.json) · [MII/FDPG scope](mii-quality.md) · [Study protocol](studies.md).
