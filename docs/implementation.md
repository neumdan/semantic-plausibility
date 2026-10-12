# Process, code and verification

The named check reads an export, writes local artifacts and exits. Evidence research and clinician review import are separate commands.

| Step | Implementation |
| --- | --- |
| Parse TOML, source roles, selected R4 rules | `batch.py`, `profile_import.py`, `profile_config.py` |
| Read CSV/FHIR/SQLite/PostgreSQL | `audit.py`, `batch_quality.py`, `db_source.py`, `export_review.py` |
| Guarded grouping and weighted statistics | `export_grouping.py` (embedded DuckDB) |
| Real UCUM syntax/factors and LOINC metadata | `observation_services.py` |
| Additional configured CodeSystems/ValueSets | `terminology_router.py`, `terminology_validation.py` |
| Pinned package setup / aliases / optional OLS and WHO APIs | `terminology_packages.py`, `name_checks.py`, `terminology_apis.py`; `lookup` inspects one source |
| Code–unit fit, references and recommendations | `fhir_quality_pipeline.py`, `recommendations.py` |
| Optional local-model extraction, code/name proposals and guards | `laboratory_suggestions.py`, `research_prompts.py`, `model_schemas.py`, `unit_catalog.py` |
| Profile-derived service plan | `profile_lookups.py`; primary/source/optional routes |
| Clinician decisions and scoped reuse | `review_feedback.py`; append history, target rechecks and context guards |
| Separate evidence dossier and prompt/query trace | `evidence_research.py`; audited export, bounded PubMed/Europe PMC retrieval |
| CSV/HTML, audit and comparisons | `export_report.py`, `study_compare.py` |

The command prints input, active lookup sources and model enablement. The CSV/evidence file contain row-level findings; the summary records source versions, timing, coverage and failed lookup operations. The audit is a redacted hash chain with artifact hashes. Detailed reports can contain sensitive measurements.

Lookup results are cached per run. Three consecutive request failures open a source circuit; affected findings stay unverified. Model inputs exclude measured values and identifiers, permit only local/LAN endpoints, bound response sizes and require supporting quotations. No automatic coding/value rewrite occurs.

## Libraries

Python 3.14; DuckDB 1.5.6; optional psycopg/psycopg-binary 3.3.6; uv 0.11.24 in the Python 3.14.8-slim container. Versions are locked and reported per run. Ollama is an optional separate service. No agent orchestration framework is needed.

## Tests

```sh
uv sync --locked --no-editable --extra postgres --group dev
uv run --no-sync ruff check src tests tools
uv run --no-sync python -m unittest discover -s tests -v
SQC_LIVE_TESTS=1 uv run --no-sync python -m unittest discover -s tests -v
```

Core tests check grouping, input/output contracts, profile import, audit integrity and schema/security guards without claiming terminology validity. Live tests contact NLM, the public FHIR terminology server, the BfArM registry/public HDDT package and SemLookP. They check actual examples/versions/factors/aliases, package checksum and modified-file detection, then replay only captured real lookup responses. There are no mock terminology/model servers or fabricated lookup fixtures. Service failures fail the live tests; they are not reported as successful verification.

Protected BfArM translation tests require `SQC_BFARM_ACCEPT_DOWNLOAD_CONDITIONS=1` after accepting the linked terms. WHO tests require `SQC_WHO_LIVE_TESTS=1`, `WHO_ICD_CLIENT_ID` and `WHO_ICD_CLIENT_SECRET`. These are explicit opt-ins; skipped integrations are not counted as verified. No protected package content is committed.

Run from a fresh output folder. PostgreSQL/hospital integration and local-model clinical accuracy need site-specific studies. The application complements MII FHIR conformance/availability checks; it is not a FHIR validator.
