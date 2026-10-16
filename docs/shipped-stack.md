# Containers, Python and models

## One compose.yaml

Use either `podman-compose` or `docker compose` with the same file. The table names the services to start; include the matching `--profile` explicitly, as required by Podman Compose.

| Task | Services / command | Persistent state |
| --- | --- | --- |
| Check CSV/FHIR/DB | `run --rm run-plausibility-check check` | Reports in `output/` |
| Acquire profile + bindings | `--profile setup run --rm profile-init` | Ignored `local/profiles/` |
| Read local terminology files | Runner with file sources in TOML | Your `local/terminologies/` files |
| Serve imported terminology | `--profile terminology up -d blaze`; `--profile terminology run --rm terminology-init` | Named `validation_blaze` volume; installation receipt in `local/validation/` |
| Local UCUM | `--profile terminology up -d --build ucum` | Built image with official pinned library |
| Local AI | `--profile research up -d ollama`; `--profile research run --rm model-init` | Shared `semantic-plausibility-ollama` model volume |
| Full FHIR conformance | `validator-init`, build/start `matchbox` | `local/validation/` setup and cached validator image; [instructions](validation.md) |
| SQLite exercise | `--profile examples run --rm prepare-examples` | `local/examples/lab.sqlite` |
| PostgreSQL exercise | `--profile laboratory-inputs up -d example-postgres` | Separate example DB volume; [input recipe](../examples/lab/README.md#postgresql) |
| MII validator alternative | `--profile mii-validation up -d mii-validator` after `validator-init` | IG/terminology cache volumes |

The runner is a **one-shot Python command**, backed by the same importable library used in native execution. It exits after writing reports. DuckDB performs local grouping; SQLite/PostgreSQL are input sources. Long-running services are optional.

`local/` and input mounts are read-only during checking. Setup/import commands receive writable runtime folders. Service ports bind to host loopback. Blaze and UCUM use the internal validation network. Ollama receives inference requests locally; it downloads selected weights during `model-init`. The model itself does not autonomously browse: Python retrieves selected terminology/pages/studies, and passes bounded source text to the model.

## Build and reuse

```sh
podman-compose build run-plausibility-check
podman-compose --profile setup run --rm profile-init
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check check --out output/first.csv
```

Build after source/dependency changes. Ordinary checks reuse the `semantic-plausibility-stack:local` image. The example script fingerprints build inputs and rebuilds only when needed. Models are reused across manual and example runs. Stop selected services with `podman-compose stop ollama blaze ucum matchbox`; their volumes remain. Keep volumes when using `down` so model/import downloads are retained.

Docker uses `docker compose` in place of `podman-compose`. Python uses `uv sync --locked --extra postgres` followed by `uv run --no-sync --env-file .env run-plausibility-check COMMAND`. The TOML stays the same; `container_base_url` / `container_endpoint` choose service-network addresses inside Compose.

## Models

### Compose Ollama

Set the model in **`pipeline.toml`**, then provision it:

```toml
[models.local]
runtime = "ollama"
base_url = "http://127.0.0.1:11434/v1"
container_base_url = "http://ollama:11434/v1"
model = "ministral-3:3b"
temperature = 0
max_tokens = 2048
timeout_seconds = 600
response_mode = "json_schema"
```

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm model-init
podman-compose --profile research run --rm catalog-init
podman-compose --profile research run --rm research-evidence research-evidence --scope studies --report output/first.csv --out output/evidence.csv
```

`model-init` queries Ollama, downloads the exact configured model when missing, and reports its digest. The model lives in the named volume, separate from source code. CPU inference takes longer than local accelerated inference; progress reports retrieval, screening, model extraction and completed groups. `--plan` on `research-evidence` displays the actual prompts and queries before inference.

### Your host Ollama or LM Studio

For host Ollama, retain `runtime="ollama"` and use your installed model ID. Set `container_base_url="http://host.containers.internal:11434/v1"` for Podman or `http://host.docker.internal:11434/v1` for Docker Desktop. Keep `base_url` for native Python. Configure the host service to accept the container connection on your trusted local interface.

For another **local OpenAI-compatible** service, set `runtime="external"`, its actual base URLs, and the model ID reported by that service. Load the model in that service, then run `research-evidence`. Add `api_key_env` or `api_key_file` if the server requires one. Use `response_mode="json_schema"` when supported; the runner validates returned JSON and evidence citations. For another LAN host, add its hostname to `network.allowed_http_hosts` for plain HTTP and keep the model endpoint local/LAN.

## Storage locations

| Contents | Default | Change through |
| --- | --- | --- |
| Selected pipeline | `pipeline.toml` | Native `--config`; Compose `SQC_CONFIG_FILE` in `.env` |
| Source directory | `catalog.csv` | `[catalog].path`; mount custom catalogues under `local/` |
| Downloads / profiles / secrets | `local/` | File paths in TOML; Compose host mount `SQC_LOCAL_DIR` |
| Private source exports | `../input/` or `local/input/` | `input.path`; Compose host mount `SQC_INPUT_DIR` |
| Reports | `output/` | `output.path` or per-run `--out` |
| Model weights | `semantic-plausibility-ollama` | `SQC_OLLAMA_VOLUME` in `.env` |
| Terminology server data | Compose `validation_blaze` volume | Stable Compose project name |

All services and downloads are explicit. [First setup](../README.md#1-first-setup) · [Source choices](lookups.md) · [Input mapping](../examples/lab/README.md).
