# Code responsibilities and verification

The library has one public check entry point: `run_plausibility_check(config_path, output=...)`. The CLI and direct script call it. Laboratory research consumes the audited result as a separate pass.

## Code map

| Element | Responsibility and invariant |
| --- | --- |
| `__init__`, `__main__`, `cli`, `_version`; root runner | Library/command entry points and version; delegate work to the same implementation |
| `batch` | Validate TOML, source roles, domain, profile, input/output modes and database settings |
| `deployment`, `deployment_checks`, `model_config` | Resolve paths and secret references, prepare adapters, probe configured connections and provision local model/catalogue |
| `batch_quality`, `db_source`, `elements` | Map CSV/DB scalar columns or native JSON; quote SQL identifiers, bind filters, use read-only transactions |
| `audit` | Read source records, preserve complete semantic metadata, assemble check evidence and real captured-response replay |
| `input_decision`; `deploy/laya.Dockerfile` | Optional pinned local Laya service/client; bounded metadata, strict response checks and abstention; no source/verdict mutation |
| `export_review` | Laboratory orchestration: preflight, validation, grouping, checks, provenance and atomic report publication |
| `export_grouping`, `aggregate_statistics` | Embedded DuckDB grouping; known counts, sample statistics, context guards and unit-consistent pooling |
| `profile_config`, `profile_import`, `profile_lookups` | Import selected StructureDefinition/ValueSet rules, R4 fallback, selected system/binding routes |
| `fhir_validation`, `matchbox.Dockerfile` | Local Matchbox/HL7 or MII resource validation; pinned guides, separate outcomes, local-only destinations |
| `observation_quality` | Typed terminology/evidence records and scoped approved mappings |
| `fhir_quality_pipeline`, `recommendations`, `name_checks` | Laboratory code–unit–specimen/method/name fit; numeric/reference checks; verified transformation plans |
| `local_terminology`, `terminology_router` | Real catalogue/file/SQLite/FHIR sources, local-first/all-source routing and edition-aware attempts |
| `terminology_validation`, `terminology_apis`, `terminology_lookup` | Real FHIR/OLS/WHO API adapters and one-source lookup commands |
| `source_catalog` | Resolve selected service IDs from the single metadata-only `catalog.csv`; explicit TOML overrides |
| `profile_setup` | Download pinned real profile/bindings at explicit setup; verify canonical/version/hash and reuse local files |
| `terminology_packages` | Explicit optional package acquisition and release metadata checks |
| `deploy/ucum/server.cjs` | Small NLM-compatible local HTTP service backed by pinned official `ucum-lhc`; exact syntax and supported conversion |
| `catalog_evidence`, `snapshot_services` | Supplied ObservationDefinition evidence and replay of recorded real service responses |
| `observation_services` | NLM UCUM, LOINC, bounded HTTP, local model client and public study retrieval |
| `laboratory_suggestions`, `unit_catalog` | Retrieve real code/name/unit candidates, select/verify proposals, retain source catalogue provenance |
| `study_sources`, `research_prompts`, `model_schemas` | Analyte-aware screening, visible prompts/schema, exact quotation/number/unit/type guards |
| `evidence_research`, `research_summary` | A2/A3 orchestration from verified A1 files, model/query trace and scalar study table |
| `medication_input`, `medication_checks` | Product/regimen normalization; all instructions; quantity, timing, strength and explicit constant-infusion consistency |
| `export_report`, `medication_report` | Domain summaries, HTML reports, audit events, output hashes and atomic publication |
| `report_sources`, `report_context` | Source → linked definition → proposal; effective settings, assumptions, methods and actual evidence |
| `laboratory_report_table`, `medication_report_table`, `report_comparison` | Shared field-table decisions, scoped status colours, citation links and HTML escaping |
| `laboratory_assessment` | Evidence-only conditional matches and five independent QC/completeness scopes, shared by CSV, HTML and structured evidence |
| `tabular_report` | One scalar per CSV cell; numbered repeated elements; temporary spool keeps row memory bounded |
| `review_feedback`, `study_compare` | Audit verification, clinician decisions and rechecked mapping reuse; matched-export congruence comparison |
| `support`, `progress` | Hashing, formula-safe text, context/config helpers, phase logs and timed model activity |
| `tools/run-podman-examples.sh`, `container-cache.sh` | Isolated example reports; reuse images/volumes and rebuild changed inputs only |
| `tools/prepare-lab-examples.py` | Create/reuse the exact supplied synthetic SQLite source |
| `tools/render_batch_diagrams.py` | Generate SVG/Mermaid and the matching README diagrams from one graph definition |
| `tools/build_docs_site.py`, `build_release.py` | Publish an allowlisted manual/runner; exclude private data, secrets and caches |

