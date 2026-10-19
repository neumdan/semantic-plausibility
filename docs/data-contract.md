# Input and output contract

## Minimum export

Choose the cohort/time window upstream and preserve the selected semantic fields. CSV has one value per cell. A mapped database column supplies the same role; native FHIR supplies its structured elements directly.

### Laboratory columns

| Role in `input.columns` | Example value | Meaning |
| --- | --- | --- |
| `id` | `example-1` | Source record identifier for item output |
| `code` | `718-7` | Test code as supplied |
| `code_system` | `http://loinc.org` | Actual source coding system |
| `code_version` | `2.83` | Source terminology edition |
| `analyte_name` | `Hemoglobin` | Source test name / CodeableConcept text |
| `code_display` | `Hemoglobin [Mass/volume] in Blood` | Source coding display |
| `value` | `14` | Point value; zero is retained |
| `value_comparator` | `<` | Censoring qualifier |
| `unit` | `g/dL` | Source display label |
| `unit_code` | `g/dL` | Machine-readable unit code |
| `unit_system` | `http://unitsofmeasure.org` | Actual unit coding system |
| `reference_low` | `12` | Lower bound of this interval |
| `reference_high` | `16` | Upper bound of this interval |
| `reference_unit` | `g/dL` | Common unit code for both bounds |
| `reference_unit_system` | `http://unitsofmeasure.org` | Common system for both bounds |
| `range_text` | `Illustrative interval` | Source qualification/text |
| `specimen` | `Bld` | Resolved LOINC system-axis descriptor |
| `method` | `Automated count` | Resolved source method descriptor |
| `status` | `final` | Source observation status |
| `source_test` | `LAB-HB` | Local assay/test identifier |
| `component`, `property`, `time_aspect`, `scale` | Source axis descriptors | Additional known LOINC context |
| `count` | `40` | Known frequency of an already aggregated row |
| `value_mean` | `14` | Supplied mean instead of a point value |
| `value_sd` | `1` | Supplied sample SD, denominator `n−1` |
| `value_min` | `12` | Supplied observed minimum |
| `value_max` | `16` | Supplied observed maximum |

**Means, SDs, minima and maxima are numeric; the row's `unit_code` applies to them all.** Optional legacy statistic-unit mappings are accepted only when consistent with that common unit.

The 12–16 interval here is synthetic input for demonstrating the pipeline. Clinical applicability comes from the source laboratory and expert review.

Map a role once using its public name or documented internal alias. `reference_low/high` alias `range_low/high`; `analyte_name` aliases `code_text`; `specimen` aliases `loinc_system`. Defaults are explicit facts shared by every row. Source codes retain their declared systems throughout the analysis.

For different low/high units, use separate `range_low_code`, `range_high_code`, `range_low_system`, `range_high_system`, plus optional `_unit` displays and `_comparator` fields. For one qualified interval, use `range_type_code/system/text`, `range_applies_to_code/system/text`, and `range_age_low/high` with `_code`, `_system` and `_unit`. These are separate CSV columns.

For multiple intervals, alternate codings, components or detailed qualifiers, use the complete FHIR resource. A DB/CSV `resource_json` column is an explicit document input. A DB `reference_ranges_json` column can carry the complete FHIR interval array instead of scalar reference columns. Complete dosage JSON similarly preserves complex medication instructions. The simple CSV examples use scalar columns; the `*-fhir.csv` examples deliberately carry full documents for validation.

### Granularity

- `input.granularity="individual"`: one source record per row; use `output.mode="distinct"` to aggregate for the evidence pipeline. Optional `"items"` retains values and IDs for inspection.
- `input.granularity="distinct"`: source rows already contain known frequencies or statistics; map `count` when available.
- `output.mode="distinct"`: combine equivalent semantic contexts, keep their known counts and eligible statistics. Add `value` to `aggregation.group_by` for exact point-value sets.

