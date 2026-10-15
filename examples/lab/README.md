# Working laboratory inputs

This workflow covers **quantitative laboratory results**, including summary statistics; other uses of FHIR Observation are outside its scope. All input values here are synthetic. All lookup responses come from real configured services. Medication is outside this walkthrough. The source is never rewritten.

## 1. Choose one input

Paths in a TOML are relative to that TOML. Run these recipes in place; when copying one, adjust its paths. `config-check` reports missing files/columns before the check.

| Input / intended check | Configuration | Expected input accounting / report |
| --- | --- | --- |
| Individual CSV: numeric values → group statistics | `individual.toml` | 3 Hb values → 1 set; mean 14, sample SD 2 g/dL; `output/lab-individual.html` |
| Distinct CSV: supplied mean, SD, min/max with units | `aggregated.toml` | 5 rows → 4 sets / 90 source occurrences; `output/lab-summary.html` |
| SQLite view/table: same distinct contract | `sqlite.toml` | Same sets/statistics as aggregate CSV; `output/lab-sqlite.html` |
| PostgreSQL / LIS / CDS Hub view: same contract | `postgres.toml` | Same sets/statistics; `output/lab-postgres.html` |
| One complete FHIR Observation | `single-fhir.toml` | 1 resource; `output/single-fhir.html` |
| FHIR Bundle | `fhir.toml` | Reads `../laboratory.bundle.json`; `output/fhir.review.html` |
| FHIR NDJSON | `ndjson.toml` | Same Observations as Bundle; `output/ndjson.html` |
| Complete FHIR Bundle in a CSV cell + MII validation | `fhir-csv.toml` | Reads `../laboratory-fhir.csv`; `output/laboratory-fhir-review.html` |
| CSV against local Blaze + manually installed BfArM labels | `local-terminology.toml` | Aggregate CSV; `output/lab-local-terminology.html` |

A target IG does not infer vendor column meanings. `lis.toml` / `mii-lis.toml` retain the older minimal CSV recipes. Single FHIR/Bundle/NDJSON examples select base R4; the aggregate and full-validation examples select pinned MII Laboratory 2026.0.3. Change `[specification]` deliberately for your target.

## 2. Run CSV or FHIR

After the [root setup](../../README.md#first-test--podman), replace the recipe name as needed:

```sh
podman-compose run --rm run-plausibility-check config-check --config /app/examples/lab/aggregated.toml
podman-compose run --rm run-plausibility-check check --config /app/examples/lab/aggregated.toml
```

Docker: replace `podman-compose` with `docker compose`. Python:

```sh
uv run --no-sync --env-file .env run-plausibility-check check --config examples/lab/aggregated.toml
```

Use a fresh output for repetitions, for example `--out /app/output/lab-summary-repeat.csv` in a container. For individual IDs, copy `individual.toml` and set `output.mode="items"`; aggregated rows cannot recover original IDs.

**What is checked:** source membership/syntax, numeric data, LOINC axes/example-unit fit, reference-bound consistency and verified conversions. Being outside a local reference interval does not itself mean semantic error. Formal FHIR validation is only for complete resources; [prepare Matchbox first](../../docs/index.html#validation) for `fhir-csv.toml`.

## 3. Understand the aggregate example

`aggregated.csv` has mean, sample SD, min, max and a unit column for each, plus count and reference context. `[input.columns]` maps them to `value_mean`, `value_sd`, `value_min`, `value_max` and the corresponding `*_unit` roles. If a statistic's unit column is omitted, the declared measurement unit is used. Supplied mixed units are rejected: normalise the export first, preserving provenance.

Two Hb rows share the exact same semantic context: n=10, mean=14, SD=1 and n=20, mean=13, SD=2 g/dL. They represent **disjoint** groups and pool to n=30, mean=13.3333, sample SD≈1.77790 g/dL; observed bounds are 9–17 g/dL. Their local reference remains 12–16 g/dL. Neither pooled statistics nor observed extremes become a new reference interval.

The g/L Hb row remains a separate source set. If NLM verifies factor 0.1 to g/dL, its mean 140, SD 12 and min/max 120–160 become mean 14, SD 1.2 and min/max 12–16 g/dL in the **proposal**, alongside transformed reference bounds. The report retains the original values. MCHC `786-4` is a different analyte/property context from Hb `718-7`; a shared unit does not merge them.

For multiple summary rows, weighted means require counts; sample SD additionally requires enough within-group information. Missing counts/SD remain missing. SD is not SE. Median/IQR are only calculated from individual values; they are never reconstructed from mean/SD. Overlapping cohorts must not be pooled.

## 4. SQLite

Create the small synthetic source locally; this does not download terminology:

```sh
uv run --no-sync python tools/prepare-lab-examples.py
podman-compose run --rm run-plausibility-check check --config /app/examples/lab/sqlite.toml
```

The helper reuses only an identical existing example database; it refuses to overwrite different data. The checker opens SQLite read-only. For a real export, change its path, table and column map.

## 5. PostgreSQL (also the LIS / CDS Hub pattern)

Add `EXAMPLE_DB_USER` and a chosen `EXAMPLE_DB_PASSWORD` to your ignored `.env`. Then:

```sh
podman-compose -f compose.yaml -f compose.examples.yaml --profile laboratory-inputs up -d example-postgres
podman-compose -f compose.yaml -f compose.examples.yaml --profile laboratory-inputs run --rm run-plausibility-check config-check --connections --config /app/examples/lab/postgres.toml
podman-compose -f compose.yaml -f compose.examples.yaml --profile laboratory-inputs run --rm run-plausibility-check check --config /app/examples/lab/postgres.toml
podman-compose -f compose.yaml -f compose.examples.yaml --profile laboratory-inputs stop example-postgres
```

The separate example database has no host port and seeds the supplied aggregate CSV on first start. Its named volume persists. Credentials are required even for this local example. PostgreSQL support is included in the container image. Production: use an IT-approved read-only role/view, actual host/schema/table/column mapping and TLS `verify-full`; the isolated demo alone uses `disable`. Do not reuse hospital credentials for the demo. No CDS Hub schema or joins are guessed.

## 6. FHIR server / TORCH / CQL exports

Direct server querying is upstream. A working local representation is `fhir.toml` or `ndjson.toml`: place the authorised export next to the repo and change `[input].path`. Retain complete Observations, resource IDs, every reference interval and context. TORCH selection/CRTDL and CQL depend on the site's data and permissions; this project does not provide a universal server query. [MII placement](../../docs/mii-quality.md).

## 7. Add AI/evidence to the aggregate report

Provision Ollama and the UCUM catalogue as in the root README. Then:

```sh
podman-compose --profile research run --rm research-evidence research-evidence --config /app/examples/lab/aggregated.toml --scope lookup --out /app/output/lab-summary-ai.csv
podman-compose --profile research run --rm research-evidence research-evidence --config /app/examples/lab/aggregated.toml --scope studies --out /app/output/lab-summary-evidence.csv
```

Both use the original checked `output/lab-summary.csv` and its sidecars. Inspect `lab-summary-evidence.html`, `.studies.csv` and `.trace.jsonl`. Published mean/SD/min/max or intervals need an exact quotation/unit; unsupported extracts are rejected. Comparability of population, method and specimen remains a clinician task. Local values and reference bounds are not sent to public search/model requests.

## 8. Optional local terminology service

Follow the [manual BfArM download and Blaze instructions](../../docs/index.html#local-server). The recipe requires actual downloaded files at `local/terminologies/bfarm-lab/`. It never substitutes example terms for a missing catalogue. The metadata-only `package-check` announces newer releases without downloading or changing any pinned edition.