`catalog.csv` is the single source directory. Actual terminology concepts, profiles and binding resources are acquired into ignored runtime folders. The wheel includes catalogue metadata; the image/ZIP exclude downloaded content.

## Robustness checks

Input contracts reject ambiguous role aliases, competing scalar/JSON definitions, missing mapped columns, malformed CSV and non-finite database JSON. Min/max-only summaries receive the same finite-number/order/unit checks as mean/SD summaries. Native FHIR preserves all selected codings, intervals and instructions. Individual and grouped output retain distinct frequency semantics.

Reports are generated in temporary files, formula-safe/HTML-escaped, and published with a completion summary and artifact hashes. Complete objects live in the evidence sidecar; research verifies CSV and sidecars before reconstructing an internal row. Existing audited report layouts remain readable. Review import compares protected scalar fields before recording decisions.

Public terminology/research requests use bounded responses and explicit service adapters. Secret values are excluded from report configuration. Model endpoints and full-resource validators are restricted to local/LAN destinations. Lookup failures retain uncertainty; per-run caches/circuit breaking avoid repeated failing requests. Logging uses DEBUG/INFO/WARNING/ERROR and timed model progress. INFO names the source, granularity, target profile, enabled routes, aggregate counts and current group; source values and credentials stay out of these progress messages.

## Tests

```sh
uv sync --locked --no-editable --extra postgres --group dev
uv run --no-sync ruff check src tests tools run-plausibility-check.py
uv run --no-sync run-plausibility-check profile-init --config pipeline.example.toml
uv run --no-sync python -m unittest discover -s tests -v
```

| Tests | What they establish |
| --- | --- |
| `test_source_catalog` | Real catalogue configuration, file/API/server selection, explicit credential references, no bundled definitions, optional real local UCUM tests |
| `test_batch`, `test_aggregate_statistics`, `test_run_modes` | Input/grouping modes, values/counts/statistics, CSV/SQLite/FHIR representation and output protection |
| `test_single_configuration` | Executes the input blocks from the manual with the shared template; validates grouping/research hand-off and safe configuration logging |
| `test_input_workflow` | CSV/FHIR/DB scalar/DB JSON parity; individual statistics; grouping guards; audited research hand-off; private-data-safe progress; repeatable SQLite setup |
| `test_tabular_contract` | Scalar CSV round-trip, repeated bounds, zero/false, union columns, explicit examples, audit-protected research hand-off and ambiguous mapping rejection |
| `test_medication` | Product and every dosage instruction; scalar/DB/FHIR adapters; rate/duration and source-strength arithmetic when real services are selected |
| `test_configuration_workflow`, `test_domain_terminologies`, `test_fhir_validation` | Source/profile configuration, read-only mappings, credential handling and local validation contracts |
| `test_input_decision` | Disabled/default contract, source privacy, local endpoint settings and opt-in real Laya runs/recorded-response guards |
| `test_model_schemas`, `test_study_sources` | Exact source quotations, numeric/statistic roles, separate analytical limits, unit typography, analyte/stratum attribution, CSV/HTML decision parity and real recorded-trace regressions |
| `test_laboratory_report`, `test_report_comparison`, `test_report_context`, `test_review_feedback` | Field decisions/colours, sources, escaping, secret exclusion and clinician feedback integrity |
| `test_missing_metadata`, `test_assessment_layers` | Missing versus conflicting metadata; real LOINC/UCUM conditional matches, name/specimen/unit conflicts, numeric errors, source preservation and scope/CSV/HTML agreement |
| `test_podman_examples` | All example execution plans, separate paths, service lifecycle, content fingerprint and public packaging |
| `test_live_lookups`, `test_live_research`, `test_live_terminologies` | Actual configured terminology/model/provider responses and provenance |

