# Setup and operation

Use one TOML file for the pipeline and `.env` or secret files for credentials. Python, Docker Compose and `podman-compose` all default to **`pipeline.toml`**. A check runs once and writes local reports; model research is a separate command.

## First setup

Run from the repository directory. Choose Python with uv, Docker with Compose, or Podman with `podman-compose` installed. On macOS/Windows, initialise a Podman machine once with `podman machine init` if needed, then start it with `podman machine start`.

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p ../input local output
```

The example input is `examples/laboratory.csv`: eight illustrative measurement rows checked with real public terminology services. No API credentials or local model are needed for this first check. The supplied MII Laboratory 2026.0.3 profile is selected.

The copy commands preserve existing files. For an older installation, set `SQC_CONFIG_FILE=pipeline.toml` in your existing `.env`. If it was previously set in your shell, run `unset SQC_CONFIG_FILE` once and remove the old assignment from your shell startup file. [Compose variable precedence](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

## Python and uv

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.toml
```

The direct script is equivalent:

```sh
uv run --no-sync --env-file .env python run-plausibility-check.py --config pipeline.toml
```

`--config` selects a TOML file; omitted, it defaults to `pipeline.toml` in the current directory. uv provides Python 3.14 and the locked dependencies. Add `--extra postgres` to `uv sync` for direct PostgreSQL access.

Without uv, use Python 3.14, create/activate a virtual environment and run `python -m pip install .` (or `python -m pip install '.[postgres]'`). Then run `python run-plausibility-check.py --config pipeline.toml`. Plain Python does not automatically load `.env`: use the configured `*_file` secret references or an environment supplied by your runtime. The uv command above loads `.env` explicitly.

## Docker Compose

```sh
docker compose run --rm --build run-plausibility-check
```

## Podman Compose

```sh
podman-compose run --rm --build run-plausibility-check
```

Use `podman-compose` directly to select that provider without changing shell settings. `podman compose` also works when your Podman installation already selects a suitable Compose provider; the documented commands use `podman-compose` consistently.

Both engines use the same image and Compose file. The host TOML is mounted read-only at `/app/pipeline.toml`; `examples/`, `local/` and sibling `../input/` are read-only during a check, and `output/` receives reports. Create the TOML during first setup before starting a container. Rebuild containers (or rerun `uv sync --locked --no-editable`) after code/dependency changes; configuration/input changes require only a new run.

## Inspect configuration and output

Check setup without contacting terminology services:

```sh
uv run --no-sync --env-file .env run-plausibility-check config-check --config pipeline.toml
# Docker alternative
docker compose run --rm run-plausibility-check config-check --config /app/pipeline.toml
# Podman alternative
podman-compose run --rm run-plausibility-check config-check --config /app/pipeline.toml
```

Add `--connections` to probe the selected database/terminology connections. Setup readiness is not a semantic data result. The actual check reads and assesses the input.

Open `output/laboratory-review.html` and CSV. The example gives **8 rows → 7 review groups / 84 source occurrences**. Verify artifact hashes with one of:

```sh
uv run --no-sync run-plausibility-check verify output/laboratory-review.audit.jsonl
docker compose run --rm run-plausibility-check verify /app/output/laboratory-review.audit.jsonl
podman-compose run --rm run-plausibility-check verify /app/output/laboratory-review.audit.jsonl
```

## Your data and repeat runs

Edit `pipeline.toml`, starting with `[input]`, `[input.columns]` and `[output]`. Put your CSV under sibling `../input/` (TOML `path="../input/laboratory.csv"`) or ignored `local/`; preserve real source coding, units, names and complete reference context. Configure the real source catalogue when your LIS uses local codes. [Configuration reference and source recipes](configuration.md) · [Field contract](data-contract.md).

For a repeat run, change the existing output table:

```toml
[output]
mode = "distinct"
path = "output/laboratory-review-2.csv"
```

Then repeat the same Python/Docker/Podman command. Reports already present are protected; `overwrite=true` is an explicit alternative. Change the audit filename in verification commands to match the new prefix.

## Select another TOML

The default check and research use the same `pipeline.toml`. Python selects a different file with `--config another.toml`. Compose selects it through `SQC_CONFIG_FILE=another.toml` in `.env` or a separate selector file passed with `--env-file`; container paths still resolve against `/app/pipeline.toml`. Extra configurations are useful for another source/profile, and are not required for research. [Paths and configuration](configuration.md#paths-and-container-mounts).

## Individual terminology commands

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc --code 718-7
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup ucum --code g/dL
```

For Docker or Podman, place `lookup --config /app/pipeline.toml --lookup loinc --code 718-7` after `compose run --rm run-plausibility-check`. `lookup` contacts only the named enabled service and starts no batch or model.

## Ollama

Keep the existing input/output/profile tables in `pipeline.toml`. Its research tables should contain:

```toml
[models.local]
runtime = "ollama"
base_url = "http://127.0.0.1:11434/v1"        # Native Ollama address
container_base_url = "http://ollama:11434/v1" # Compose address, used automatically
model = "ministral-3:3b"
temperature = 0
max_tokens = 2048
timeout_seconds = 180
response_mode = "json_schema"

[research]
enabled = false         # check stays deterministic; research-evidence activates this pass
scope = "studies"
sources = ["europe_pmc", "pubmed"]
max_articles_per_loinc = 3
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.europe_pmc]
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESULTS = 1
# QUERY_FILTER = '(human OR humans OR patients OR volunteers) NOT (feline OR cats OR canine OR dogs OR rats OR mice OR veterinary)'
```

Edit existing tables; do not append duplicate TOML tables. Europe PMC needs no key. The model endpoint is local; it receives retrieved public text, not local measured values or patient IDs. At least 8 GiB VM memory is a useful start for the 3B example; use 16 GiB with the validator stack.

### Docker: prepare once, run research

```sh
docker compose --profile research up -d ollama
docker compose --profile research run --rm --build model-init
docker compose --profile research run --rm catalog-init
# Run the check if its CSV/sidecars do not exist yet:
docker compose run --rm --build run-plausibility-check
docker compose --profile research run --rm research-evidence
```

### Podman: prepare once, run research

Start `podman machine start` in your terminal on macOS/Windows, then:

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm --build model-init
podman-compose --profile research run --rm catalog-init
# Run the check if its CSV/sidecars do not exist yet:
podman-compose run --rm --build run-plausibility-check
podman-compose --profile research run --rm research-evidence
```

