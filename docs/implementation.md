# Process, code and verification

The named check reads an export, writes local artifacts and exits. Evidence research and clinician review import are separate commands.

| Step | Implementation |
| --- | --- |
| Parse TOML, source roles, selected R4 rules | `batch.py`, `profile_import.py`, `profile_config.py` |
| Read CSV/FHIR/SQLite/PostgreSQL | `audit.py`, `batch_quality.py`, `db_source.py`, `export_review.py` |
| Guarded grouping and weighted statistics | `export_grouping.py` (embedded DuckDB) |
| Real UCUM syntax/factors and LOINC metadata | `observation_services.py`; `local_terminology.py` reads official LOINC CSV and preserves published ATC/DDD text |
| Local full FHIR/IG validation | `fhir_validation.py`, pinned Matchbox/HL7 or MII service |
| Source → linked terminology → complete proposal and citations | `report_sources.py`, provider-origin metadata in `observation_services.py` |
| Additional configured CodeSystems/ValueSets | `terminology_router.py`, `terminology_validation.py` |
| Pinned package setup / aliases / optional OLS and WHO APIs | `terminology_packages.py`, `name_checks.py`, `terminology_apis.py`; `lookup` inspects one source |
| Code–unit fit, references and recommendations | `fhir_quality_pipeline.py`, `recommendations.py` |
| Optional local-model extraction, code/name proposals and guards | `laboratory_suggestions.py`, `research_prompts.py`, `model_schemas.py`, `unit_catalog.py` |
| Profile-derived service plan | `profile_lookups.py`; primary/source/optional routes |
| Clinician decisions and scoped reuse | `review_feedback.py`; append history, target rechecks and context guards |
| Separate evidence dossier and prompt/query trace | `evidence_research.py`; scope=lookup for terminology AI, scope=studies adds bounded PubMed/Europe PMC retrieval |
| Medication input, checks and reports | `medication_input.py`, `medication_checks.py`, `medication_report.py`; full regimen grouping, real source routing, conditional strength/infusion arithmetic |
| CSV/HTML, audit and comparisons | `export_report.py`, `study_compare.py` |

The command logs input type, resource, enabled source aliases and phases via `progress.py`; `logging.level` or `--log-level` controls DEBUG/INFO/WARNING/ERROR output. The CSV/evidence file contain row-level findings; the summary records source versions, timing, coverage and failed lookup operations. The audit is a redacted hash chain with artifact hashes. Detailed reports can contain sensitive measurements.

Lookup results are cached per run. Laboratory lookups open a source circuit after three consecutive request failures; medication caches code/unit results and failed conversions per run; affected findings stay unverified. Model inputs exclude measured values and identifiers, permit only local/LAN endpoints, bound response sizes and require supporting quotations. No automatic coding/value rewrite occurs.

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

Run from a fresh output folder. PostgreSQL/hospital integration and local-model clinical accuracy need site-specific studies. The application complements MII FHIR conformance/availability checks; it delegates full resource/guide validation to the local Matchbox/HL7 or MII service. [Setup and scopes](validation.md).

## Validation container compatibility

Matchbox 4.1.21 and MII validator 0.0.1-alpha.8 are pinned; the latter uses the optional `compose.mii.yaml` overlay. Matchbox runs on an internal network with its configured local Blaze service. Its image build installs the real guide/dependencies. The generated ephemeral H2 inventory widens Matchbox 4.1.21's `TYPE` column to accommodate MII datatype canonical identifiers; no published guide resource is edited. Server AI error analysis/statistics storage are disabled.

Live FHIR tests require a running local Matchbox with the real MII guide: `SQC_LIVE_FHIR_VALIDATION_TESTS=1 uv run --no-sync python -m unittest discover -s tests -p test_fhir_validation.py -v`. The valid/invalid source inputs are illustrative; every validation response comes from the actual service.

## Current verification

Version **0.13.0**, 14 October 2026: adds MedicationStatement/Medication adapters, local official LOINC CSV support, published ATC/DDD context, local-first/all-source routing with visible attempts, configuration summaries and log levels. SQLite rejects missing mapped columns before querying (quoted unknown names must never become literal values).

The offline suite ran **86 tests: 61 passed, 25 optional integrations skipped**. Real public checks exercised NLM/LOINC, source-name provenance, captured-response replay, BfArM metadata, an unprotected real gematik package, SemLookP and the already-downloaded BfArM laboratory supplements. All **15 medication tests passed** with real UCUM/medication services enabled. Full official Loinc.csv and the protected ATC/DDD package were not available for this run: their artifact tests remain explicit opt-ins. No protected download conditions were accepted automatically. Database deployment, new medication FHIR profile conformance and model accuracy are not established by these tests.

