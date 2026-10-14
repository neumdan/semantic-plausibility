# Semantic plausibility

A local Python library and batch runner for reviewing **laboratory and medication exports** against real terminology and selected FHIR rules. It writes a **CSV + HTML report** showing the source set, linked definitions, findings, recommendations and their evidence. Clinicians decide semantic correctness; the runner does not change source records.

| Domain | Implemented checks |
| --- | --- |
| Laboratory `Observation` | Code/name/unit lookup, LOINC axes and example-unit fit, numeric values and reference bounds, verified unit transformations; optional local AI and study evidence |
| `Medication` | Product/ingredient/form codes, source strength quantities, canonical names and available published ATC/DDD context |
| `MedicationRequest`, `MedicationStatement` | The product checks plus every dosage instruction, dose units, timing/ranges and conditional strength calculations |
| `MedicationAdministration` | The product checks plus dose/rate/duration consistency; uninterrupted constant delivery must be explicitly established |

Full FHIR validation is optional and applies to **complete resources**. Medication clinical dose appropriateness, interactions, AI research, diagnosis and procedure adapters are outside the implemented scope.

## 1. First run: choose a runtime

From this repository, prepare the supplied example without overwriting existing settings:

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
chmod 600 .env
mkdir -p ../input local output
```

A fresh `pipeline.toml` selects `examples/laboratory.csv`: **8 synthetic input rows → 7 distinct sets, representing 84 source occurrences**. All terminology responses come from real services. The default public UCUM/LOINC endpoints need no credentials. An existing `pipeline.toml` is preserved: inspect its `[input]` before running.

Choose **one** of these runtimes:

### Python / uv

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed; it provisions the pinned Python runtime and dependencies.

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env run-plausibility-check config-check --config pipeline.toml
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.toml --out output/01-lookup.csv
```

The direct script is equivalent: `uv run --no-sync --env-file .env python run-plausibility-check.py --config pipeline.toml`. PostgreSQL users add `--extra postgres` to `uv sync`.

### Docker Compose

```sh
docker compose build run-plausibility-check
docker compose run --rm run-plausibility-check config-check
docker compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

### Podman Compose

Start your Podman engine first; macOS/Windows use `podman machine start` after initial machine setup. Install `podman-compose` as the Compose provider.

```sh
podman-compose build run-plausibility-check
podman-compose run --rm run-plausibility-check config-check
podman-compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

Open **`output/01-lookup.html`**, alongside its CSV. Each check runs once and exits. Use a fresh output filename on repeat runs. TOML/input changes need another run; code/dependency changes need a rebuild. No model or validator is started for this first check.

## 2. Configure your own check

Work through these categories in order. [Complete configuration guide](docs/configuration.md) · [English HTML manual](docs/index.html).

| Choice | Where to configure | What you supply |
| --- | --- | --- |
| **Domain** | `[general].RESOURCE_TYPE` | One of the five resource types above; start from its example TOML |
| **Input** | `[input]` | `csv`, `fhir_file`, `postgres` or `sqlite`; path or database access; individual vs already distinct |
| **Source mapping** | `[input.columns]`, `[database]` | Logical roles → your actual headers/columns; database schema and table/view. Native FHIR needs no column map |
| **Target rules** | `[specification]`, optional `[validation]` | FHIR R4, pinned MII package/profile and local artifacts. A profile does **not** infer your database column meanings |
| **Terminology** | `[lookups.NAME]`, `[terminology]` | Real downloaded catalogues and/or API endpoints; exact code-system URIs; explicit fallback policy |
| **Secrets** | `.env` or secret files | Only credentials for services you enable; TOML holds references such as `password_env`, never the password |
| **Output** | `[output]`, `[logging]` | CSV path, `distinct` or `items`, console verbosity. HTML, evidence, summary and audit accompany every report |

**Start with exports.** Put private data/downloads in sibling `../input/` or ignored `local/`. The supplied containers mount both. CSV and database mappings are identical semantic roles. Full FHIR can be one JSON resource, a Bundle, array, NDJSON, or a complete `resource_json` CSV cell. Extract FHIR server data upstream with TORCH/CQL; export actual resources, not a count-only MeasureReport. LIS APIs likewise export to a supported format.

