# Laboratory input: one configuration

**Edit `pipeline.toml` in the repository root for every source.** Keep its output, profile, terminology and model settings. Replace its existing `[input]` and `[input.columns]` tables with one recipe below; remove old `[input.defaults]`, `[input.context]` and `[input.elements]` if they no longer describe the source. Add `[database]` only for a database. Edit existing tables in place.

First setup: [Podman / Docker / Python](../../docs/shipped-stack.md). Every example uses illustrative input and real terminology services. Keep hospital exports in `../input/` or `local/`; secrets go in `.env`.

| Your source | Input recipe | Evidence preparation |
| --- | --- | --- |
| One measurement per CSV row | [Individual CSV](#individual-csv) | Aggregate equivalent measurements |
| Frequencies/statistics already exported | [Aggregated CSV](#aggregated-csv) | Preserve counts and pool compatible, disjoint summaries |
| R4 resource, Bundle or NDJSON | [FHIR](#fhir) | Read native fields, then aggregate |
| SQLite file | [SQLite](#sqlite) | Read the selected table and mapped columns |
| PostgreSQL / CDS Hub | [PostgreSQL](#postgresql) | Read the selected view/table and mapped columns |

The source adapter always feeds the same laboratory set: **code + system + name + specimen/method + value or statistics + unit/system + reference context**. The target profile supplies field requirements; your column map supplies the actual data. Select `output.mode="distinct"` for the evidence pipeline; `"items"` retains individual IDs for lookup inspection.

## Individual CSV

Replace the input tables with this complete example:

```toml
[input]
type = "csv"
delimiter = "," # Match your header: comma, semicolon (";"), or tab ("\t").
path = "examples/lab/individual.csv"
origin = "lis"
source_namespace = "example-laboratory"
granularity = "individual"

[input.columns]
id = "id"
code = "code"
code_system = "code_system"
analyte_name = "name"
value = "value"
unit = "unit"
unit_code = "unit_code"
unit_system = "unit_system"
reference_low = "reference_low"
reference_high = "reference_high"
specimen = "specimen"
status = "status"
```

**Expected arithmetic:** three Hb values, 12, 14 and 16 g/dL, become one group: n=3, mean=14, sample SD=2, min=12, max=16. The source name, code, unit, specimen and reference context stay together. Different context creates separate groups. The example's reference-bound units are inferred from its measurement unit and reported as an assumption; map `reference_unit` and `reference_unit_system` when supplied.

For your export, change `path` and the **right-hand column names**. Keep the source code-system and unit-system values. Map available context and source IDs using the [field reference](../../docs/data-contract.md#minimum-export).

## Aggregated CSV

Replace the input tables with:

```toml
[input]
type = "csv"
delimiter = "," # Match your header: comma, semicolon (";"), or tab ("\t").
path = "examples/lab/aggregated.csv"
origin = "lis"
source_namespace = "example-laboratory"
granularity = "distinct"

[input.columns]
code = "code"
code_system = "code_system"
analyte_name = "name"
unit = "unit"
unit_code = "unit_code"
unit_system = "unit_system"
count = "count"
value_mean = "mean"
value_sd = "sd"
value_min = "min"
value_max = "max" # All statistics are numbers in the row's common unit_code.
reference_low = "reference_low"
reference_high = "reference_high"
specimen = "specimen"
status = "status"
```

**Typo exercise:** the final row deliberately uses `g/dLL`. The real UCUM service rejects it; AI lookup can propose independently validated `g/dL`. Review the intended unit before changing values or bounds. The source row stays unchanged.

**Expected:** six input rows become five groups. Two compatible Hb summaries combine to n=30 and mean=13.3333… g/dL. `sd` means sample SD (n−1). Pool summaries only when their underlying measurements are disjoint. All statistics are numbers in the common `unit_code`; no separate mean/SD/min/max unit columns are needed. Omit unavailable statistic/count mappings. For exact-value frequency rows, map `value` and `count` instead of summary statistics; the starter `examples/laboratory.csv` demonstrates this (8 rows → 7 groups, 84 occurrences).

## FHIR

Replace the input tables with this block; native FHIR provides the field mapping:

```toml
[input]
type = "fhir_file"
path = "examples/fhir/laboratory-valid.bundle.json"
origin = "fhir"
source_namespace = "example-laboratory"
granularity = "individual"
```

**Expected:** one Observation is checked; its Patient and Organization are retained as document context. `Observation.code.coding`, `code.text`, `valueQuantity`, `referenceRange`, `status`, `method`, components and referenced Specimen information supply the semantic set. Set `path="examples/lab/observation.json"` for a single resource, `"examples/lab/observations.ndjson"` for NDJSON, or `"examples/laboratory.bundle.json"` for the 84-measurement Bundle.

For a FHIR server, export the selected current resources using TORCH/CQL or your site's export process, retain referenced context and set `path` to that export. [CQL example](../studies/LaboratorySelection.cql). Enable `validation.enabled=true` and follow the [local validator setup](../../docs/validation.md) to validate complete resources against the pinned MII guide before grouping.

For complete FHIR JSON stored in CSV, use this input block instead:

```toml
[input]
type = "csv"
delimiter = "," # Match your header: comma, semicolon (";"), or tab ("\t").
path = "examples/laboratory-fhir.csv"
origin = "fhir"
source_namespace = "example-laboratory"
granularity = "individual"

[input.columns]
resource_json = "resource_json"
```

The CSV cell contains a complete document. Its original resource structure and reference context reach the same validator and semantic adapter.

## SQLite

Prepare the illustrative database once:

```sh
podman-compose --profile examples run --rm prepare-examples
# Python alternative
uv run --no-sync python tools/prepare-lab-examples.py
```

Use the **individual CSV column mapping** above. Replace `[input]` and add `[database]`:

```toml
[input]
type = "sqlite"
path = "local/examples/lab.sqlite"
origin = "lis"
source_namespace = "example-laboratory"
granularity = "individual"

[database]
DB_TABLE = "laboratory_values"
```

The read-only query produces the same 3 → 1 result as the individual CSV. For aggregate input, select `laboratory_summary`, `granularity="distinct"` and the aggregate column map. This produces the same 6 → 5 result as the aggregate CSV.

## PostgreSQL

For the included local database exercise, set `EXAMPLE_DB_USER` and `EXAMPLE_DB_PASSWORD` in `.env`, then start:

```sh
podman-compose --profile laboratory-inputs up -d example-postgres
```

Use the **aggregate CSV column mapping** above. Replace `[input]` and add `[database]`:

```toml
[input]
type = "postgres"
origin = "cds_hub"
source_namespace = "example-laboratory"
granularity = "distinct"

[database]
DB_HOST = "example-postgres"
DB_PORT = 5432
DB_NAME = "laboratory"
DB_USER_ENV = "EXAMPLE_DB_USER"
DB_PASSWORD_ENV = "EXAMPLE_DB_PASSWORD"
DB_SCHEMA = "public"
DB_TABLE = "laboratory_summary"
DB_SSLMODE = "disable" # Only the isolated local example; use verify-full for the hospital connection.
```

Run the normal check with compose.yaml, e.g. `podman-compose run --rm run-plausibility-check check --out output/postgres-01.csv`. Expected: 6 → 5 groups, matching the aggregate CSV. Stop the exercise with `stop example-postgres`; its volume persists.

For a hospital/CDS Hub connection, select a read-only account, host/database, schema, table/view and actual columns. Use `granularity="individual"` for raw measurements. A curated view handles joins/current-version selection. For JSONB Observations, map only `resource_json="resource"` and use individual granularity. For full-document validation, export the resources to FHIR files. [DSN, TLS and secret-file settings](../../docs/configuration.md#postgresql-and-sqlite).

## Run every source the same way

```sh
podman-compose run --rm run-plausibility-check config-check
podman-compose run --rm run-plausibility-check check --out output/lookup.csv
```

Open `output/lookup.html`. Change the output filename on the next run. Docker uses `docker compose`. Python uses `uv run --no-sync --env-file .env run-plausibility-check` in place of the Compose prefix (install `--extra postgres` for PostgreSQL).

Then use the **same configuration** and grouped report for `research-evidence --scope lookup` or `--scope studies`. [Ollama setup and exact commands](../../README.md#local-ai-select-download-run). Inputs and values stay local; configured APIs provide terminology and study evidence. A missing source remains visibly unverified.