Grouping preserves code systems/editions, names, unit codes/systems, reference qualifiers, specimen, method, comparators and status. Zero, missing, malformed and censored measurements remain distinguishable. Repeated source IDs in FHIR require upstream version selection. Supplied means/SDs are pooled only for disjoint groups with sufficient counts; SD requires at least two observations. Min/max-only exports keep those bounds. Statistics use the declared measurement unit; a verified linear conversion scales every compatible statistic and reference bound.

### Medication columns

Select the resource type and its recipe in [medication examples](../examples/medication/README.md).

| Field family | Explicit columns |
| --- | --- |
| Product | `product_code`, `product_system`, `product_version`, `product_name`, `form_text`, `ingredient_text` |
| Strength | `strength_numerator_value/code/system`, `strength_denominator_value/code/system` (one column per name) |
| Dose | `dose_value`, `dose_unit`, `dose_code`, `dose_system` |
| Route | `route_text`, `route_code`, `route_system` |
| Request/Statement timing | `frequency`, `period`, `period_unit`, `as_needed`, `dosage_text` |
| Administration | `rate_value`, `rate_unit`, `rate_code`, `rate_system`, `duration_value/code/system`, `constant_rate` |
| Context | `id`, `count`, `status`, Request `intent`, `do_not_perform` |

Each scalar row describes one complete product and one instruction. Full FHIR/DB JSON retains multiple ingredients, instructions, dose ranges and rate ratios. `constant_rate=true` means the source explicitly asserts uninterrupted constant delivery. The report labels calculations using source-declared strength as conditional.

## Output

Version 0.14 writes scalar CSV columns. Structured consumers use the audited evidence sidecar; the A2/A3 and review commands handle this hand-off automatically.

Every check writes a matching file set:

| File | Contents |
| --- | --- |
| `.csv` | One item/group per row; scalar source, check, proposal, transformation and review columns |
| `.html` | The same decisions in one field table per item |
| `.evidence.jsonl` | Complete structured sets, sources and internal review rows, linked by `tuple_id` |
| `.summary.json` | Configuration fingerprint, mapping, assumptions, methods, versions, counts, timing and artifact hashes |
| `.audit.jsonl` | Hash-chained execution events and file integrity |
| `.fhir-validation.jsonl` | Full-resource outcomes when validation is selected |
| `.studies.csv`, `.trace.jsonl` | A3 candidates/quotations and actual queries/model responses; A2 also records its trace |

CSV column families:

| Columns | Meaning |
| --- | --- |
| `tuple_id`, `resource_type`, `input_rows`, `occurrence_count` | Identity, domain, rows and known frequency |
| `code`, `code_system`, `analyte_name`, `value`, `unit_code`, `unit_system`, `value_*` | Direct laboratory source/statistic columns |
| `input.*` | Complete selected source leaves; repeated elements numbered from 1 |
| `input.reference_ranges.1.low.value` | First interval's lower value; `.code` and `.system` are separate |
| `input.dosages.1.doseAndRate.1.doseQuantity.value` | Medication dose value; unit/system have their own columns |
| `code_check`, `unit_check`, `code_unit_fit`, `reference_check` | Implemented check outcomes |
| `metadata_status`, `missing_metadata`, `detected_error_count` | Absent source metadata separately from demonstrated errors |
| `loinc_candidate_verified`, `ucum_candidate_verified` | Verified review candidates; source systems remain unchanged |
| `assessment.code.finding.1`, `.action`, `.recommendation`, `.source_status`, `.proposal_status` | The corresponding HTML field decision |
| `assessment.code.condition`, `.support_level`, `.reason` | Explicit system assumption, ordered corroboration and short review reason |
| `qc.profile_qc.*`, `qc.identity_set.*`, `qc.quantity_set.*`, `qc.code_quantity_fit.*`, `qc.complete_set.*` | Separate scope result, completeness, reason and supporting context |
| `assessment.code.source.1.source`, `.version` | Source URI and edition; additional citations get separate numbered columns |
| `suggested_code`, `suggested_name`, `suggested_unit`, `suggested_factor`, `transformation.*` | Sourced proposal and verified arithmetic |
| `fhir_validation_state`, `mii_numeric_set_ready`, `approved_mapping_ready` | Separate conformance, numerical readiness and clinician-approved mapping signals |
| `review_decision`, `reviewer`, `reviewed_at`, `review_evidence`, `approved_*` | Clinician feedback in a separate review copy |