For labs preserve **code + system/edition + analyte/name + value or observed bounds + unit/system + all reference intervals**, plus available specimen, method and status. For medication preserve product/ingredients/strength, every dose/unit/route/timing instruction and status. Unknown counts stay unknown; distinct output removes IDs, while `items` retains IDs/locators from individual input.

**The target specification and output format are separate.** MII/FHIR defines checks. CSV/HTML is the current report format. OMOP and CDS Hub tables can be mapped as input views; automatic OMOP/CDS Hub schema discovery and FHIR/database output writers are not implemented. After review, apply approved mappings in your ETL and validate the resulting FHIR.

### Local terminology or API

| Source | Local setup | API alternative |
| --- | --- | --- |
| LOINC | Official full `Loinc.csv`, `type="loinc_csv"`, actual release edition | Official LOINC FHIR API with username/password, or a configured FHIR service |
| BfArM translations | Downloaded FHIR JSON/package directory, `type="fhir_artifacts"` | Your FHIR terminology server hosting the same edition |
| German ATC/DDD | BfArM `bfarm.terminologien.atcgm#2026.0.0`; retains published `ddd` text with provenance | Configured FHIR API containing German ATC; WHO ATC has a different canonical system |
| UCUM | Real saved lookup pack for lab syntax/conversions; catalogues can supply labels/membership | NLM UCUM grammar and conversion service |
| Site codes | Actual CSV, SQLite catalogue or FHIR CodeSystem | Your configured terminology API |

[Exact download/API recipes](docs/lookups.md) include conditions, directory layout and credentials. `terminology.example.toml` contains disabled optional sources to enable deliberately. **BfArM LOINC translations alone cannot establish full LOINC membership or units.** ATC classification and DDD do not verify a product's formulation or a patient's dose.

The default `terminology.strategy="fallback"` tries local sources first, then enabled compatible APIs until membership is established. `"all"` cross-checks every enabled source for that exact system. Disagreement stays unresolved. Missing coverage, unavailable services and editions appear in the report. The inventory is a configuration reference; it is not permission to contact every listed service.

`TX_FHIR_BEARER_TOKEN` is an optional bearer token issued by **your chosen FHIR terminology service**. Leave it unused unless that service requires it. Downloading files does not require an API credential for local reads. [Service inventory](docs/lookups.csv) · [Canonical systems](docs/terminology-catalog.csv).

## 3. Check setup, then run

```sh
# Validate input mappings, local files, target rules and selected output.
uv run --no-sync --env-file .env run-plausibility-check config-check --config pipeline.toml
# Optional: also test database table/columns and configured API/model connections.
uv run --no-sync --env-file .env run-plausibility-check config-check --config pipeline.toml --connections
# Inspect just one enabled source.
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc --code 718-7
# Check the dataset. Change the output name for a new run.
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.toml --out output/my-review.csv --log-level INFO
```

For containers replace the Python prefix with `docker compose run --rm run-plausibility-check` or `podman-compose run --rm run-plausibility-check`, followed by `config-check`, `lookup` or `check`. Root `pipeline.toml` is mounted automatically. For a supplied recipe use `--config /app/examples/medication/statement.toml`; site TOMLs under `local/` are available at `/app/local/…`. Relative data/output paths resolve from the selected TOML.

Logs support `DEBUG`, `INFO`, `WARNING`, `ERROR`. Progress goes to stderr, results to stdout. Console logs avoid clinical values and credentials; the hash-linked audit remains complete at every log level. `config-check` reports configuration readiness, not a clinical result.

## 4. Optional validation and AI

| Mode | Input → report | Setup |
| --- | --- | --- |
| Lookup | Export → checked CSV/HTML | Real catalogues/APIs |
| Lookup + FHIR validation | Complete FHIR or JSON-in-CSV → checked CSV/HTML | Local Matchbox/HL7 validator + pinned MII guide; [instructions](docs/validation.md) |
| Laboratory AI recommendations | Original checked report + sidecars → candidate report | Local Ollama and real unit catalogue; `research-evidence --scope lookup` |
| Laboratory AI + evidence | Same original report → sourced literature dossier | Above plus Europe PMC/PubMed; `research-evidence --scope studies` |

