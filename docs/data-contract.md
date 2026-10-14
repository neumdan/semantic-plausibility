# Laboratory input and output contract (0.11.3)

## Minimum export

Select the hospital cohort/time window upstream. Prefer one row per distinct **code + code system + value + unit code/display + unit system + complete reference set + semantic qualifiers**, with its real `count`. Preserve comparators (`<`, `<=`, `>=`, `>`), versions, status, method, specimen and population/age/type qualifiers. Do not retain a random first code/range or discard outliers before review.

Supported inputs: scalar CSV, complete FHIR JSON in a CSV cell, FHIR R4 Bundle/JSON/array/NDJSON, mapped PostgreSQL view, read-only SQLite extract. MedicationRequest/Administration use their separate [product/dose input contract](../examples/medication/README.md); diagnosis is not implemented. LIS vendor APIs and TORCH/CQL run upstream and produce these formats.

| TOML role | Source meaning |
| --- | --- |
| `resource_json` | Full FHIR document in CSV, or Observation JSON in a DB column; alternative to scalar mappings, individual input only |
| `code`, `code_system`, `code_version` | Declared terminology identity and edition; no implicit LOINC assignment |
| `analyte_name` / `code_text`, `code_display` | Source analyte/test name → CodeableConcept.text; optional coding display → Coding.display. Native FHIR retains both |
| `value` | Point value, including genuine zero; finite decimal required for numeric readiness |
| `value_comparator` | Preserve censoring; censored values are not expanded into a numeric distribution |
| `unit`, `unit_code`, `unit_system` | Display label, machine code, canonical system; display is not proof of UCUM |
| `reference_ranges_json` | Full FHIR `referenceRange` array; recommended over flattened bounds |
| `reference_low`, `reference_high`, `reference_unit`, `reference_unit_system` | One simple interval only; full JSON needed for multiple/qualified intervals |
| `specimen` | Resolved LOINC system-axis token (e.g. `Bld`), **not** a raw SNOMED code |
| `method`, `method_json`, `source_test`, `status` | Resolved method axis, native method codings, test label and result status |
| `count` | Positive signed 64-bit integer from the source; omit if unknown |
| `value_min`, `value_max` | Aggregate extrema instead of `value`; ordered finite bounds |
| `source_id` | Optional item provenance; native FHIR ID is retained only in items mode |
| `modifier_extensions_json` | Qualifiers whose effect cannot be ignored; unresolved modifiers block readiness |

Use defaults only for facts established by the source contract. Do not label all LIS units as UCUM or all codes as LOINC merely to satisfy the checker. Numeric strings are allowed in CSV; in native FHIR `Quantity.value` and reference bound values must be JSON numbers. FHIR JSON numbers use decimal parsing; booleans/non-finite literals cannot pass as values. Malformed FHIR numeric encoding is reported as an error.

CSV headers must be unique/nonempty and mapped columns present. Mismatched row width fails the batch with a redacted failure audit. Invalid measurement numbers within an otherwise valid CSV become review findings. A numeric encoding budget (1,024 coefficient digits, at most 1,024 digits before the decimal point and absolute decimal exponent at most 1,024; input text at most 4,096 characters) prevents pathological fixed-point expansion. It is not a biological plausibility threshold. Native FHIR and full-resource CSV use `granularity="individual"` and native elements/`input.elements`; Scalar column/default mappings and a distinct frequency declaration are rejected rather than ignored; full-resource CSV maps only `resource_json`. Export already aggregated frequency sets as mapped CSV/DB rows. Native FHIR exports must contain one current version per Observation ID; duplicate IDs fail rather than silently doubling counts.

## Frequency and aggregation

Supplied code displays/analyte names remain in guarded group identity. The name check adds sourced aliases/candidates, not a normalized replacement. For cross-source comparisons select names consistently; the comparator refuses duplicate canonical signatures instead of silently combining potentially different tests.

- `granularity="individual"`: each measurement counts once. Components remain separate measurements; source records and selected measurement counts are both reported.
- `granularity="distinct"`: supplied `count` weights the distribution. Without it, frequency stays unknown and weighted summaries stay unavailable.
- Bounds-only exports: map `value_min/value_max`, omit `value`; they cannot supply mean/SE/median/IQR.
- `output.mode="items"`: requires individual input; reports original ID/file locator/component path. Distinct mode never reconstructs IDs.

Default grouping excludes point value and combines eligible values per complete semantic identity. Add `value` to `aggregation.group_by` for exact tuples. Guards always retain code versions, all codings/references, status, units, comparator, specimen/method/context, modifiers and measurement path. Different populations or interval sets remain separate.

DuckDB groups staged rows before costly lookups. Weighted quartiles use continuous interpolation over source frequency ranks; sample SD and SE are descriptive. Repeated observations from one patient are not independent research subjects. Censoring, unknown frequencies or extrema-only inputs prevent full distribution statistics. Invalid/missing points are counted separately; eligible statistics do not make the whole group valid.

## Optional export manifest

`input.manifest` points to local JSON. Allowed keys: `exported_rows`, `source_occurrences`, `description`, `exporter`, `exporter_version`, `selection_sha256`, `deduplication_policy`. No patient identifiers or credentials. `exported_rows` is the number of selected Observation source rows, not Bundle entries or component measurements. Known source frequencies are checked against `source_occurrences`. Selection hashes record the exact upstream selection/configuration. Unknown frequency remains unknown.

