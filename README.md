# Laboratory Semantic Review

A local Python library for **laboratory export → grouped sets → real terminology checks → review proposals**. It checks code/system, unit/system, code–unit fit, numbers and supplied reference ranges. Source data stays unchanged.

## Where everything is

| What | Where |
| --- | --- |
| Example input | `examples/laboratory.csv` — 8 illustrative measurement rows, no clinical reference recommendations |
| Your input | Put your export under ignored `local/`; select it in `[input]` |
| Configuration | Copy `pipeline.example.toml` to ignored `pipeline.toml` |
| Credentials / model URL | Ignored `.env`; template `.env.example` |
| Checks | `src/semantic_plausibility/`; [process and module map](docs/implementation.md) |
| Output | `output/laboratory-review.csv`, HTML, evidence, summary and audit log |

**Every default lookup contacts a real service:** NLM UCUM and the public FHIR terminology test server at `https://tx.fhir.org/r4`. Actual source URLs and returned LOINC versions are recorded. For routine studies configure your own service or the authenticated [official LOINC API](https://loinc.org/kb/api/auth). An outage or missing evidence stays unverified. No fabricated lookup data is supplied.

## Run with Podman

Install Podman and `podman-compose`; start the Podman machine on macOS/Windows. From this folder:

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p local output
export PODMAN_COMPOSE_PROVIDER=podman-compose
SQC_CONFIG_FILE=pipeline.toml podman compose run --rm --build runner
```

The runner writes reports and exits. No model is used until enabled. Set a new `output.path` for another run; explicit `overwrite=true` replaces that report set.

With Python 3.14 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env plausibility batch --config pipeline.toml
```

## What happens

1. Read the selected CSV, FHIR R4 Bundle/NDJSON, SQLite table or read-only PostgreSQL view.
2. Preserve codes, units, values/bounds, complete reference sets and context. Group in embedded DuckDB; retain real frequencies or individual IDs as configured.
3. Check declared systems/codes, UCUM syntax, LOINC example units, verified conversion factors and numeric/reference consistency.
4. Optionally use a local model to extract quoted unit/range candidates from real catalogues, terminology pages and PubMed abstracts.
5. Write separate findings and proposed transformations for physician review. An out-of-reference value is descriptive, not a patient diagnosis.

`passed`, `suggestion`, `unclear`, `error` describe assessed findings. `mii_numeric_set_ready` is a numeric completeness/terminology proxy, not MII certification or clinical approval.

## Add Ollama

The TOML already defines `ministral-3:3b`; `.env` supplies its local URL. [Start the optional model, download the real UCUM catalogue and enable research](docs/shipped-stack.md#ollama). Model output never establishes code validity or a conversion factor by itself.

[Input/profile configuration](docs/configuration.md) · [Field/output contract](docs/data-contract.md) · [Architecture and decision diagrams](docs/index.html) · [MII quality checks](docs/mii-quality.md) · [Study protocol](docs/studies.md)

```python
from semantic_plausibility import run_batch, verify_audit
result = run_batch("pipeline.toml")
print(result.csv, result.statuses)
print(verify_audit(result.log))
```

Python 3.14; DuckDB 1.5.6; optional psycopg 3.3.6. Dependencies are locked in `uv.lock`. [Implementation and real-service tests](docs/implementation.md).

Apache-2.0; terminology/model licences remain separate. Laboratory Observation only; medication/diagnosis are later work.
