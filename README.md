# Laboratory semantic plausibility

Help clinicians and IT teams populate the selected **MII KDS laboratory FHIR Observation** with a defensible code, name, numeric value, unit and complete reference context. The pipeline checks the source set, proposes target coding and transformations, gathers evidence of reported practice and reuses clinician-approved mappings in later runs.

**Validate FHIR input → check semantic sets → research evidence → clinician review → scoped mapping reuse.** Full FHIR resources can be validated locally with Matchbox/HL7 or the MII validator against the pinned implementation guide. Source records remain unchanged; apply and revalidate approved mappings in institutional ETL.

## Run a check

Input: CSV, FHIR R4 Bundle/NDJSON, read-only PostgreSQL view or SQLite extract. Distinct exports are the main use case; individual input can retain resource IDs. Run these commands from the repository directory.

### Prepare once

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p ../input local output
```

**`pipeline.toml` is the default configuration.** It defines input/columns, profiles, lookups, model and output; `.env` contains credentials and optional container settings. No shell exports are needed. For the first run, keep the supplied example input. For your own data, edit `[input]` and `[input.columns]` in the TOML. Put private exports/downloads in sibling `../input/` or ignored `local/`; both are mounted read-only. Credentials stay in `.env`/secret files.

### Choose one way to run

**Python and uv** — uv provides Python 3.14 and the locked dependencies:

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.toml
```

The direct script uses the same configuration: `uv run --no-sync --env-file .env python run-plausibility-check.py --config pipeline.toml`.

**Docker** — Docker with Compose:

```sh
docker compose run --rm --build run-plausibility-check
```

**Podman** — Podman with `podman-compose`; start its machine first on macOS/Windows:

```sh
podman-compose run --rm --build run-plausibility-check
```

The runner checks once and exits. Its default is a deterministic check, even when research is enabled in the TOML. The examples use the actual MII Laboratory 2026.0.3 snapshot, a real referenced HL7 ValueSet, NLM UCUM and a public LOINC FHIR test service. Measurements are illustrative; lookup responses are real.

Open `output/laboratory-review.html` and its CSV. The example produces **8 input rows → 7 review groups / 84 source occurrences**. Set a fresh `output.path` for another run.

Existing installation: in `.env`, change an old `SQC_CONFIG_FILE=pipeline.example.toml` to `SQC_CONFIG_FILE=pipeline.toml`. Remove any old shell assignment with `unset SQC_CONFIG_FILE`; the copy commands preserve existing files. [Setup and repeat runs](docs/shipped-stack.md) · [Configuration reference and source recipes](docs/configuration.md).

## Add evidence research

Use the **same `pipeline.toml`**. The explicit `research-evidence` command activates research; `run-plausibility-check` stays deterministic. The starter selects Europe PMC + PubMed and a local Ollama model. Prepare once, then research the existing report:

```sh
docker compose --profile research up -d ollama
docker compose --profile research run --rm --build model-init
docker compose --profile research run --rm catalog-init
docker compose --profile research run --rm research-evidence
```

For Podman replace `docker compose` with `podman-compose`. The TOML's `models.local.container_base_url` selects the Compose model; native Python uses `base_url` or `base_url_env`. No second research TOML or selector file is needed.

For native Ollama and Python, after starting/provisioning the model and catalogue:

```sh
uv run --no-sync research-evidence --config pipeline.toml --plan
uv run --no-sync --env-file .env research-evidence --config pipeline.toml
```

Open **`output/laboratory-review.research.html`**. Each row shows **source set → linked terminology → complete proposal**: LOINC/system/edition, analyte/component, canonical name, unit, transformation of values and each reference bound, and sourced study evidence. API/file editions, article URLs/quotes and available hashes sit beside the proposal. Unresolved identity or conversion remains explicit; clinicians adjudicate the whole set.

The research command reads `output.path` from TOML by default. `--report` selects another checked CSV **with its evidence/summary/audit sidecars**; a raw CSV needs the check first. `--out output/lab.research-2.csv` selects a fresh research prefix. Queries exclude hospital values/reference bounds and patient IDs. Executed searches/prompts/replies are in `.research.trace.jsonl`.

[Exact Python/Docker/Podman commands and model setup](docs/shipped-stack.md#ollama) · [Evidence and provenance](docs/research.md).

## Add FHIR and guide validation

```sh
podman-compose --profile validation run --rm --build validator-init
podman-compose --profile validation up -d blaze
podman-compose --profile validation run --rm terminology-init
podman-compose --profile validation up -d --build matchbox
```

For Docker use `docker compose`. Set `validation.enabled=true` for automatic validation of `fhir_file` input before aggregation. The same command can validate a FHIR file explicitly: `run-plausibility-check validate-fhir --config pipeline.toml --input examples/fhir/laboratory-valid.bundle.json --out output/fhir-valid.jsonl`. Matchbox uses the official HL7 validator; its pinned MII guide/dependencies are installed during the image build. [Complete setup, examples and MII alternative](docs/validation.md).

## Feed clinician reviews back

Return review columns with the original `tuple_id`, reviewer, timestamp, evidence and approved target/factor. Import them into an ignored local registry, then reference `quality.review_registry` in TOML. Future checks reuse approvals only for the same source/site, full semantic context, catalogue fingerprint and profile edition; target codes/units/factors are rechecked. Rejected/deferred/revoked decisions remain non-executable.

[Review import and concrete example](docs/research.md#clinician-feedback) · [Goal and requirements](docs/requirements.md).

## Configure terminology

The profile defines system/binding requirements; it does not provide endpoint URLs. Select `role="primary"` for target verification, `"source"` for your real LIS catalogue and `"optional"` for additional labels/search evidence. Local codes and units are checked independently of target-profile compatibility. Unconfigured source terminology stays unverified; no site catalogue is invented.

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc --code 718-7
```

[Input, profiles, paths and service roles](docs/configuration.md) · [Local downloads, APIs and credentials](docs/lookups.md) · [Docker, Podman and Python](docs/shipped-stack.md) · [Field contract](docs/data-contract.md).

## Architecture

![Laboratory check, evidence research and clinician feedback](docs/architecture.svg)

## Decision flow

![Source verification, target mapping, research and scoped review reuse](docs/decision-flow.svg)

```python
from semantic_plausibility import run_plausibility_check, research_evidence, import_reviews
result = run_plausibility_check("pipeline.toml")
# Optional, after preparing the local model:
# dossier = research_evidence("pipeline.toml", report=result.csv)
# import_reviews(result.csv, "local/clinician-review.csv", "local/mappings.json", "pipeline.toml")
```

Python 3.14; DuckDB 1.5.6; optional psycopg 3.3.6; exact dependencies in `uv.lock`. [Implementation/tests](docs/implementation.md) · [MII quality environment](docs/mii-quality.md).

Laboratory Observation is implemented. [Medication is the next domain](docs/requirements.md#medication-next): product/form, ingredient, concentration, dose/unit, route, timing and package amount must be assessed together. Condition and Procedure are later adapters. FDPG evidence access is later work requiring an appropriate authorised interface/export. Apache-2.0; terminology/model licences remain separate.