Core tests use synthetic **input** and pure contracts. Live checks use real services; recorded-response tests use actual captured evidence. Credentials, real publisher files and live-service opt-ins are supplied through an ignored `.env.tests` file. Example entries:

```dotenv
SQC_LIVE_TESTS=1
SQC_LIVE_MEDICATION_TESTS=1
```

Run selected service tests with `uv run --no-sync --env-file .env.tests python -m unittest discover -s tests -v`. The live FHIR test uses `SQC_LIVE_FHIR_VALIDATION_TESTS=1` and a running pinned Matchbox; WHO/BfArM tests explain their credential/licence opt-ins in the skip messages. Review passed, failed and skipped counts separately.

For an end-to-end local container run:

```sh
bash tools/run-podman-examples.sh
bash tools/run-podman-examples.sh --lookup-only
```

To test the real optional Laya service, set `decision.enabled=true`, start its container, and add `SQC_LIVE_LAYA_CONFIG=pipeline.toml` to `.env.tests`. Run `uv run --no-sync --env-file .env.tests python -m unittest discover -s tests -p test_input_decision.py -v`. This calls real Laya/terminology services, verifies provenance and abstention, and keeps model quality separate from protocol success.

To recheck a completed study report, add `SQC_STUDY_REPORT=output/<run>/04-lab-ai-evidence/report.csv` to the ignored `.env.tests` file, then use the test command above. This verifies source guards, audit integrity and matching CSV/HTML field decisions.

[Expected example results](../examples/README.md). The scripts use fresh report folders; `summary.tsv` records command outcomes and reports carry semantic findings.

### Initial Laya evaluation

Six real Podman runs covered `examples/laboratory.csv`, individual/aggregate CSV, native FHIR, FHIR-in-CSV and SQLite: **16 grouped assessments**, with repeated representations. All model requests completed in the final sequential runs; audit verification and **160 field status pairs** agreed between CSV and HTML. Existing terminology/consistency verdicts were retained. The Laya domain question abstained on **16/16 groups** at the configured 0.8 threshold; no next-step proposal was accepted. This demonstrates a working integration and insufficient classification quality for automatic routing, not a clinical accuracy estimate. Keep the feature experimental and disabled by default.

The CPU service admits one request at a time. Run example batches sequentially; concurrent callers can receive a busy response, which is reported as unavailable while ordinary checks continue. Model weights and image tags are reused.

## Runtime

Python 3.14; DuckDB 1.5.6; optional psycopg 3.3.6. Optional decision service: Laya 0.4.10, PyTorch 2.14.1 (CPU wheels on Linux); transitive dependencies are pinned in `uv.lock`. `uv.lock` pins dependencies. The container uses Python 3.14.8 and uv 0.11.24. Matchbox 4.1.21 uses the pinned MII/FHIR packages; its generated inventory schema accommodates MII datatype canonical identifiers. The optional local UCUM service pins NLM `ucum-lhc` 8.0.0 and its npm lockfile, using Node 24.19.0. Every service uses the same `compose.yaml`. Exact service versions and source editions are recorded in each run.

## Repository boundaries

The public tree contains Python/HTTP service code, dependency locks, one operator TOML template, one metadata catalogue, docs and synthetic input tests/examples. Internal regression configurations live under `tests/configurations/`. All actual terminology, downloaded profile snapshots/ValueSets, private settings, reports, databases and model weights stay in ignored folders or named volumes.

The previous generated terminology inventories, duplicate source lists, bundled profile/ValueSet files and four Compose overlays were removed. Operators select services from one Compose file; they configure one TOML and extend one catalogue.
