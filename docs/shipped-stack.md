# Setup and operation

Use one TOML file for the pipeline and `.env` or secret files for credentials. Python, Docker Compose and `podman-compose` all default to **`pipeline.toml`**. A check runs once and writes local reports; model research is a separate command.

## First setup

Run from the repository directory. Choose Python with uv, Docker with Compose, or Podman with `podman-compose` installed. On macOS/Windows, initialise a Podman machine once with `podman machine init` if needed, then start it with `podman machine start`.

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p local output
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

Both engines use the same image and Compose file. The host TOML is mounted read-only at `/app/pipeline.toml`; `examples/` and `local/` are read-only during a check, and `output/` receives reports. Create the TOML during first setup before starting a container. Rebuild after code/dependency changes; configuration/input changes require only a new run.

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

Edit `pipeline.toml`, starting with `[input]`, `[input.columns]` and `[output]`. Put your CSV under `local/`; preserve real source coding, units, names and complete reference context. Configure the real source catalogue when your LIS uses local codes. [Configuration reference and source recipes](configuration.md) · [Field contract](data-contract.md).

For a repeat run, change the existing output table:

```toml
[output]
mode = "distinct"
path = "output/laboratory-review-2.csv"
```

Then repeat the same Python/Docker/Podman command. Reports already present are protected; `overwrite=true` is an explicit alternative. Change the audit filename in verification commands to match the new prefix.

## Select another TOML

Python selects a file directly: `run-plausibility-check --config pipeline.research.toml`. Containers use the `.env` line `SQC_CONFIG_FILE=pipeline.research.toml`; the runner still receives `/app/pipeline.toml` inside the container. Keep container TOMLs in the repository root and their data paths under the mounted `examples/`, `local/` and `output/` directories. [Path rules](configuration.md#paths-and-container-mounts).

To keep `.env` unchanged when switching container configurations, create `local/research.env` containing this one line:

```dotenv
SQC_CONFIG_FILE=pipeline.research.toml
```

Then pass it as a parameter:

```sh
docker compose --env-file local/research.env --profile research run --rm research-evidence
podman-compose --env-file local/research.env --profile research run --rm research-evidence
```

This selector file chooses the host TOML for Compose interpolation. The supplied services still load credentials/model settings from the project `.env`. Any Compose image/port overrides you need must also be present in the selector file. No shell exports are needed.

## Individual terminology commands

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc --code 718-7
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup ucum --code g/dL
```

For Docker or Podman, place `lookup --config /app/pipeline.toml --lookup loinc --code 718-7` after `compose run --rm run-plausibility-check`. `lookup` contacts only the named enabled service and starts no batch or model.

## Ollama

Copy `pipeline.research.example.toml` to `pipeline.research.toml`. Set `SQC_CONFIG_FILE=pipeline.research.toml` and `LOCAL_MODEL_BASE_URL=http://ollama:11434/v1` in `.env`. Choose the served model with `models.local.model`. Allocate adequate VM memory for the 3B example (start with 8 GiB).

```sh
cp -n pipeline.research.example.toml pipeline.research.toml
docker compose --profile research up -d ollama
docker compose --profile research run --rm --build model-init
docker compose --profile research run --rm catalog-init
```

For Podman, replace `docker compose` with `podman-compose` in these commands. `model-init` provisions the selected model; `catalog-init` downloads actual UCUM definitions/common examples. Ollama stays running until you stop it with `docker compose --profile research stop ollama` or the Podman equivalent. Checks/research jobs exit after one run.

For native macOS Ollama, start/pull the model yourself. Use `LOCAL_MODEL_BASE_URL=http://host.containers.internal:11434/v1` for Podman, `http://host.docker.internal:11434/v1` for Docker Desktop, or `http://127.0.0.1:11434/v1` for Python. Omit Compose `ollama`/`model-init` for native inference. On Linux Docker, a host service needs an explicit host-gateway alias/network setup; the Compose Ollama service avoids that dependency. LM Studio uses `runtime="external"` and its actual served model ID.

## Separate evidence research and clinician feedback

Research a completed audited check report. Python:

```sh
uv run --no-sync research-evidence --config pipeline.research.toml --report output/laboratory-review.csv --plan
uv run --no-sync --env-file .env research-evidence --config pipeline.research.toml --report output/laboratory-review.csv
```

Docker, with the research TOML selected in `.env` (or via the selector file above):

```sh
docker compose --profile research run --rm research-evidence research-evidence --config /app/pipeline.toml --report /app/output/laboratory-review.csv --plan
docker compose --profile research run --rm research-evidence research-evidence --config /app/pipeline.toml --report /app/output/laboratory-review.csv
```

Use `podman-compose` for Podman. The repeated name is the service followed by the CLI command. With default report selection, `docker compose --profile research run --rm research-evidence` suffices. `--plan` shows prompts/searches without network/model calls. The dossier uses a new `.research` prefix and records executed prompts/queries in `.research.trace.jsonl`; it does not requery the hospital database.

After clinician review, select `SQC_CONFIG_FILE=pipeline.toml` in `.env` again:

```sh
docker compose --profile review run --rm review-import review-import --config /app/pipeline.toml --report /app/output/laboratory-review.csv --review /app/local/clinician-review.csv --registry /app/local/clinician-mappings.json
```

The Python command and review fields are in [clinician feedback](research.md#clinician-feedback). Enable `quality.review_registry="local/clinician-mappings.json"`, keep the source namespace stable and select a fresh check output path for reuse.

## Local terminology and disconnected checking

Install real artifacts under `local/terminologies/` or configure APIs with secret references. For BfArM setup, copy `terminology.example.toml` to `terminology.toml`, select `SQC_CONFIG_FILE=terminology.toml` in `.env`, and run `docker compose --profile terminology run --rm --build package-init` (or `podman-compose`). Package download conditions must be explicitly accepted; normal checks never download packages automatically. [Local/API setup](lookups.md#choose-local-files-or-an-api).

For disconnected operation, provide installed artifacts and actual captured UCUM/LOINC evidence in `input.snapshot`, then set `network.mode="offline"`. Select that TOML before running a prebuilt image with either engine:

```sh
docker compose -f compose.yaml -f compose.offline.yaml run --rm run-plausibility-check
podman-compose -f compose.yaml -f compose.offline.yaml run --rm run-plausibility-check
```

Missing evidence remains unknown. Model provisioning/research need connectivity or transferred artifacts. Local values/IDs are not sent to public terminology/study services. Scope hashes check integrity; institutional authorisation determines who may approve mappings.