For your installed real tables, set `SQC_TEST_LOINC_CSV`, `SQC_TEST_LOINC_VERSION` and/or `SQC_TEST_ATC_DIR` in an ignored test environment file, then run `uv run --no-sync --env-file .env.tests python -m unittest discover -s tests -v`. The two artifact tests read the actual publisher data. No synthetic lookup tables or service replies are introduced.

### Earlier verification

0.12.0 adds executable medication terminology/dose checks to the four laboratory modes. Both medication recipes ran against actual NLM UCUM and FHIR terminology services on 13 October 2026: ATC N02BE01 returned paracetamol; 5 mL at a source-declared 20 mg/mL yielded a conditional 100 mg; the contradictory constant-infusion example was flagged. The final installed-package suite ran **75 tests: 52 passed, 23 integrations skipped** without service opt-ins. All **14 medication tests passed** with the real-service opt-in. The public server reported ATC edition **2025**, independently of the selected MII guide version. No medication FHIR conformance or clinical-dose safety result is claimed from these terminology calls.

The suite covers source contracts, all dosage instructions, negative/string quantities, strength denominators, timing bounds, liquid/tablet mismatch, unknown counts, distinct context, CSV/SQLite/resource-CSV equivalence, local reference resolution, output protection and missing-validator reporting. Generic FHIR terminology calls reject redirects (including credential-forwarding redirects), limit response size, and retain the returned CodeSystem edition when supplied. No synthetic lookup replies are used. Live medication checks: `SQC_LIVE_MEDICATION_TESTS=1 uv run --no-sync python -m unittest discover -s tests -p test_medication.py -v` (an optional test flag, not runtime configuration).


The previous live evidence run is retained below as a versioned result; changing the model or source content can change proposals.

Verified on 13 October 2026 for 0.11.2: **49 tests; 46 passed, 3 explicit skips**. Actual LOINC/NLM, Europe PMC/PubMed, existing BfArM files and native Ollama requests passed. The skipped integrations were a running Matchbox instance, a new protected BfArM download and credentialed WHO access. The prior live Matchbox/MII-guide test returned 0 errors for the complete example Bundle and 2 for its missing-identifier variant; it was not restarted for this CSV run.

Native model: Ollama 0.40.2, `ministral-3:3b`, Q4_K_M; observed model SHA-256 `316262d960e27504463e6270bd8c1e8665c957ef2cff620ba333af9e1480df63`. The tag's served metadata reports 3.8B parameters. This run used Python 3.14.8 and DuckDB 1.5.6. The wheel, public manual site and runner archive built successfully; private configuration, inputs and reports were excluded.

The live research test retrieves the actual caregiver/thiamine article (PMID 42473864). Its reported hemoglobin measurement can be extracted, while the thiamine interval cannot become a hemoglobin reference recommendation. Quotes are chosen from normalised source sentences; rejected content does not disable later model tasks. These source/schema guards constrain extraction, while clinicians still assess applicability and semantic correctness.

The starter's eight rows group into seven sets representing 84 occurrences. Six sets have verified LOINC codes; all seven unit expressions pass NLM UCUM syntax, including `10*6`, whose compatibility with a cell concentration is unresolved. Three sets meet the documented numeric readiness conditions (75/84 weighted occurrences). A value outside an exported range is flagged without classifying the result as semantically wrong. CSV aggregates receive no full-resource FHIR validation.

The complete Europe PMC run retrieved 11 distinct articles, accepted 16 model responses through the source guards and rejected 13. The actual proposals were:

| Source set | Result and source |
| --- | --- |
| `LIS-HB`, Hemoglobin, blood, `g/dL` | Propose [718-7](https://loinc.org/718-7), component Hemoglobin, canonical name “Hemoglobin [Mass/volume] in Blood”; clinician review remains required. |
| Hemoglobin `140 g/L`, illustrative bounds `120–160 g/L` | Propose `14 g/dL`, bounds `12–16 g/dL`, using the NLM-verified factor `0.1`. |
| Leukocytes `6690-2`, `10*6` | [LOINC's example](https://loinc.org/6690-2) supplies `10*3/uL` as a review candidate; no executable conversion factor is supplied. |
| Study evidence | Extracted a reported hemoglobin value of `5.7 g/dL` from [PMID 42473864](https://europepmc.org/article/MED/42473864). No supported reference interval was extracted in this bounded run. The case value does not define an interval. |

This run selected `sources=["europe_pmc"]`, `MAX_RESULTS=3`, `max_articles_per_loinc=6` and the documented optional human-population query filter. Changing provider results, model weights or budgets may change candidates. The report's trace retains the actual inputs and guarded outputs; the original checked report remained unchanged.