The setup actions download the configured model and real UCUM catalogue. Skip `model-init`/`catalog-init` when those artifacts already exist. Model provisioning needs connectivity; subsequent inference is local. Research/check jobs exit; Ollama stays up until `docker compose --profile research stop ollama` (or the Podman equivalent).

### Native Python with Ollama

Start native Ollama (`ollama serve` if it is not already running). Set `LOCAL_MODEL_BASE_URL=http://127.0.0.1:11434/v1` in `.env`. In another terminal:

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env run-plausibility-check model-init --config pipeline.toml
uv run --no-sync run-plausibility-check catalog-init --config pipeline.toml
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.toml
uv run --no-sync --env-file .env research-evidence --config pipeline.toml
```

Skip setup/check actions that have already succeeded. For a Compose runner talking to native Ollama, change **`container_base_url`** in TOML to `http://host.containers.internal:11434/v1` (Podman) or `http://host.docker.internal:11434/v1` (Docker Desktop), and omit the Compose Ollama service. Linux Docker requires an explicit host-gateway setup. LM Studio uses `runtime="external"` with its actual model ID and local addresses.

## Read and repeat research

Open **`output/laboratory-review.research.html`** and the matching CSV. Rows retain the whole original set beside linked terminology and a complete proposed target with explanations/sources. The check's statuses/readiness remain unchanged. The research section reports accepted model calls; unavailable/rejected extraction remains visible.

`research-evidence` selects the checked report at TOML `output.path`. A manually altered check CSV fails integrity verification; clinician edits belong in a separate review copy. Keep the matching `.summary.json`, `.evidence.jsonl` and `.audit.jsonl` beside the CSV. To inspect prompts without contacting services:

```sh
uv run --no-sync research-evidence --config pipeline.toml --plan
podman-compose --profile research run --rm research-evidence research-evidence --config /app/pipeline.toml --plan
```

For an explicit CSV and fresh output prefix:

```sh
uv run --no-sync --env-file .env research-evidence --config pipeline.toml --report output/laboratory-review.csv --out output/laboratory-review.research-2.csv
podman-compose --profile research run --rm research-evidence research-evidence --config /app/pipeline.toml --report /app/output/laboratory-review.csv --out /app/output/laboratory-review.research-2.csv
```

For Docker replace `podman-compose` with `docker compose`. The repeated name is the service followed by the CLI command. Output includes CSV, HTML, evidence/summary and `.trace.jsonl` with actual searches, prompts and guarded replies. No hospital database is requeried. [Source provenance and clinician feedback](research.md).

After clinician review:

```sh
docker compose --profile review run --rm review-import review-import --config /app/pipeline.toml --report /app/output/laboratory-review.csv --review /app/local/clinician-review.csv --registry /app/local/clinician-mappings.json
```

Enable `quality.review_registry="local/clinician-mappings.json"`, keep the source namespace stable and select a fresh check output path for reuse.

## Local FHIR validation

Matchbox/HL7 validates actual FHIR inputs against the pinned MII guide; the MII validator is selectable. Run `validator-init`, build/start the local service and enable `[validation]` for FHIR input. Allocate 16 GiB to the VM for Ollama plus validation. [Exact Docker/Podman/Python commands, valid/invalid FHIR examples and resource privacy](validation.md).

## Local terminology and disconnected checking

Install real artifacts under `local/terminologies/` or configure APIs with secret references. For BfArM setup, copy `terminology.example.toml` to `terminology.toml`, select `SQC_CONFIG_FILE=terminology.toml` in `.env`, and run `docker compose --profile terminology run --rm --build package-init` (or `podman-compose`). Package download conditions must be explicitly accepted; normal checks never download packages automatically. [Local/API setup](lookups.md#choose-local-files-or-an-api).

For disconnected operation, provide installed artifacts and actual captured UCUM/LOINC evidence in `input.snapshot`, then set `network.mode="offline"`. Select that TOML before running a prebuilt image with either engine:

```sh
docker compose -f compose.yaml -f compose.offline.yaml run --rm run-plausibility-check
podman-compose -f compose.yaml -f compose.offline.yaml run --rm run-plausibility-check
```

Missing evidence remains unknown. Model provisioning/research need connectivity or transferred artifacts. Local values/IDs are not sent to public terminology/study services. Scope hashes check integrity; institutional authorisation determines who may approve mappings.