The application performs bounded retrieval and exact rechecks; the model may abstain. Keep report sidecars together. [Copyable commands for all runtimes](docs/shipped-stack.md#four-run-modes) · [Prompts and clinician feedback](docs/research.md).

Run the existing **four lab modes + request/administration** together with Podman:

```sh
bash tools/run-podman-examples.sh --dry-run
bash tools/run-podman-examples.sh
```

This script prepares services and creates a separate report directory per invocation under `output/podman-examples-…/`, with `index.html`, outcomes and a run log. It reuses the fixed image/project, models and volumes, and stops its services afterwards. The new product/statement recipes are separate:

```sh
podman-compose run --rm run-plausibility-check check --config /app/examples/medication/product.toml
podman-compose run --rm run-plausibility-check check --config /app/examples/medication/statement.toml
```

Their German ATC lookup becomes verifiable after you install/enable that catalogue; missing evidence is shown as unknown. [All medication recipes, example calculations and limits](examples/medication/README.md).

## Read the report

Read identity, unit syntax, code–unit fit, numeric/reference checks and FHIR validation separately. Membership does not prove that the correct analyte was selected. First-example fallback is a review candidate; a verified conversion supplies a factor/offset for values and bounds. Sources, attempted lookups, editions and gaps are beside each set. Numeric readiness is not MII certification or clinical correctness.

Medication reports retain every regimen, conditional calculations and `ddd_references_json`. A synthetic 5 mL dose at source-declared 20 mg/mL can yield **100 mg conditional on verified units and that source strength**. A `not-taken` statement cannot establish an administration. Missing references, ambiguous units and unknown product strength remain review tasks.

The CSV is the full result; HTML previews at most 2,000 rows. `.summary.json`, `.evidence.jsonl`, `.audit.jsonl` hold counts, evidence and hashes. AI adds `.trace.jsonl` with queries/prompts and responses. [Field contract](docs/data-contract.md) · [MII quality-check environment](docs/mii-quality.md).

## Architecture and decisions

![Architecture](docs/architecture.svg)

![Decision flow](docs/decision-flow.svg)

[Source-to-report flow](docs/batch-flow.svg) · [Requirements](docs/requirements.md) · [Implementation and tests](docs/implementation.md).

## Library and storage

```python
from semantic_plausibility import run_plausibility_check
result = run_plausibility_check("pipeline.toml", output="output/check.csv")
```

Python 3.14, DuckDB 1.5.6, optional psycopg 3.3.6; exact versions are locked in `uv.lock`. The optional local model starter is Ollama + `ministral-3:3b`; other local compatible endpoints are configurable.

Use one workflow to avoid duplicate model volumes. The runner image is `semantic-plausibility-stack:local`; rebuild after code changes and reuse it afterwards. `podman system df` inspects storage; `podman image prune` optionally removes unused untagged images. Preserve model/Blaze volumes; do not prune volumes or use `down --volumes` for image cleanup. [Storage details](docs/shipped-stack.md#storage-and-persistent-data).

## Commit and push

```sh
git status --short
git diff --stat
git add -A
git diff --cached --stat
git diff --cached --check
git commit -m "Clarify setup and add local terminology and medication support"
git push origin main
```

Inspect staged paths before committing. `.env`, private root TOMLs, `local/`, `output/`, caches and builds are ignored. Keep downloads/exports under `local/` or `../input/`, not `examples/`. If Git reports macOS “Operation canceled” or “mmap failed”, inspect `ls -lO` on the named file: iCloud placeholders need **Keep Downloaded** in Finder, including `.git`. Keep future clones outside synced Desktop/Documents, for example `~/Developer/`.

Apache-2.0; terminology/model licences remain separate. No automatic uploads, resource rewrites or patient-specific prescribing recommendations.