## Output artifacts

For `output.path="output/review.csv"`:

| Artifact | Purpose |
| --- | --- |
| `review.csv` | Every item/group, separate checks, credibility/readiness, transformation/coding proposals, expert fields |
| `review.html` | Offline overview, ratios, first 2,000 rows and links to full CSV/log |
| `review.summary.json` | Counts, explicit numerators/denominators, versions, hashes, profile provenance and scope |
| `review.evidence.jsonl` | Detailed per-group assessment with original semantic metadata and evidence |
| `review.audit.jsonl` | UTC phases/progress, software, hashes, counters, redacted errors; hash chain |
| `review.fhir-validation.jsonl` when enabled for native FHIR or full-resource CSV input | Full local validator OperationOutcomes, requested profile/packages and source/submitted resource hashes |
| Optional captured pack | Only queried verdicts/terms/verified linear factors for offline reuse; same report directory |

An existing prefix requires `overwrite=true` or a new filename. Concurrent runs with the same output prefix are blocked. Files publish from staged temporary files; the summary is published last as the completion marker. Failed runs retain `.failed.<run-id>.audit.jsonl`, with safe diagnostics and no completed summary. A stale `.run.lock` should be removed only after confirming its owning process is no longer running.

### Columns to read first

The main statuses are `passed`, `suggestion`, `unclear`, `error`; `not_applicable` marks a measurement outside the quantitative laboratory scope.

`status`, `reason`, `code_check`, `unit_check`, `code_unit_fit`, `reference_check`, `value_reference_check`, `lookup_credibility`, `mii_numeric_set_ready`, `occurrence_count`, `value_min/max`, `value_mean/se`, `value_median/q1/q3/iqr`, `suggested_unit`, `transformation_json`, `coding_proposal_json`, `fhir_mapping_proposal_json`.

Names/research: `analyte_name` is supplied; `canonical_name` is the exact lookup display. `suggested_code`/`suggested_name` record a model-selected, independently verified candidate or remain empty on abstention. `suggestion_state`, `laboratory_suggestions_json` retain the shortlist, conflicts, unresolved axes and provenance; `reference_candidates_json` contains quoted candidate intervals with context. Retrieved candidates are never asserted equivalent to the source test.

Formal FHIR: `fhir_validation_state` and `fhir_validation_scope` describe the full input-document check before grouping. States are `passed`, `passed_with_warnings`, `invalid`, `incomplete`, `unavailable`, `disabled` or `not_applicable` for scalar CSV/DB input and direct database JSON. Aggregated rows share the input-level result; this is not a per-group conformance certification. The HTML links the full outcomes and places supporting citations beside each proposal. [Validation setup](validation.md).

Credibility is an evidence category, **not a calibrated probability**: `supported_by_configured_sources`, `partial`, `unverified`, `rejected`. Numerical readiness is defined in the report independently of credibility. `clinical_value_evidence` and `clinical_reference_evidence` remain `not_established` by the numeric core. Grounded research candidates do not change that into clinical approval.

CSV strings are escaped against spreadsheet formulas. Full evidence may contain measured values and semantic metadata; the audit log avoids raw source IDs/values/secrets. Removing IDs is not anonymisation. Nothing is sent back automatically: transfer the reviewed reports only through your institution's approved process.

## Source checks and clinician reuse

`source_namespace` is retained in distinct reports as a stable source/site label, not a patient ID. `source_code_check`, `source_unit_check`, `source_code_unit_fit` and `source_reference_check_json` describe source evidence separately from target-profile checks. `mapping_review_state`, `approved_mapping_ready` and `clinician_mapping_json` describe scoped feedback; original numeric readiness remains unchanged by an un-applied mapping plan.

Review columns and the separate evidence dossier/trace are described in [research and feedback](research.md). The registry is local and versioned; accepted targets/factors are rechecked on reuse. `approved_mapping_ready` counts currently supported approved plans, not full patient-resource validation.

Optional mapped roles `component`, `property`, `time_aspect` and `scale` preserve known source axis descriptors alongside specimen/method. Use resolved descriptors, not inferred aliases. For native FHIR, a context file can supply `source_axes={component,property,time,scale}`; time here is the observation definition (such as Pt or a collection duration), not a patient timestamp. Conflicts with resolved LOINC axes stay under review.

## Complete review projection

Every checked/researched row now contains:

| Field | Meaning |
| --- | --- |
| `source_data_json` | Original codings/name, numeric values/statistics, units, all reference intervals and context; source ID only if retained by item mode |
| `linked_data_json` | Exact LOINC name/component/axes/edition, unit lookup, sourced labels and verified retrieval candidates |
| `recommendation_set_json` | Conditional target code/system/edition/name/analyte, proposed unit/transformation, original reference sets and applicable evidence candidates, explanations/sources and review requirement |
| `matched_analyte`, `suggested_analyte` | Existing exact-code component versus proposed-code component; absent when not resolved |
| `suggested_code_version` | Edition associated with the proposed target code |

A proposal never rewrites input. Exact membership, definition/name, dimensional fit, numeric completeness and evidence applicability remain separate. The HTML presents these structured fields as source → linked data → proposal, with checks in the final column.
