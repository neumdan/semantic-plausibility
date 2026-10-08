# Laboratory Semantic Review

A local Python library for **laboratory export → distinct semantic sets → checks and review proposals**. It checks code/system, unit/system, code–unit fit, numeric values and reference ranges. Source data stays unchanged.

## Run the example

Install [uv](https://docs.astral.sh/uv/), then from this folder:

```sh
uv sync --locked --no-editable
uv run --no-sync plausibility batch --config pipeline.example.toml
```

Open **`output/laboratory-export-review.html`**; use its CSV for expert review. The synthetic offline example has **8 input rows → 7 groups**, representing 84 occurrences: 2 passed, 1 suggestion, 3 unclear, 1 error. [See what each case means](docs/studies.md).

The runner exits after writing its reports. No model is needed. Installation needs internet or prepared dependencies. For another run, change `output.path`; existing reports are protected.

**Podman alternative:** copy `.env.example` to `.env`, create `local/` and `output/`, then run `podman compose run --rm --build runner`. [Container, disconnected setup and secrets](docs/shipped-stack.md).

## Use your data

1. Copy `pipeline.example.toml` to `pipeline.toml`.
2. Set the source, field mapping and `individual`/`distinct` granularity.
3. Replace the **synthetic** reference pack and ConceptMap with your reviewed evidence, or remove them and explicitly configure lookup sources. Missing evidence stays unverified.
4. Select the deployed FHIR R4/profile rules and an output filename.
5. Run `uv run --no-sync plausibility batch --config pipeline.toml`. Add `--env-file .env` before `plausibility` when using secrets.

| Input | Selection |
| --- | --- |
| LIS / database CSV | `type="csv"`; map your headers |
| FHIR R4 Bundle or NDJSON | `type="fhir_file"`; native Observation fields |
| LIS / CDS Hub PostgreSQL | `type="postgres"`; approved read-only view; optional `postgres` package extra |
| SQLite export | `type="sqlite"`; map table and columns |

FHIR servers are exported upstream using TORCH or supported CQL. [Source configuration](docs/configuration.md) · [Field and output contract](docs/data-contract.md).

## Read the result

| Status | Meaning |
| --- | --- |
| `passed` | Assessed checks are supported |
| `suggestion` | A documented mapping or transformation needs expert approval |
| `unclear` | Evidence, context or a unique interpretation is missing |
| `error` | A deterministic inconsistency was found |

An out-of-reference value is reported separately from coding validity. `mii_numeric_set_ready` measures verified LOINC–UCUM fit and numeric value/reference completeness; it is **not clinical approval or full MII conformance**. Physicians review proposals in a separate CSV copy.

## Use the library

```python
from semantic_plausibility import run_batch, verify_audit

result = run_batch("pipeline.toml")
print(result.csv, result.report, result.statuses)
print(verify_audit(result.log))
```

Python 3.14, DuckDB 1.5.6; optional psycopg 3.3.6. Versions are pinned in `uv.lock` and recorded per run. Default execution is offline. Research uses an explicitly configured local model and is opt-in.

[Compact visual guide](docs/index.html) · [Optional research](docs/research.md) · [MII/FDPG scope](docs/mii-quality.md) · [Code, audit and development](docs/implementation.md)

Apache-2.0. Terminology and model licences remain separate. Supported configuration: version 3; laboratory Observation only.

## Repository layout

```text
src/semantic_plausibility/  library and CLI
tests/                     regressions and synthetic fixtures
examples/                  inputs, configurations and pinned profile
docs/                      guide, diagrams and synthetic reports
deploy/                    container build
tools/                     documentation and release builders
.github/workflows/         tests and optional GitHub Pages
```

## Publish your repository

Create an empty GitHub repository, then run from this folder:

```sh
git add .
git commit -m "Initial laboratory semantic review library"
git remote add origin https://github.com/YOUR_ACCOUNT/semantic-plausibility.git
git push -u origin main
```

Replace `YOUR_ACCOUNT` with your account or organisation. Tests run on pushes and pull requests. For documentation, select **GitHub Actions** under Settings → Pages, then run **Publish documentation** in Actions.
