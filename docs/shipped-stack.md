# Setup and operation

The runner is a **one-off local Python process**: read an export, write reports, exit. Ollama is an optional local research server. [Source/profile configuration](configuration.md).

## uv

```sh
uv sync --locked --no-editable
uv run --no-sync plausibility batch --config pipeline.example.toml
```

For PostgreSQL add `--extra postgres` to `uv sync`. For your configuration use `--config pipeline.toml`; add `--env-file .env` before `plausibility` when resolving secrets. Review `output/*.html` and the full CSV. Keep the original artifacts; physician edits belong in a separate CSV copy.

## Podman Compose

Install Podman and a Compose provider. On macOS/Windows start the configured machine. From the project folder:

```sh
cp .env.example .env
mkdir -p local output
podman compose run --rm --build runner
```

Set `SQC_CONFIG_FILE=pipeline.toml` in `.env` to use your configuration. Inside the runner, inputs/configuration are read-only under `/app`; `output/` is writable. Paths in TOML are relative to its mounted location. The default starts only the runner.

For an already built image with runtime networking disabled:

```sh
podman compose -f compose.yaml -f compose.offline.yaml run --rm runner
```

## Secrets and local files

TOML contains `*_ENV` / `*_FILE` references. Values belong in ignored `.env` or mounted `local/secrets/` files. Compose reads `.env`; uv reads it only when requested. Put private exports/reference packs under ignored `local/`. Installation, catalogue downloads and model provisioning require connectivity or prepared artifacts; an offline review does not.

## Optional local models

First configure the model and research sections described in [research.md](research.md). For Compose:

```sh
podman compose --profile research up -d ollama
podman compose --profile research run --rm model-init
podman compose --profile research run --rm catalog-init
podman compose --profile research run --rm runner model-test --config /app/pipeline.toml
podman compose --profile research run --rm runner
```

`model-init` provisions the selected Ollama model; `catalog-init` downloads UCUM definitions/common examples to the configured `research.unit_catalog`. Catalogue examples cannot enumerate every valid UCUM expression. Model tests use synthetic extraction cases, not patient data.

With native Ollama use `http://127.0.0.1:11434/v1` for uv, or `http://host.containers.internal:11434/v1` inside Podman. For a native endpoint run the helper through `runner model-init --config /app/pipeline.toml` instead of the service-specific `model-init` dependency. Set a suitable VM/model memory budget; native Ollama on macOS can use Metal.

LM Studio: load the model, enable its local API, set `runtime="external"`, `base_url="http://127.0.0.1:1234/v1"` and its exact served model ID. Use host.containers.internal from Podman. `model-test` works; `model-init` provisions Ollama only. Only local/LAN model endpoints are accepted. The optional GPU overlay is for a configured Linux GPU/CDI installation.

Stop Compose services with `podman compose --profile research down`; the model volume persists.

## Ship into a disconnected site

Build while connected for the recipient's architecture:

```sh
uv build --wheel
uv run --no-sync python tools/build_release.py
podman build --build-arg INSTALL_POSTGRES=1 -f deploy/Dockerfile \
  -t semantic-plausibility-stack:0.9.1 .
podman save --format oci-archive -o reviewer.oci.tar semantic-plausibility-stack:0.9.1
```

Transfer the source runner/image and reviewed configuration/reference artifacts through the approved process. Load with `podman load -i reviewer.oci.tar`, then run Compose without `--build`. ARM64 and AMD64 need their own built/tested image. Disconnected uv installation additionally needs Python and platform-specific dependency wheels/cache; the application wheel alone is insufficient.