Headers are the union of fields found in that run. Empty cells retain absence; every repeated bound/coding/dose has its own position. Formula-like strings are escaped for spreadsheet safety. Full objects formerly embedded in `*_json` CSV columns are now in the audited evidence file. Library consumers can use `report_details(path, hydrate=True)` from `semantic_plausibility.review_feedback` to read a verified complete row; older audited reports remain readable.

## Status meanings across workflows

Laboratory `passed` means the implemented checks found supporting evidence; `suggestion` presents a proposed transformation; `unclear` needs context/evidence; `error` identifies a supported inconsistency. Medication uses `needs_review` for unresolved technical findings. `dose_check` summarises structural/arithmetic review; clinical dose evaluation is the physician's step.

The legacy top-level `status` remains a broad workflow signal for existing consumers. Use `assessment.*` for individual fields and `qc.*` for the scope results below. A legacy `unclear` row can contain strong conditional matches and zero detected errors.

## Assessment scopes

| Scope / CSV prefix | What is assessed |
| --- | --- |
| `qc.profile_qc` | Implemented profile element rules, declared terminology membership and required bindings. Full-resource validation has its own document/run outcome. |
| `qc.identity_set` | Code, declared system, supplied name, specimen and any method required by the code. |
| `qc.quantity_set` | Finite numeric values/statistics and unit code/system membership. |
| `qc.code_quantity_fit` | Compatibility between the identified code and quantity unit; verified conversion requirements. |
| `qc.complete_set` | Combined identity/quantity/reference checks, quoted study measurements and reference intervals, with clinician review still required. Analytical performance limits are counted separately. |

Every scope has `state`, `complete` and `reason`. Completeness means the selected fields were supplied; it does not mean they are correct. `supported` refers to that scope only. `conditional` requires confirmation of the stated system/context. `incomplete` requires metadata; `unverified` needs lookup/context resolution; `error` identifies a demonstrated conflict. `transformation_required` requires a verified conversion. `needs_review` retains pending checks; `needs_evidence` requests physiological evidence after technical checks; `evidence_for_review` means sourced numeric evidence is available for applicability review.

For missing source systems, a literal `2823-3` match can have `support_level="code_only"`. A matching name adds `code_name`; a compatible unit adds `code_name_unit` when the source context does not conflict. These are ordered corroboration levels, not calibrated probabilities. A different proposed code or spelling correction remains a review proposal. No system URI, specimen or measurement is written back to the input.

The HTML contains the same five scope rows as the CSV. `.evidence.jsonl` retains `assessment_layers`; library callers can use `semantic_plausibility.assessment_layers(row, summary)` on a hydrated report row. `qc.complete_set.evidence_state` distinguishes `not_searched`, `no_eligible_evidence` and `evidence_for_review`. A search without eligible evidence does not show that no relevant study exists.

`lookup_credibility` is a source-support category. `mii_numeric_set_ready` counts the defined numeric/code/unit/reference checks. Full FHIR conformance is separately recorded per validated document/run. Clinician-approved mapping readiness counts reusable plans that pass current target/context checks.

## Reading the recommendation

Use **field → source → method/finding → recommendation and Why → source/proposal status**. Green source means supported within scope; blue **conditional match** names the assumption; yellow needs resolution; red identifies a problem. Green proposal means keep; blue means confirm the condition; yellow means review; red asks for source clarification and a supported replacement. Grey means not applicable.

A conversion proposal includes its verified factor and consistent value/statistic/reference transformations. Name normalisation retains the local name. Study evidence retains the measured quantity type, literal quoted unit, checked UCUM spelling, population, source and extraction state. Reference-interval adoption requires laboratory review. [Clinician feedback](research.md#clinician-feedback).
