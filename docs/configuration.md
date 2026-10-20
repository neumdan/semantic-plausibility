# Laboratory configuration reference

## One pipeline.toml

Start with the [setup in pipeline.toml](../README.md#1-first-setup). This page explains the available fields; the [input manual](../examples/lab/README.md) supplies source-specific blocks for the same file. The operator workflow focuses on laboratory Observations.

| Category | Select | Settings / command |
| --- | --- | --- |
| **A. Plausibility level** | lookup; ai-lookup; ai-evidence | `check`; optionally `research-evidence --scope lookup` or `--scope studies` on the original checked report |
| **B. Input** | CSV, PostgreSQL/SQLite, native FHIR or server export | `[input]`, `[input.columns]`, `[database]`, `[output]` |
| **C. Lookup / terminology sources** | Downloaded catalogues, enabled APIs, or a combination | `[lookups]`, `[terminology]`, `[network]`; credential references only |
| **D. Laboratory target** | Quantitative Observation with the pinned MII Laboratory guide | `general.RESOURCE_TYPE`, `[specification]`, optional `[validation]` |

FHIR validation is independent: `[validation]` applies to complete resources, not scalar aggregates. ai-lookup/ai-evidence currently support laboratory Observations only. Model settings belong in `[models]`; study APIs/budgets in `[research]`. **For laboratory, use one TOML for all workflows**, and keep `.env`/secret files for credentials. No shell exports are needed.

Map actual source facts: a local LIS test identifier stays local until reviewed. Profiles supply target rules, not a guess about source column meanings. Reports are CSV/HTML; automatic FHIR/OMOP/database output writers are not implemented. All relative paths resolve from the selected TOML. [Laboratory configuration overview](index.html#setup) · [Input walkthroughs](index.html#inputs) · [Download/API walkthroughs](index.html#terminology).

## Select the configuration

| Runtime | Select the TOML | Default |
| --- | --- | --- |
| Python / uv | `run-plausibility-check check --config pipeline.toml`; uv loads credentials with `--env-file .env` | `pipeline.toml` in the current directory |
| Docker Compose | `.env` contains `SQC_CONFIG_FILE=pipeline.toml`; run `docker compose run --rm run-plausibility-check` | Host `pipeline.toml`, mounted at `/app/pipeline.toml` |
| Podman Compose | Same `.env`; run `podman-compose run --rm run-plausibility-check` | Same host/container paths |

Keep the default `pipeline.toml` selector for CSV, FHIR, database and research runs. `SQC_CONFIG_FILE` remains available for deployments that already select a private root file. The setup copy commands preserve an existing `.env` and `pipeline.toml`.

## Fill in your own export

Edit the existing sections of **`pipeline.toml`**, using the comments in [the template](../pipeline.example.toml). TOML tables appear once; replace a source block when switching input types. Keep this file at the repository root for the supplied Compose stack.

1. **Place your input.** Create `local/input/` (or use `local/data/`) and copy your export there. Both are already inside the Compose `local/` mount. Set `input.path="local/input/laboratory.csv"` to its actual filename.
2. **Read its header.** Set `input.delimiter` to the character between column names: `","`, `";"`, or `"\t"` for tabs. `delimiter` is the column separator. Numeric cells use a decimal point, e.g. `13.5`.
3. **Map its columns.** The left-hand name is a pipeline role; the right-hand value is your exact header. For example, `analyte_name="test_name"` reads `test_name`. Remove mappings for absent columns. Keep missing systems/context as review points; add `input.defaults` only for documented constants from the source.
4. **Describe the rows.** Use `granularity="individual"` and `value="result"` for single measurements. For supplied summaries use `granularity="distinct"`, remove `value`, and map `value_mean="mean"`, `value_sd="sd"`, `value_min="min"`, `value_max="max"`. Map `count` only when it counts the measurements represented. Statistics are numbers sharing the row's unit; reference bounds have their own fields. Use `output.mode="distinct"` for shared evidence research.
5. **Select the target and sources.** Keep the pinned MII Laboratory profile initially. Choose API, direct files, or file + API in the [source recipes](lookups.md#3-api-files-and-fallback-in-one-toml). Fill credentials in `.env` or secret files; download real terminology manually to the configured paths.
6. **Install the target definitions and check setup.** Run the commands below. `profile-init` acquires the selected profile/bindings into `local/profiles/`; terminology downloads have their own source paths.

```sh
podman-compose build run-plausibility-check
podman-compose --profile setup run --rm profile-init
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check check --out output/my-run/lookup.csv
```

Open **`output/my-run/lookup.html`**. Choose a new output folder for the next run and keep its CSV, summary, evidence and audit files together. The same TOML configures all workflows:

| Workflow | What to select | Command after setup |
| --- | --- | --- |
| `lookup` | Your mapped input, sources and profile | `check --out output/my-run/lookup.csv` |
| `fhir-validation` | Complete FHIR input, `validation.enabled=true`, [prepared validator](validation.md) | `check --out output/my-run/fhir-validation.csv` |
| `ai-lookup` | [Start/provision Ollama and the unit catalogue](../README.md#local-ai-select-download-run) | `research-evidence --scope lookup --report output/my-run/lookup.csv --out output/my-run/ai-lookup.csv` |
| `ai-evidence` | Same model, plus selected study APIs in `[research]` | `research-evidence --scope studies --report output/my-run/lookup.csv --out output/my-run/ai-evidence.csv` |

These are workflow names; the command selects the workflow. Both research commands read the audited, grouped `lookup.csv` and add sourced proposals in separate outputs. For Podman research commands, use the prefix `podman-compose --profile research run --rm research-evidence` followed by the full command in the table. Docker uses `docker compose` instead of `podman-compose`. Python uses `uv run --no-sync --env-file .env run-plausibility-check` followed by the command; keep native model/validator endpoints running when selected.

For FHIR files and DB tables, use the complete replacement [input recipes](../examples/lab/README.md). `origin` describes the source system; `type` describes how the runner reads it. A CSV exported from a LIS therefore uses `type="csv"`, `origin="lis"`.

## Configuration sections

These sections cover the supported workflow. Unknown settings are rejected. Use actual source facts; a configuration must not invent LOINC/UCUM declarations to make a check pass.

| Section | Main settings | Purpose |
| --- | --- | --- |
| Root | `config_version=3` | Current configuration schema, separate from the software version |
| `catalog` | `path="catalog.csv"` | Editable directory of source metadata; no concept content |
| `general` | `PROJECT_NAME`, `RESOURCE_TYPE="Observation"`, `MODEL_ALIAS` | Run label, supported resource type, selected named model (default alias `local`) |
| `input` | `type`, `origin`, `source_namespace`, `path`, `granularity`, `delimiter` | Location, source/site identity and input representation |
| `input.columns` / `input.defaults` | Semantic role → column / known constant | CSV/database field mapping; defaults only for documented source facts |
| `input.elements` | Semantic role → `Observation.*` path | Optional scalar FHIR selection; the native adapter normally needs no mapping |
| `input.context` / `context_file` / `manifest` | Context and export-selection artifacts | Known axes/qualifiers and source-selection accounting |
| `input.snapshot` | Captured real lookup-pack path | UCUM/LOINC evidence for offline checks |
| `database` | `DB_TABLE`, optional `DB_SCHEMA`, connection references | Read-only PostgreSQL or SQLite selection |
| `specification` | `FHIR_VERSION`, `PROFILE`, `PROFILE_VERSION`, `STRUCTURE_DEFINITION` or `PROFILE_FILE`, `VALUE_SETS`, `BINDING_URLS`, `BINDING_VERSIONS`, `PACKAGE_URL`, `IG_*`, `links` | Selected target rules and pinned artifacts |
| `validation` | `enabled`, `backend`, `endpoint`, `container_endpoint`, `terminology_server`, `setup_directory`, `additional_packages`, `timeout_seconds`, `fail_on_error` | Local full-resource/guide validation before grouping FHIR input |
| `aggregation` | `engine="duckdb"`, `group_by` | Grouped sets; optional exact point-value grouping |
| `output` | `path`, `mode`, `overwrite`, `include_distribution`, `lookup_pack` | Scalar CSV/HTML prefix, item/group mode and optional captured evidence |
| `network` | `mode`, `allowed_http_hosts` | Offline/connected lookups and explicit local plain-HTTP hosts |
| `lookups.NAME` | `enabled`, `role`, `source` (catalogue ID), optional `type`, `endpoint`/`container_endpoint` or `path`, `systems` | Real primary/source/optional terminology services |
| `packages.NAME` | Registry, name, exact version, path, acceptance | Exact pins for optional release metadata checks; laboratory downloads are manual |
| `decision` | `enabled`, local URLs, pinned model/revision, score threshold | Optional Laya input hints during a laboratory check |
| `models.NAME` | Runtime, native URL reference, `container_base_url`, model ID, budgets | Optional local inference endpoint |
| `research` | Scope, providers, per-LOINC budget, catalogue | Separate page/study evidence retrieval |
| `quality` | `review_registry`, optional `concept_map` | Reviewed local mappings; clinician registry is the feedback route |
| `study` | `cohort`, `period_start`, `period_end`, `source_label` | Operator declarations for matched LIS/FHIR/CDS Hub comparisons |

## Paths and container mounts

For Python, relative input, output, artifact and secret-file paths resolve against the selected TOML's directory. `--config` itself resolves from your current directory. Keep a configuration beside the paths it describes, or use appropriate absolute paths.

For the supplied Compose stack, edit `pipeline.toml` in the repository root. `SQC_CONFIG_FILE` is a repository-relative host filename; the file always appears at `/app/pipeline.toml`. Its relative paths therefore resolve under `/app`.

| TOML path | Host location | Container location / access |
| --- | --- | --- |
| `examples/laboratory.csv` | Repository example | `/app/examples/laboratory.csv`, read-only |
| `../input/laboratory.csv` | Sibling private input directory | `/input/laboratory.csv`, read-only |
| `../input/terminologies/…` | Sibling real downloads | `/input/terminologies/…`, read-only |
| `local/input/laboratory.csv` or `local/data/laboratory.csv` | Your input export | Same path under `/app/local/`, read-only for checking |
| `local/profiles/…` | Downloaded profile/bindings | `/app/local/profiles/…`, read-only for checking |
| `local/terminologies/…` | Installed real terminology | `/app/local/terminologies/…`, read-only for checking |
| `local/secrets/…` | Secret files | `/app/local/secrets/…`, read-only for checking |
| `output/laboratory-review.csv` | Reports | `/app/output/laboratory-review.csv`, writable |

A host path such as `/Users/…/lab.csv` is not automatically available in a container. Use sibling `../input/` (the default Compose input mount) or ignored `local/`. Set `SQC_INPUT_DIR` in `.env` only to choose a different host input directory; it still appears at `/input` in the container. For Python, adjust the TOML path to the actual host location when using a custom input directory. Package/catalogue/review-import services have writable `local/` mounts for their setup actions.

## CSV and aggregation

Follow the [individual/aggregate CSV manual](../examples/lab/README.md). `input.columns` maps pipeline roles on the left to source headers on the right. Use `input.granularity="individual"` for one measurement per row, or `"distinct"` for supplied frequencies/statistics. `output.mode="distinct"` is the evidence workflow default.

Grouping retains codes/editions, name, unit/system, reference qualifiers, comparator, specimen, method and status. Individual values produce count, mean, sample SD/SE, observed min/max, median and quartiles. Summary pooling uses disjoint groups, counts and sample SD; retain that assumption in your source-export documentation. Use numeric statistics and one common `unit_code` per row. Omit unavailable count/statistic roles.

For separate lookup inspection, `output.mode="items"` retains source IDs. Adding `"value"` to the default `aggregation.group_by` creates exact code–value sets. Evidence research reads an audited context-grouped report; run a fresh `check` with `mode="distinct"` and the default grouping for that stage.

Map one reference interval to scalar columns; preserve multiple qualified intervals through native FHIR or a DB `reference_ranges_json` column. The `specimen` scalar role is a resolved LOINC system-axis token. [Complete field roles](data-contract.md#minimum-export).

## Complete FHIR resources in CSV

For a CSV-to-CSV run that includes formal validation, use the complete source JSON rather than reconstructing resources from scalar tuples. Replace the input tables with:

```toml
[input]
type = "csv"
origin = "fhir"
source_namespace = "site-fhir-a"
path = "../input/resources.csv"
granularity = "individual"

[input.columns]
resource_json = "resource_json"
```

Each cell contains one complete R4 resource, Bundle or resource array, quoted according to CSV rules. Map **only** `resource_json`; remove scalar mappings/defaults and counts. Native fields supply IDs, codings, quantities and all reference ranges. Additional unmapped columns are ignored. Set `validation.enabled=true` for the same local validation used by native FHIR files. Bundle references stay together; independent resources/array entries have independent validation scope. CSV cells are bounded to 32 MiB; use native files for large exports.

For the working example set `path="examples/laboratory-fhir.csv"` in the same `pipeline.toml`. The invalid variant, `examples/laboratory-fhir-invalid.csv`, removes the required MII identifier while preserving the numerical set. Run `check` with a fresh output filename after [preparing validation](validation.md).

## FHIR Bundle and NDJSON

The [FHIR input manual](../examples/lab/README.md#fhir) provides commands and native-field mappings. Set `input.type="fhir_file"`, `path`, `granularity="individual"` and `output.mode="distinct"`. Remove scalar `input.columns/defaults`. The adapter reads native R4 resources, components and reference intervals, then aggregates equivalent measurements.

Use TORCH/CRTDL or site-supported CQL to export selected resource versions and referenced Specimen resources. Optional `input.elements` selects supported scalar `Observation.*` paths; native components use the regular reader. To include formal validation, enable `validation`, select the pinned target guide and [prepare the local validator](validation.md).

## PostgreSQL and SQLite

Prefer a CSV export for disconnected hospital databases. Direct reads use your site's selected table/view and the same `input.columns` contract; the runner assumes no universal CDS Hub schema.

For PostgreSQL, install `uv sync --locked --no-editable --extra postgres` (the Compose image includes the adapter), remove `input.path`, retain your column mapping, and configure:

```toml
[input]
type = "postgres"
origin = "cds_hub"                  # Or lis.
source_namespace = "site-cds-hub-a"
granularity = "distinct"           # Use individual for a view containing single measurements.

[database]
DB_SCHEMA = "semantic_review"
DB_TABLE = "lab"
DB_DSN_ENV = "LAB_DB_DSN"
```

Put the real DSN in ignored `.env`; load it with uv's `--env-file .env` or Compose. A template is `LAB_DB_DSN=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=verify-full`. Configure a trusted CA and SELECT-only account. `DB_DSN_FILE="local/secrets/lab-dsn"` is the secret-file alternative. Identifiers are validated and PostgreSQL scanning uses a read-only transaction.

Other supported connection fields are `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER_ENV`, `DB_PASSWORD_ENV` or `DB_PASSWORD_FILE`, and `DB_SSLMODE`. Choose a DSN or these separate fields. `PATH_TO_DB_CONFIG_TOML` alternatively reuses flat CDS Toolchain `DB_*` settings; `DB_USER_KEY`/`DB_PASSWORD_KEY` select its credential keys. Keep that existing credential-bearing file under ignored `local/`. [Source grouping SQL](../examples/studies/distinct-export.postgres.sql).

For a PostgreSQL JSON/JSONB Observation column, map `resource_json="resource"` and use individual granularity; actual resource type must be Observation. Do not mix this with scalar value/code/unit overrides. Direct database JSON receives semantic checks in this release. For full-resource validation, export those resources as native FHIR files or the resource CSV above. Choose a curated current-version Observation view upstream; the runner never guesses the CDS Hub schema.

For SQLite, use `input.type="sqlite"`, `input.path="local/lab.sqlite"`, the column mapping and `database.DB_TABLE="lab"`; omit PostgreSQL connection fields. It opens read-only. DuckDB is the aggregation engine for every input type, not an additional database server.

## Laboratory profile and terminology roles

Keep `general.RESOURCE_TYPE="Observation"`. The starter pins FHIR R4 and MII Laboratory 2026.0.3 in `pipeline.toml`. `STRUCTURE_DEFINITION` is an installed resolved R4 Observation snapshot; `IG_PACKAGE`, `IG_VERSION` and `IG_CANONICAL` record its identity. Alternatively select a reviewed `PROFILE_FILE` policy. `VALUE_SETS` lists the real versioned definitions used to derive bound systems. The operator workflow is laboratory-focused; medication onboarding and AI/evidence are planned for version 2. [Existing technical adapters](../examples/medication/README.md) retain their regression examples.

Profiles define constraints/bindings, not API endpoint URLs or credentials. The MII snapshot fixes the quantitative unit system to UCUM and gives laboratory codes a **preferred** LOINC binding. ValueSet routing does not expand filters or prove membership. Unspecified selected rules fall back to international R4; explicit prohibitions remain effective. Set `validation.enabled=true` to run the selected local Matchbox/HL7 or MII service before grouping actual FHIR input. The complete guide and dependencies are pinned independently of the selected-element importer. [Validation configuration and examples](validation.md).

| Lookup role | Use | Example |
| --- | --- | --- |
| `primary` | Verify target terminology identity/unit grammar | Dedicated NLM UCUM and LOINC FHIR services |
| `source` | Check what the LIS's original local codes/units mean | Real site CSV, SQLite, FHIR artifacts or API |
| `optional` | Additional labels, aliases and candidates | BfArM supplements, SemLookP / OLS or a configured website |

For **code membership**, `fallback` orders local sources before APIs, with primary/source/optional roles within each group and configuration order for ties. It tries enabled sources covering the canonical system until one returns valid; `all` queries every compatible membership source. Conflicting valid/invalid results stay `unknown` and require review. A partial catalogue absence stays unknown. Install enabled files and configure credentials before running: fallback handles lookup results/service failures after setup. The dedicated UCUM syntax/conversion check uses its one selected service; binding checks and AI candidate searches have separate evidence records. [Concrete API/file/fallback recipes](lookups.md#3-api-files-and-fallback-in-one-toml).

```toml
[lookups.lis_catalogue]
enabled = true
role = "source"
type = "csv"
path = "local/terminologies/lis-catalogue.csv"
systems = ["https://your-site.example/lis/tests", "https://your-site.example/lis/units"]
complete = false
```

This references your actual file; the project does not generate local terminology. CSV catalogues require `system,code`, optionally `version,display,definition,expected_unit_code,expected_unit_system,specimen,method,ddd`. Set `complete=true` only for a complete edition.

Supported types: `ucum`, `loinc`, `loinc_csv`, generic `fhir`, `csv`, `sqlite`, `fhir_artifacts`, `website`, `ols`, `who_icd`. Set `enabled=true` deliberately, one dedicated UCUM and LOINC service each. API adapters use `endpoint`; dedicated UCUM/LOINC also accept `endpoint_env`. Local adapters use `path` and declared `systems`. WHO ICD-11 is a standalone lookup, not a Condition batch check. [Adapter-specific fields, access and setup](lookups.md#5-own-servers-and-extending-the-catalogue).

## Full FHIR validation

`validator-init` derives Matchbox/MII server settings from the selected profile/package, then the container build installs the guide dependencies. Native Python uses `validation.endpoint`; the container uses `container_endpoint`. Full resources are only sent to a local/LAN service; redirects and proxy forwarding are disabled. `validation.token_env`/`token_file` are optional references for a protected validator. `additional_packages` accepts pinned `package#version` entries. Scalar CSV/database tuples are not full FHIR resources. Native FHIR files and complete `resource_json` CSV cells support full validation; direct database JSON currently receives semantic checks only. [Exact setup and OperationOutcome outputs](validation.md).

## Network and credentials

The starter uses `network.mode="lookups"` for enabled real sources. For `"offline"`, install real local artifacts and reference actual captured UCUM/LOINC evidence in `input.snapshot`; absent evidence stays unverified. Capture a connected run with `output.lookup_pack="output/reference-pack.json"`. Research cannot retrieve remote evidence in offline mode.

`allowed_http_hosts` permits explicitly named local/plain-HTTP endpoints, including Compose `ollama`, Docker Desktop `host.docker.internal` and Podman `host.containers.internal`. It is not an outbound firewall. Public API URLs in the examples use HTTPS.

| Service | TOML references for credentials |
| --- | --- |
| Official LOINC Basic Auth | `username_env` / `username_file`, `password_env` / `password_file` |
| Generic FHIR / website bearer token | `token_env` / `token_file` |
| Generic FHIR mTLS (including MII SU-TermServ) | Both `client_cert` and `client_key`: PEM file paths relative to TOML, under ignored `local/secrets/` |
| WHO ICD OAuth | `client_id_env` / `client_id_file`, `client_secret_env` / `client_secret_file` |
| PostgreSQL | `DB_DSN_ENV` / `DB_DSN_FILE`, or user/password references above |
| Local model requiring a key | `api_key_env` / `api_key_file` |
| PubMed optional key | `research.pubmed.API_KEY_ENV` / `API_KEY_FILE` |
| Protected package setup | `packages.NAME.token_env` / `token_file`, with explicit conditions acceptance |

Client certificate/key paths are passed to TLS without copying key contents into reports or logs. `config-check` loads the pair locally; `--connections` tests server access with it. [MII certificate setup](lookups.md#5-own-servers-and-extending-the-catalogue).

Environment references name a variable whose value lives in `.env` or the runtime. File references point to a secret file containing only the value; mount it under `local/secrets/` for Compose. Plain Python can use file references without loading `.env`. The default user TOMLs and files under `local/`/`output/` are excluded from Git, as are `.env` credentials. Keep other site configurations under ignored `local/` or add them to your ignore rules.

## Local models and research

`check` uses deterministic terminology/consistency checks. With `decision.enabled=true`, it also records separate Laya input hints. Research/extraction remain explicit ai-lookup/ai-evidence commands. `research-evidence --scope lookup` selects terminology AI without study queries; `--scope studies` adds the configured literature providers. A scope argument overrides TOML for that invocation and is retained in the report/trace. Use the separate `research-evidence` command on the completed audited report. The legacy `plausibility batch` command still honours `research.enabled`.

```toml
[models.local]
runtime = "ollama"                 # external for LM Studio / another local compatible server.
base_url = "http://127.0.0.1:11434/v1"
container_base_url = "http://ollama:11434/v1"
model = "ministral-3:3b"
temperature = 0
max_tokens = 2048
timeout_seconds = 180
response_mode = "json_schema"
```

The starter defines the native address directly in TOML. For an environment reference, replace `base_url` with `base_url_env="LOCAL_MODEL_BASE_URL"` and set that variable in `.env`; choose exactly one. Inside Compose, `container_base_url` overrides the native address automatically. The endpoint must remain local. Choose the actual served model ID; configuring it does not start or download the model. `general.MODEL_ALIAS` selects a named table.

`response_mode` supports `json_schema` or `json_object` with the same application guards. Optional controls include `reasoning_effort`, `enable_thinking`, `expected_digest` and `pull_timeout_seconds`; use only controls supported by the selected server/model. [Provisioning and native URLs](shipped-stack.md#compose-ollama).

### Optional Laya decisions

**Experimental, disabled by default.** Initial tests with our laboratory examples did not establish reliable domain classification. Use this service to inspect model suggestions and collect evaluation data. The configured score threshold is an abstention gate, not a medical accuracy estimate.

Use the existing `[decision]` block in `pipeline.toml`:

```toml
[decision]
enabled = true
base_url = "http://127.0.0.1:8085"
container_base_url = "http://laya:8000"
model = "convaiinnovations/laya-multilingual"
revision = "1720e3e3357cfe1e281542e223f8273b0890ca34"
min_probability = 0.8
```

The optional [Laya runtime](https://github.com/NandhaKishorM/laya) is pinned to **0.4.10**; the [multilingual checkpoint](https://huggingface.co/convaiinnovations/laya-multilingual/tree/1720e3e3357cfe1e281542e223f8273b0890ca34) uses **Apache-2.0**. Its model licence and exact revision are recorded with every response. Laya runs in its own CPU service; Ministral stays in Ollama for terminology proposals and study extraction.

| Question | Input used | Review outcome |
| --- | --- | --- |
| Clinical domain | Name, code, specimen, native resource type when present | Laboratory / medication / other / unclear |
| Unit system | Explicit source unit-system URI | UCUM / other / unclear; actual validity comes from the terminology check |
| Next review step | Semantic metadata and existing check findings | AI terminology lookup / study evidence / human clarification |

Names and bounded semantic metadata stay local. IDs, measured values, reference numbers, paths and credentials are omitted from the Laya request. Technical input type and column mapping come from configuration; native FHIR `resourceType` comes from the resource. **Declare LIS/CPOE provenance with `input.origin`**. The model scores are uncalibrated for this workflow. Scores below `min_probability`, unclear answers and truncated contexts produce abstentions. An unresolved laboratory domain also suppresses its next-step suggestion. Conflicts and service failures remain visible; the model never changes the source, check verdict, profile or research scope.

**Podman / Docker:** start the `laya` service with profile `decision` as shown in the README. First startup downloads the pinned files from Hugging Face into ignored `local/laya/`; later starts reuse them. The image contains the optional CPU runtime; the regular runner retains its small dependency set. `models.local` is unchanged.

**Native Python**, in one terminal:

```sh
uv sync --locked --extra decision --extra postgres
uv run --no-sync --env-file .env run-plausibility-check decision-serve
```

In another terminal, run the ordinary `check --out output/laya-laboratory.csv` command. The service uses CPU by default; native macOS can select `device="mps"` when supported. Optional settings are `threads=2`, `timeout_seconds=120` and `cache_dir="local/laya"`. Container images use CPU wheels. Changing a cache path also requires changing the matching Compose mount. Local/LAN endpoints only; use `api_key_env="LAYA_SERVICE_KEY"` with its value in `.env`, or `api_key_file="local/secrets/laya-key"`, when protecting the service.

The HTML adds one **Input decision** row per laboratory set. CSV uses scalar `decision.*` columns; audited `*.evidence.jsonl` retains the full bounded request, response, model revision, timing and response hash. ai-lookup/ai-evidence preserve this record from the checked report. Set `enabled=false` to use the original model-free check.

### Use your own model or service

Keep the input/profile/output settings. Change the relevant tables:

| Choice | Configuration | Setup |
| --- | --- | --- |
| Another model in Compose Ollama | Change `models.local.model` to its actual Ollama ID | Start Ollama, then run `model-init`; the selected weights use the persistent volume |
| Native Ollama | Native `base_url`; Podman `container_base_url="http://host.containers.internal:11434/v1"` | Start your native Ollama server and model |
| LM Studio, llama.cpp or vLLM | `runtime="external"`, actual served model ID and OpenAI-compatible `/v1` URL | Start your server; it must support chat completions and the selected JSON response mode |
| Your terminology server | `lookups.NAME.endpoint`, supported `systems`, optional `token_env`/`token_file` | The server must implement the selected adapter's operations |
| Literature API proxy | `research.europe_pmc.BASE_URL` or `research.pubmed.BASE_URL` | Compatible API; arbitrary search engines require an adapter |

Example: **replace** `[models.local]` for LM Studio, using the model ID shown by your server:

```toml
[models.local]
runtime = "external"
base_url = "http://127.0.0.1:1234/v1"
container_base_url = "http://host.containers.internal:1234/v1"
model = "your-served-model-id"
temperature = 0
max_tokens = 2048
timeout_seconds = 600
response_mode = "json_schema"
# api_key_env = "LOCAL_MODEL_API_KEY"
```

Docker Desktop uses `host.docker.internal`. Keep the HTTP hostname in `network.allowed_http_hosts`. For a LAN server use its private IP and add it to that allowlist. Models must remain local/LAN; public cloud endpoints are rejected. The API dialect does not select a vendor.

If authentication is needed, uncomment `api_key_env` and put `LOCAL_MODEL_API_KEY=...` in the ignored `.env`, or use `api_key_file` with a mounted secret. Secrets never belong in URLs/TOML. Multiple models use named `[models.NAME]` tables; set `MODEL_ALIAS="NAME"` in the existing `[general]` table. One alias serves the whole run.

Run against an audited lookup report, with a fresh output:

```sh
# Native Python/uv
uv run --no-sync --env-file .env research-evidence --config pipeline.toml --scope studies --report output/lookup.csv --out output/my-model-research.csv

# Podman (Docker: replace podman-compose with docker compose)
podman-compose --profile research run --rm research-evidence research-evidence --config /app/pipeline.toml --scope studies --report /app/output/lookup.csv --out /app/output/my-model-research.csv
```

These are alternative commands. Add `--plan` to inspect queries/prompts. External runtimes need no Compose Ollama service or `model-init` download. All models face the same quotation, number, analyte and unit guards; a model change is not a clinical validation.

```toml
[research]
enabled = false # research-evidence activates research explicitly
scope = "studies"                 # lookup uses pages/catalogues without study databases.
sources = ["pubmed", "europe_pmc"]
max_articles_per_loinc = 4         # 1–12 selected articles sent to the model per exact code/edition.
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.pubmed]
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAX_RESULTS = 3                    # Candidates per query, screened before model calls.
# EMAIL = "study-contact@example.org"
# API_KEY_ENV = "PUBMED_API_KEY"

[research.europe_pmc]
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESULTS = 6
# QUERY_FILTER = '(human OR humans OR patients OR volunteers) NOT (feline OR cats OR canine OR dogs OR rats OR mice OR veterinary)'
```

Prepare the real catalogue with `catalog-init` and the model with `model-init` or native setup. Provider limits bound query results; the selected-article cap is separate and retrieval is cached per code/edition. `--plan` exposes tasks/schemas/searches; executed `.trace.jsonl` records actual prompts, queries and guarded responses. Public evidence supports clinician adjudication, not an automatic semantic-correctness judgment. [Research and feedback](research.md).

## Output and clinician feedback

Edit the existing output table:

```toml
[output]
mode = "distinct"                 # items requires individual input.
path = "output/laboratory-review.csv"
overwrite = false
include_distribution = true
# lookup_pack = "output/reference-pack.json"
```

`run-plausibility-check check --out output/another-check.csv` selects a fresh prefix without editing TOML. Relative `--out` and `--report` paths resolve against the selected TOML. The effective output path and research scope are audited separately from the unchanged configuration hash.

Only CSV is the batch data output format; HTML, summary, evidence and audit sidecars are always produced. Pick a fresh path to preserve prior reports; research needs a fresh output prefix too. [Artifact contents](data-contract.md#output).

For reviewed mappings, set a stable `input.source_namespace` before collecting feedback. After `review-import` creates the local registry, enable:

```toml
[quality]
review_registry = "local/clinician-mappings.json"
```

Reuse requires matching source/profile/context and revalidated targets/factors; history and revocations remain visible. Study selection metadata records cohort/period declarations, not proof that exports are comparable. [Clinician workflow](research.md#clinician-feedback) · [Source comparison studies](studies.md).

## Validate settings

```sh
uv run --no-sync --env-file .env run-plausibility-check config-check --config pipeline.toml
```

Add `--connections` for explicit probes. Without it, the report checks setup and local files/columns; neither mode is a clinical audit. Unknown keys, mismatched source fields and unsupported resource/FHIR versions fail explicitly. A missing/unavailable terminology response remains unverified during a check.

## Profile and resource links

The same TOML pins the validation package and local snapshot. Add publication/GitHub links for the report:

```toml
[specification.links]
resource = "https://hl7.org/fhir/R4/observation.html"
guide = "https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Laborbefund_2026/MIIIGModulLaborbefund.html"
source = "https://github.com/medizininformatik-initiative/kerndatensatzmodul-labor"
```

Links are for readers; they neither download nor substitute validation artifacts. Install a real R4 StructureDefinition **snapshot**, supply `STRUCTURE_DEFINITION`, pin `IG_PACKAGE`/`IG_VERSION` and configure actual `VALUE_SETS`. Matchbox/HL7 (or MII) validates the individual FHIR document against the installed guide before grouping. Scalar CSV/DB aggregates receive semantic-set checks and cannot establish full resource conformance. Validate the actual generated FHIR after applying approved mappings. [Validator setup](validation.md).

A GitHub branch can change; the guide edition and source-file hashes establish what was actually checked. Use HTTP(S) documentation URLs without embedded credentials or query strings. Endpoint credentials stay in `.env` or `local/secrets/`, not these links.

`validation.setup_directory` defaults to `local/validation` beside `pipeline.toml`, matching the Compose build directory.

## Fallbacks and logging

```toml
[terminology]
strategy = "fallback"  # Local first; enabled APIs for the exact system if needed.
# strategy = "all"    # Cross-check every enabled compatible source; more requests.

[logging]
level = "INFO"        # DEBUG, INFO, WARNING, ERROR.
```

The catalogue never enables a service automatically. Missing code/system, uncovered systems, version mismatch, unavailability and source disagreement remain explicit. The laboratory evidence sidecar records lookup attempts, states, editions and available source hashes. The HTML includes these traces. A dedicated UCUM grammar service is configured separately; finite unit catalogues do not replace its grammar/conversion checks. Laboratory offline packs cover only queries actually captured.

`--log-level DEBUG` overrides TOML for one command and adds source-attempt states. INFO shows phases/counts; WARNING/ERROR reduce console output. No credentials or clinical values are printed by the progress logger. Reports/trace files do contain source-derived data and must remain private. Audit recording is independent of console verbosity.

`config-check` lists mappings, target rules, active sources, strategy and output. It verifies local catalogue readability and CSV/SQLite columns. `--connections` adds read-only PostgreSQL table/column checks and supported service readiness probes. A successful readiness probe does not promise that every code, edition or clinical assertion in your dataset can be verified.

## What the report records

`run_context` in the summary is a whitelist of effective settings: A–D choices, input column/element/table selection, defaults/context, profile, grouping, source aliases, credential mechanism (not its values), model/scope and output. URL credentials/query strings are omitted from this projection. It is not a copy of TOML or the process environment.

Each evidence record's `assessment_context` references the configuration hash and records input accounting, method results, assumptions, actual lookup evidence, validation scope and the next improvement step. Individual mode retains IDs; distinct mode does not. Defaults are disclosed as configured source assertions; the adapter does not track which individual fields originally used each default. Research reads the audited lookup report, records its original input/configuration, and keeps its verdict separate from new candidates. New model/retrieval events are counted per item; cached results may be reused without a new call. Existing reports without these fields are never retroactively assigned today's settings.

## Laboratory summary statistics

Map `value_mean`, `value_sd`, `value_min`, `value_max` to numeric columns and `unit_code` to their common measurement unit. Map `count` only when it is the actual number of contributing source observations. All these statistics use the declared measurement unit. Mixed supplied units, nonfinite numbers, negative SD and contradictory mean/bounds fail preflight. SD is sample SD, not SE. Matching disjoint groups can be pooled with sufficient counts/SD; missing information is not inferred. [Working CSV/DB examples](../examples/lab/README.md).

`[terminology] check_updates=true` performs metadata-only version checks for enabled `[packages.*]` entries. Pins and files are never changed; defaults leave the check off. `package-check` runs it separately.

## CSV report columns

Each cell holds one scalar value. Repeated source elements use numbered dotted headers, such as `input.reference_ranges.1.high.value`. Field findings and proposal colours use `assessment.*` columns. Complete structures remain in the matching `.evidence.jsonl`; keep the audited file set together for AI research and review import. [Column contract](data-contract.md#output).
