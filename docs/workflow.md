# Processing path

## Configure, then run

1. Copy `pipeline.example.toml` to `pipeline.toml` and `.env.example` to `.env`.
2. Select the laboratory source below. Put exported data under `../input/` or `local/`.
3. Map each source column once to a semantic role. Declare source systems and editions as supplied. Use `input.defaults` for facts that apply to every row, such as the site's local code-system URI.
4. Select sources by ID from `catalog.csv`; put manual downloads under `local/terminologies/`. Set input granularity, output mode and target profile. Run `profile-init` once to acquire the configured real profile/bindings under `local/profiles/`.
5. Set `validation.enabled` for complete FHIR resources and prepare the local validator when selected.
6. Run `config-check`; add `--connections` to probe selected services. Run `check` into a fresh output path.
7. For laboratory proposals, run A2 or A3 from that checked report. Review findings and source applicability, then import clinician decisions where appropriate.

Start with the [CSV / FHIR / DB input manual](../examples/lab/README.md). Commands for Python, Docker and Podman are in the [setup guide](shipped-stack.md). All paths resolve relative to the selected TOML. The root Compose configuration is mounted as `/app/pipeline.toml`.

## Source → selected input → common representation → output

| Source | Configure/read | Intermediate representation | Output |
| --- | --- | --- | --- |
| LIS/other CSV | `type="csv"`, `path`, delimiter, `[input.columns]` | One mapped value per cell; code/name, value or summary, unit, reference context | Item or distinct CSV + HTML |
| SQLite export | `type="sqlite"`, file path, `database.DB_TABLE`, mapped columns | Same column adapter as CSV; read-only query | Same item/distinct report |
| PostgreSQL / CDS Hub view | `type="postgres"`, database connection, schema/table, mapped columns | Read-only streamed selection; a view selects joins/cohort upstream | Same item/distinct report |
| DB column containing FHIR JSON/JSONB | Map `resource_json` to that column; individual granularity | Decode the complete resource and check its semantic fields | Item/distinct semantic report; export JSON/NDJSON for full-document validation |
| FHIR JSON/Bundle/NDJSON | `type="fhir_file"`, path; Observation selection | Native fields, every coding/range, exported references | Optional validation outcomes, then item/distinct report |
| FHIR server | Select/export with TORCH/CQL or the site's export process; configure the resulting file | Same native FHIR path | Same report plus export provenance |
| Full FHIR document in CSV | Map only `resource_json`; individual granularity | Native FHIR document reader | Optional validation, then item/distinct report |

For DB scalar columns, a mapping such as `code="test_id"`, `value="numeric_result"`, `unit_code="unit_code"` selects exactly those columns. For JSON/JSONB, `resource_json="resource"` selects the complete document. A database view is the explicit place for joins and extraction from vendor-specific nested values.

## Laboratory set and checks

Keep code + system/edition + name + specimen/method + value/comparator or statistics + unit/system + all reference qualifiers together. A1 checks terminology membership, name/axes, UCUM syntax, code–unit fit, supported conversions and numeric/reference consistency. A2/A3 add sourced proposals. The existing medication technical adapter is retained for version 2; [current scope](../README.md#scope).

## Method → input → decision → output

| Stage | Input | Work | Output |
| --- | --- | --- | --- |
| Configuration/preflight | TOML, column headers, profile files, secrets by reference | Validate roles, files, switches, editions and selected adapters | Readiness report; clear errors before analysis |
| Optional FHIR validation | Complete exported resources + pinned IG | Local Matchbox/HL7 validator; retain OperationOutcomes | Separate validation state before grouping |
| Normalize | Mapped rows or native FHIR | Preserve all selected semantic context, numeric types, units and IDs | Internal laboratory set |
| Represent | Set + input frequencies + `output.mode` | Keep records, or group by complete semantics and pool eligible statistics | Stable tuple ID, counts, source-value statistics |
| A1 `check` | Set + profile + configured terminology | Source-code meaning and target-system fit, then cross-field consistency | Findings, source credibility, transformation proposals, evidence/audit |
| A2 `research-evidence --scope lookup` | Original grouped A1 CSV and sidecars | Retrieve candidates/pages; local model proposes; verify against real sources | Sourced code/name/unit proposals |
| A3 `research-evidence --scope studies` | Original grouped A1 CSV and sidecars | A2 lookup plus screened Europe PMC/PubMed; local extraction; quote/analyte/number/unit guards | Sourced measurements, statistics and interval candidates; `.studies.csv` |
| Review/reuse | Original report + clinician review copy | Recheck target code/unit/factor and scoped approval | Versioned local registry and approved-ready count on later checks |

The report keeps field validity, whole-set fit, full-resource conformance and expert approval separately. Example: `140 g/L` can be converted with a verified factor `0.1` to `14 g/dL`; compatible reference bounds and SD change by the same factor. A published interval is a sourced comparison candidate whose population and method need review.

## Individual values or grouped statistics

```toml
[input]
type = "csv"
path = "../input/laboratory.csv"
granularity = "individual"

[output]
mode = "distinct"
path = "output/review.csv"
```

Map `value` for individual measurements. `output.mode="distinct"` combines equivalent records and computes count, mean, sample SD/SE, observed min/max, median and quartiles before A1 and evidence research. For separate record inspection, map `id` and select `"items"`; use a new grouped check report for A2/A3. Codes/editions, names, unit codes/systems, reference qualifiers, specimen, method, comparator and status guard grouping.

For supplied aggregates, set `input.granularity="distinct"`. Map a genuine frequency using `count`, or leave it unmapped when unknown. Map either exact `value` or `value_mean`, `value_sd`, `value_min`, `value_max`; each statistic is numeric and shares the row's `unit_code`. Mean/SD pooling uses disjoint source groups and sample SD (`n−1`). Declare that assumption when preparing the export.

For optional A1 inspection of exact code–value sets, add (use the default context grouping for A2/A3):

```toml
[aggregation]
engine = "duckdb"
group_by = ["code", "code_system", "unit", "unit_system", "reference_range", "value"]
```

## Profile and terminology

Use `specification.STRUCTURE_DEFINITION`, `VALUE_SETS`, `IG_PACKAGE`, `IG_VERSION` and `IG_CANONICAL` for the selected target. The imported profile supplies system/binding rules. The source-column map supplies the data.

Use `lookups.NAME.role="source"` for a site's LIS dictionary, `"primary"` for target terminology and `"optional"` for corroboration/labels. `terminology.strategy="fallback"` uses local evidence first; `"all"` records cross-checks from every applicable enabled source. Configure system URIs and either an API endpoint with credential references or an installed file path. [Origins and setup](lookups.md).

## Containers and data flow

![Architecture](architecture.svg)

The runner is a one-shot Python job. DuckDB/SQLite are embedded libraries. Ollama serves local inference; Matchbox and Blaze serve local FHIR validation/terminology when selected. Inputs and configuration are read-only mounts, reports are writable, and model/terminology volumes persist. [Exact service commands](shipped-stack.md).

![Decision flow](decision-flow.svg)
