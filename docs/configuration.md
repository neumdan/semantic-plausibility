# Configure a laboratory review

Copy `pipeline.example.toml` to `pipeline.toml`. Paths are relative to that file. Configure only the source and features you use; omitted lookup/model features stay off.

| Section | Defines |
| --- | --- |
| `input` | CSV, FHIR file, PostgreSQL or SQLite; location and input granularity |
| `input.columns` | Semantic role → actual CSV header / database column |
| `input.snapshot` | Local reference evidence; the supplied pack is synthetic |
| `specification` | FHIR R4 and the selected deployed profile/policy |
| `aggregation` | Optional exact-value grouping; other semantic qualifiers are always guarded |
| `network` / `lookups` | Offline mode or explicit terminology APIs/lists |
| `quality` | Optional reviewed ConceptMap; mappings remain proposals |
| `study` | Matched cohort/period declarations for source comparisons |
| `output` | Report filename and `distinct`/`items` mode |

Keep declared systems, versions, units and every reference interval. Defaults may express documented source facts; they must not invent LOINC or UCUM declarations. See the [field contract](data-contract.md) for available roles.

## CSV and aggregation

Set `type="csv"`, map your headers, and choose:

- `granularity="individual"`: one measurement per row; no aggregate count/bounds.
- `granularity="distinct"`: already grouped input; use real `count`, or omit it when unknown.
- `output.mode="distinct"`: summarize values per guarded code–unit–reference set.
- `output.mode="items"`: retain source IDs/locators; requires individual input.

To keep exact point values as separate tuples, add:

```toml
[aggregation]
group_by = ["code", "code_system", "unit", "unit_system", "reference_range", "value"]
```

Min/max-only inputs cannot use exact-value grouping or supply mean/SE/median/IQR.

## FHIR Bundle / NDJSON

Use [examples/lab/fhir.toml](../examples/lab/fhir.toml), with `input.type="fhir_file"` and `granularity="individual"`. Set `input.path` to an R4 Bundle, NDJSON file or NDJSON directory. Native reading preserves alternative codings, components and all reference ranges; remove CSV column/default tables. Export one current version per Observation ID.

Use TORCH/CRTDL or the server's supported CQL upstream. [LaboratorySelection.cql](../examples/studies/LaboratorySelection.cql) illustrates selection; it is not a universal export command. A count-only MeasureReport is insufficient. LIS APIs similarly produce a supported export. [MII/FDPG placement](mii-quality.md).

## PostgreSQL or SQLite

Prefer a hospital CSV export when the database is disconnected. [distinct-export.postgres.sql](../examples/studies/distinct-export.postgres.sql) illustrates grouping against a **site-defined** view; no CDS Hub schema is assumed.

For a direct PostgreSQL read, install `uv sync --locked --no-editable --extra postgres`. Keep `input.columns`, replace the input type, and add:

```toml
[input]
type = "postgres"
origin = "cds_hub"                 # or lis
snapshot = "local/reference-pack.json"
granularity = "distinct"

[database]
DB_SCHEMA = "semantic_review"
DB_TABLE = "lab"
DB_DSN_ENV = "LAB_DB_DSN"
```

Put the connection string in ignored `.env`: `LAB_DB_DSN=postgresql://USER:PASSWORD@HOST:5432/DATABASE?sslmode=verify-full`. Configure a trusted CA and a SELECT-only account. The adapter validates identifiers and reads in a read-only transaction. Do not paste real credentials into documentation/reports.

Alternatively supply `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER_ENV`, `DB_PASSWORD_ENV` or `DB_PASSWORD_FILE`, and `DB_SSLMODE`. An explicit `PATH_TO_DB_CONFIG_TOML` reuses flat CDS Toolchain `DB_*` fields; `DB_USER_KEY`/`DB_PASSWORD_KEY` select its credential keys. Choose one connection method.

SQLite uses `type="sqlite"`, `input.path="local/lab.sqlite"`, and `database.DB_TABLE="lab"`; it opens read-only. DuckDB remains the grouping engine. The [study demo](studies.md) creates a runnable synthetic SQLite example.

## Profile and evidence

International fallback is FHIR R4. For installed MII/NUM profiles, set `specification.STRUCTURE_DEFINITION` to a resolved R4 Observation snapshot and record `IG_PACKAGE`, `IG_VERSION`, `IG_CANONICAL`. Alternatively use `PROFILE_FILE` for a reviewed selected-element policy; choose one. [examples/lab/mii-lis.toml](../examples/lab/mii-lis.toml) demonstrates the pinned MII snapshot. This imports selected semantic rules, not full IG validation.

`network.mode="offline"` uses local evidence only. For remote lookups set `"lookups"` and explicitly enable each configured source. [Terminology configuration](lookups.md) · [Optional local research](research.md).

```sh
uv run --no-sync plausibility config-check --config pipeline.toml
uv run --no-sync --env-file .env plausibility batch --config pipeline.toml
```

`config-check` checks setup; `--connections` additionally requests connection probes. The batch checks the actual input contract. Existing outputs require a new filename or explicit `output.overwrite=true`.
