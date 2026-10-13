# Configure a laboratory review

Start by copying `pipeline.example.toml` to **`pipeline.toml`**. For the first check, leave the example unchanged. For your data, edit its existing `[input]`, `[input.columns]` and `[output]` tables. Enable other features only when you need them. [First setup and run commands](shipped-stack.md#first-setup).

TOML carries the pipeline settings. `.env` or secret files carry credentials; no shell exports are required. The runner supports laboratory **Observation in FHIR R4 (4.0.1)**. Medication, Condition and Procedure remain later adapters.

## Select the configuration

| Runtime | Select the TOML | Default |
| --- | --- | --- |
| Python / uv | `run-plausibility-check --config pipeline.toml`; uv loads credentials with `--env-file .env` | `pipeline.toml` in the current directory |
| Docker Compose | `.env` contains `SQC_CONFIG_FILE=pipeline.toml`; run `docker compose run --rm --build run-plausibility-check` | Host `pipeline.toml`, mounted at `/app/pipeline.toml` |
| Podman Compose | Same `.env`; run `podman-compose run --rm --build run-plausibility-check` | Same host/container paths |

For another container TOML, change the selector in `.env` or use `--env-file local/research.env` with a selector file. [Exact examples](shipped-stack.md#select-another-toml). This Compose parameter chooses a host mount; it is different from the runner's `--config` parameter. The services continue to load project `.env` credentials.

An old shell assignment can override the file selector. Remove it once with `unset SQC_CONFIG_FILE`; `.env.example` and the Compose fallback now select `pipeline.toml`. Existing `.env`/TOML files are never replaced by the setup copy commands.

## Configuration sections

These sections cover the supported workflow. Unknown settings are rejected. Use actual source facts; a configuration must not invent LOINC/UCUM declarations to make a check pass.

| Section | Main settings | Purpose |
| --- | --- | --- |
| Root | `config_version=3` | Current configuration schema, separate from the software version |
| `general` | `PROJECT_NAME`, `RESOURCE_TYPE="Observation"`, `MODEL_ALIAS` | Run label, supported resource type, selected named model (default alias `local`) |
| `input` | `type`, `origin`, `source_namespace`, `path`, `granularity`, `delimiter` | Location, source/site identity and input representation |
| `input.columns` / `input.defaults` | Semantic role → column / known constant | CSV/database field mapping; defaults only for documented source facts |
| `input.elements` | Semantic role → `Observation.*` path | Optional scalar FHIR selection; the native adapter normally needs no mapping |
| `input.context` / `context_file` / `manifest` | Context and export-selection artifacts | Known axes/qualifiers and source-selection accounting |
| `input.snapshot` | Captured real lookup-pack path | UCUM/LOINC evidence for offline checks |
| `database` | `DB_TABLE`, optional `DB_SCHEMA`, connection references | Read-only PostgreSQL or SQLite selection |
| `specification` | `FHIR_VERSION`, `PROFILE`, `PROFILE_VERSION`, `STRUCTURE_DEFINITION` or `PROFILE_FILE`, `VALUE_SETS`, `IG_*`, `links` | Selected target rules and pinned artifacts |
| `validation` | `enabled`, `backend`, `endpoint`, `container_endpoint`, `terminology_server`, `additional_packages`, `timeout_seconds`, `fail_on_error` | Local full-resource/guide validation before grouping FHIR input |
| `aggregation` | `engine="duckdb"`, `group_by` | Grouped sets; optional exact point-value grouping |
| `output` | `path`, `mode`, `overwrite`, `include_distribution`, `lookup_pack` | CSV report prefix, item/group mode and optional captured evidence |
| `network` | `mode`, `allowed_http_hosts` | Offline/connected lookups and explicit local plain-HTTP hosts |
| `lookups.NAME` | `enabled`, `role`, `type`, `endpoint` or `path`, `systems` | Real primary/source/optional terminology services |
| `packages.NAME` | Registry, name, exact version, path, acceptance | Optional explicit BfArM package installation |
| `models.NAME` | Runtime, native URL reference, `container_base_url`, model ID, budgets | Optional local inference endpoint |
| `research` | Scope, providers, per-LOINC budget, catalogue | Separate page/study evidence retrieval |
| `quality` | `review_registry`, optional `concept_map` | Reviewed local mappings; clinician registry is the feedback route |
| `study` | `cohort`, `period_start`, `period_end`, `source_label` | Operator declarations for matched LIS/FHIR/CDS Hub comparisons |

## Paths and container mounts

For Python, relative input, output, artifact and secret-file paths resolve against the selected TOML's directory. `--config` itself resolves from your current directory. Keep a configuration beside the paths it describes, or use appropriate absolute paths.

For the supplied Compose stack, keep TOMLs in the repository root. `SQC_CONFIG_FILE` is a repository-relative host filename; the file always appears at `/app/pipeline.toml`. Its relative paths therefore resolve under `/app`.

| TOML path | Host location | Container location / access |
| --- | --- | --- |
| `examples/laboratory.csv` | Repository example | `/app/examples/laboratory.csv`, read-only |
| `../input/laboratory.csv` | Sibling private input directory | `/input/laboratory.csv`, read-only |
| `../input/terminologies/…` | Sibling real downloads | `/input/terminologies/…`, read-only |
| `local/laboratory.csv` | Your input export | `/app/local/laboratory.csv`, read-only for checking |
| `local/terminologies/…` | Installed real terminology | `/app/local/terminologies/…`, read-only for checking |
| `local/secrets/…` | Secret files | `/app/local/secrets/…`, read-only for checking |
| `output/laboratory-review.csv` | Reports | `/app/output/laboratory-review.csv`, writable |

A host path such as `/Users/…/lab.csv` is not automatically available in a container. Use sibling `../input/` (the default Compose input mount) or ignored `local/`. Set `SQC_INPUT_DIR` in `.env` only to choose a different host input directory; it still appears at `/input` in the container. For Python, adjust the TOML path to the actual host location when using a custom input directory. Package/catalogue/review-import services have writable `local/` mounts for their setup actions.

## CSV and aggregation

Replace the corresponding tables in `pipeline.toml`; do not append a second table with the same name. This example assumes the headers shown in the mapping:

```toml
[input]
type = "csv"
origin = "lis"
source_namespace = "site-lis-a"
path = "../input/laboratory.csv"
granularity = "individual"
delimiter = ","

[input.columns]
code = "test_code"
code_system = "test_system"
analyte_name = "test_name"
value = "result"
unit = "unit_text"
unit_code = "unit_code"
unit_system = "unit_system"
reference_ranges_json = "reference_ranges_json"
# source_id = "observation_id"     # Optional; needed for an explicit source ID in items mode.
```

Use `granularity="individual"` for one measurement per row. For an already distinct export, set `granularity="distinct"` and map `count="count"` only when real source frequencies are supplied. Omit count when unknown; do not manufacture a frequency of one. For aggregate extrema, map `value_min/value_max` instead of `value`.

Full `reference_ranges_json` preserves all intervals and qualifiers. For one unqualified interval only, map `reference_low`, `reference_high`, `reference_unit`, `reference_unit_system` instead. Preserve code versions, comparators, status and available method/specimen context. The optional `specimen` role expects a resolved LOINC system-axis token, not a raw SNOMED code. [All field roles and examples](data-contract.md#minimum-export).

`output.mode="distinct"` combines eligible values per complete guarded set. Names, codings/editions, units, all reference qualifiers, comparators, status and context remain part of the guards. To retain exact point values as separate sets:

```toml
[aggregation]
engine = "duckdb"
group_by = ["code", "code_system", "unit", "unit_system", "reference_range", "value"]
```

The five non-value fields above are mandatory grouping keys. Min/max-only inputs cannot use exact-value grouping or reconstruct mean/SE/median/IQR. `output.mode="items"` preserves IDs/locators and requires individual input; distinct input cannot restore them.

## FHIR Bundle and NDJSON

Replace `[input]` with:

```toml
[input]
type = "fhir_file"
origin = "fhir"
source_namespace = "site-fhir-a"
path = "local/laboratory.bundle.json"
granularity = "individual"
```

Remove `[input.columns]` and `[input.defaults]`. The native adapter reads R4 Observation fields, alternative codings, components and all reference ranges. It supports Bundle/JSON, NDJSON files and NDJSON directories; export one current version per Observation ID. Optional `input.elements` is limited to selected scalar `Observation.*` paths; components use the native reader. [Runnable source example](../examples/lab/fhir.toml).

Extract resources upstream using TORCH/CRTDL or the server's supported CQL. A count-only MeasureReport cannot supply measurements, distributions or intervals. LIS vendor APIs likewise need to produce a supported file or mapped view. [MII/FDPG placement](mii-quality.md) · [CQL selection example](../examples/studies/LaboratorySelection.cql).

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

For SQLite, use `input.type="sqlite"`, `input.path="local/lab.sqlite"`, the column mapping and `database.DB_TABLE="lab"`; omit PostgreSQL connection fields. It opens read-only. DuckDB is the aggregation engine for every input type, not an additional database server.

## Profiles and terminology roles

The starter explicitly pins FHIR R4 and MII Laboratory 2026.0.3. `STRUCTURE_DEFINITION` must be an installed resolved R4 Observation snapshot; `IG_PACKAGE`, `IG_VERSION` and `IG_CANONICAL` record its identity. Alternatively use `PROFILE_FILE` for a reviewed selected-element policy; never configure both. `VALUE_SETS` lists real versioned ValueSet JSON used to derive bound systems. [Pinned profile example](../examples/lab/mii-lis.toml).

Profiles define constraints/bindings, not API endpoint URLs or credentials. The MII snapshot fixes the quantitative unit system to UCUM and gives laboratory codes a **preferred** LOINC binding. ValueSet routing does not expand filters or prove membership. Unspecified selected rules fall back to international R4; explicit prohibitions remain effective. Set `validation.enabled=true` to run the selected local Matchbox/HL7 or MII service before grouping actual FHIR input. The complete guide and dependencies are pinned independently of the selected-element importer. [Validation configuration and examples](validation.md).

| Lookup role | Use | Example |
| --- | --- | --- |
| `primary` | Verify target terminology identity/unit grammar | Dedicated NLM UCUM and LOINC FHIR services |
| `source` | Check what the LIS's original local codes/units mean | Real site CSV, SQLite, FHIR artifacts or API |
| `optional` | Additional labels, aliases and candidates | BfArM supplements, SemLookP / OLS or a configured website |

An optional result cannot reverse a confirmed primary rejection. A valid local code/unit is separate from target compatibility. Without a real source catalogue, membership stays unknown.

```toml
[lookups.lis_catalogue]
enabled = true
role = "source"
type = "csv"
path = "local/terminologies/lis-catalogue.csv"
systems = ["https://your-site.example/lis/tests", "https://your-site.example/lis/units"]
complete = false
```

This references your actual file; the project does not generate local terminology. CSV catalogues require `system,code`, optionally `version,display,definition,expected_unit_code,expected_unit_system,specimen,method`. Set `complete=true` only for a complete edition.

Supported types: `ucum`, `loinc`, generic `fhir`, `csv`, `sqlite`, `fhir_artifacts`, `website`, `ols`, `who_icd`. Set `enabled=true` deliberately, one dedicated UCUM and LOINC service each. API adapters use `endpoint`; dedicated UCUM/LOINC also accept `endpoint_env`. Local adapters use `path` and declared `systems`. WHO ICD-11 is a standalone lookup, not a Condition batch check. [Adapter-specific fields, access and setup](lookups.md#choose-local-files-or-an-api) · [Optional terminology TOML](../terminology.example.toml).

## Full FHIR validation

`validator-init` derives Matchbox/MII server settings from the selected profile/package, then the container build installs the guide dependencies. Native Python uses `validation.endpoint`; the container uses `container_endpoint`. Full resources are only sent to a local/LAN service; redirects and proxy forwarding are disabled. `validation.token_env`/`token_file` are optional references for a protected validator. `additional_packages` accepts pinned `package#version` entries. CSV/scalar database tuples are not full FHIR resources. [Exact setup and OperationOutcome outputs](validation.md).

## Network and credentials

The starter uses `network.mode="lookups"` for enabled real sources. For `"offline"`, install real local artifacts and reference actual captured UCUM/LOINC evidence in `input.snapshot`; absent evidence stays unverified. Capture a connected run with `output.lookup_pack="output/reference-pack.json"`. Research cannot retrieve remote evidence in offline mode.

`allowed_http_hosts` permits explicitly named local/plain-HTTP endpoints, including Compose `ollama`, Docker Desktop `host.docker.internal` and Podman `host.containers.internal`. It is not an outbound firewall. Public API URLs in the examples use HTTPS.

| Service | TOML references for credentials |
| --- | --- |
| Official LOINC Basic Auth | `username_env` / `username_file`, `password_env` / `password_file` |
| Generic FHIR / website bearer token | `token_env` / `token_file` |
| WHO ICD OAuth | `client_id_env` / `client_id_file`, `client_secret_env` / `client_secret_file` |
| PostgreSQL | `DB_DSN_ENV` / `DB_DSN_FILE`, or user/password references above |
| Local model requiring a key | `api_key_env` / `api_key_file` |
| PubMed optional key | `research.pubmed.API_KEY_ENV` / `API_KEY_FILE` |
| Protected package setup | `packages.NAME.token_env` / `token_file`, with explicit conditions acceptance |

Environment references name a variable whose value lives in `.env` or the runtime. File references point to a secret file containing only the value; mount it under `local/secrets/` for Compose. Plain Python can use file references without loading `.env`. The default user TOMLs and files under `local/`/`output/` are excluded from Git, as are `.env` credentials. Keep other site configurations under ignored `local/` or add them to your ignore rules.

## Local models and research

A deterministic `run-plausibility-check` does not use a model, even if the TOML enables research. Use the separate `research-evidence` command on the completed audited report. The legacy `plausibility batch` command still honours `research.enabled`.

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

`response_mode` supports `json_schema` or `json_object` with the same application guards. Optional controls include `reasoning_effort`, `enable_thinking`, `expected_digest` and `pull_timeout_seconds`; use only controls supported by the selected server/model. [Provisioning and native URLs](shipped-stack.md#ollama).

```toml
[research]
enabled = false # research-evidence activates research explicitly
scope = "studies"                 # lookup uses pages/catalogues without study databases.
sources = ["pubmed", "europe_pmc"]
max_articles_per_loinc = 3         # 1–12 total unique articles per exact code/edition.
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.pubmed]
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAX_RESULTS = 1
# EMAIL = "study-contact@example.org"
# API_KEY_ENV = "PUBMED_API_KEY"

[research.europe_pmc]
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESULTS = 1
# QUERY_FILTER = '(human OR humans OR patients OR volunteers) NOT (feline OR cats OR canine OR dogs OR rats OR mice OR veterinary)'
```

Prepare the real catalogue with `catalog-init` and the model with `model-init` or native setup. Provider limits bound query results; the unique-article cap is separate and retrieval is cached per code/edition. `--plan` exposes tasks/schemas/searches; executed `.trace.jsonl` records actual prompts, queries and guarded responses. Public evidence supports clinician adjudication, not an automatic semantic-correctness judgment. [Research and feedback](research.md).

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

Only CSV is the batch data output format; HTML, summary, evidence and audit sidecars are always produced. Pick a fresh path to preserve prior reports; research needs a fresh output prefix too. [Artifact contents](data-contract.md#output-artifacts).

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

Links are for readers; they neither download nor substitute validation artifacts. Install a real R4 StructureDefinition **snapshot**, supply `STRUCTURE_DEFINITION`, pin `IG_PACKAGE`/`IG_VERSION` and configure actual `VALUE_SETS`. Matchbox/HL7 (or MII) validates the individual FHIR document against the installed guide before grouping. CSV/DB aggregates receive semantic-set checks and cannot establish full resource conformance. Validate the actual generated FHIR after applying approved mappings. [Validator setup](validation.md).

A GitHub branch can change; the guide edition and source-file hashes establish what was actually checked. Use HTTP(S) documentation URLs without embedded credentials or query strings. Endpoint credentials stay in `.env` or `local/secrets/`, not these links.
