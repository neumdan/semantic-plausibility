# Setup and operation

The Python runner performs one review and exits. Ollama is an optional local server. Inputs/configuration are mounted read-only; reports go to `output/`.

## Podman

Install Podman and `podman-compose` (macOS: `brew install podman podman-compose`). Start the configured machine on macOS/Windows. From the repository:

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p local output
export PODMAN_COMPOSE_PROVIDER=podman-compose
SQC_CONFIG_FILE=pipeline.toml podman compose run --rm --build runner
```

Select `SQC_CONFIG_FILE=pipeline.toml` in `.env` for later commands. Explicit provider selection avoids a stale Docker Desktop credential-helper configuration. [Podman provider documentation](https://docs.podman.io/en/latest/markdown/podman-compose.1.html).

Configure input/columns in `[input]` and real endpoints in `[lookups]`. The example reads `examples/laboratory.csv` and contacts NLM UCUM / the public FHIR terminology test server. Use your authenticated/site server for routine studies. No credentials are needed by the public example.

Open `output/laboratory-review.html`. For repeat runs choose a new `[output].path`; only an explicit `overwrite=true` replaces reports. Configuration changes do not require rebuilding. Source/package changes do.

## uv

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env plausibility batch --config pipeline.toml
```

Add `--extra postgres` to `uv sync` for direct PostgreSQL access. Secrets belong in `.env` or ignored mounted `local/secrets/` files, referenced by name/path in TOML.

## Ollama

The model is already defined in the example TOML; research initially stays off. Keep `LOCAL_MODEL_BASE_URL=http://ollama:11434/v1` in `.env` for Compose. Allocate sufficient Podman VM memory for the model (start with 8 GiB for the 3B example). A 2 GiB VM is insufficient; change its memory while stopped, then start it again.

```sh
export PODMAN_COMPOSE_PROVIDER=podman-compose
export SQC_CONFIG_FILE=pipeline.toml
podman compose --profile research up -d ollama
podman compose --profile research run --rm model-init
podman compose --profile research run --rm catalog-init
```

`model-init` downloads the selected model only if absent. `catalog-init` retrieves the real official UCUM definitions/common examples. A finite catalogue does not enumerate every valid UCUM expression; NLM remains the syntax/conversion authority.

Now set `research.enabled=true` in `pipeline.toml` and run:

```sh
podman compose --profile research run --rm runner
```

`scope="lookup"` extracts quoted unit/terminology evidence; `scope="studies"` also searches PubMed. Change `output.path` for this new review. [Research behaviour](research.md). Stop the optional server with `podman compose --profile research down`.

On macOS, native Ollama can use Metal: start it yourself and set `LOCAL_MODEL_BASE_URL=http://host.containers.internal:11434/v1` for the container, or `http://127.0.0.1:11434/v1` for uv. LM Studio works with `runtime="external"`, its served model ID and local `/v1` URL; model provisioning is Ollama-specific.

## Disconnected review

Only actual responses captured by a connected review can be reused as a lookup pack. Enable `output.lookup_pack="output/lookups.json"` in that connected run. On the disconnected recipient, set `input.snapshot` to that pack and `network.mode="offline"`; absent codes/units remain unverified. Preserved source/edition and report hashes support reproducibility; they are not an authenticity signature.

Build/load an image for the recipient's architecture, then enforce runtime isolation:

```sh
podman compose -f compose.yaml -f compose.offline.yaml run --rm runner
```

Installation, model provisioning and catalogue/terminology downloads need connectivity or transferred artifacts. Hospital exports stay local; remote terminology requests contain codes/units, and optional research contains terminology context, not patient values/IDs.
