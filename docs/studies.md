# Laboratory studies and synthetic acceptance cases

## Run the complete example

```sh
uv sync --locked --no-editable
uv run --no-sync python tools/run_study_demo.py --out output/study-demo
```

Choose a new folder for another run. Three configurations and reports are created locally: LIS CSV, FHIR Bundle and a SQLite database that **stands in for a mapped CDS Hub view**. It is not a real CDS Hub schema or hospital extract.

## What happens to the synthetic data

All values, reference intervals, frequencies and terminology responses below are demonstration fixtures. They are not clinical reference recommendations.

| Input case | Export frequency | Core result | Review action |
| --- | ---: | --- | --- |
| LOINC `718-7`, 14 and 18.5 `g/dL`, reference 12–16 | 40 + 5 | One group; mean 14.5, median 14, IQR 0; code/unit fit supported, observed maximum outside supplied range | Keep the separate out-of-reference finding; do not interpret a patient |
| `718-7`, 140 `g/L`, reference 120–160 | 10 | Verified proposal `g/L → g/dL`, factor 0.1 | Review transformation: value 14 and reference 12–16; original data retained |
| `6690-2`, 5.6 `10*3/uL`, reference 4–10 | 20 | Supported code/unit/numeric/reference set | Physician confirms real assay/specimen applicability before deployment |
| `6690-2`, 5.6 `10*6`, reference unit `10*3/uL` | 3 | UCUM syntax can be valid while code-unit meaning is ambiguous | Resolve the missing denominator/source representation; no invented factor |
| `718-7`, `not-a-number`, `g/dL` | 1 | Error; native FHIR version also has an invalid JSON numeric encoding | Repair the source extraction/encoding; not a biological outlier |
| `718-7`, 14 `g/dL`, no reference interval | 2 | Unclear/incomplete requested set | Obtain the laboratory's context-specific reference metadata |
| Local `LIS-HB`, declared `urn:example:lis-tests` | 3 | Local code validated; candidate LOINC `718-7` from synthetic ConceptMap | Verify source identity, equivalence, specimen/method and target before coding change |

The 8 CSV rows represent **84 occurrences** and become **7 groups**. Expected statuses: 2 passed, 1 suggestion, 3 unclear, 1 error. Readiness: **3/7 groups; 75/84 occurrences in entirely ready groups**. Synthetic evidence forces credibility `synthetic_demo_only`.

The FHIR Bundle contains the same 84 individual Observations, with corresponding status/context and reference metadata. It produces the same 7 semantic signatures and matching known frequencies. One invalid value lacks an eligible numeric distribution proof; it remains a shared malformed group, not a proven numeric match.

The mapped database fixture deliberately changes the first hemoglobin reference upper bound from 16 to 17 and reduces the leukocyte count from 20 to 19. The comparison finds **6 shared groups, one missing/changed-context group and one added/changed-context group**, plus **one changed known frequency**. A changed signature may mean changed reference metadata, not a lost patient event.

## Study 1: semantic quality of a LIS export

Predefine the cohort/time window, current result/version policy, tests and retained fields. Export distinct point-value sets with source counts and complete references/qualifiers. Freeze the input, configuration, profile release, terminology pack and local model digest when used.

Primary descriptive outcomes: exact code/unit lookup coverage; supported code–unit fit; numeric/reference availability; group and occurrence-weighted readiness; counts of error/unclear/suggestion. Report each denominator and unresolved evidence separately. Do not call a catalogue coverage ratio a clinical accuracy measure.

Laboratory physicians independently review proposed code mappings, unit transformations and evidence applicability. Record `review_status`, `review_complete`, `review_correct`, `review_notes` against `tuple_id` in a **separate copy** of the CSV. Keep the original report unchanged for audit/comparison. Predefine adjudication and a held-out set for suggestion precision/coverage before enabling routine application.

## Study 2: congruence along LIS → FHIR → CDS Hub

Run the same selected-element contract at all three stages. Match cohort, period, timezone, status selection, handling of corrected versions, censoring, specimen/method/population context and component policy. Record the upstream selection SHA and deduplication policy in manifests. These are operator declarations, not independently inferred facts.

```sh
plausibility compare --baseline output/lis.csv \
  --target output/fhir.csv --target output/hub.csv --out output/comparison
```

The command requires matched declared cohort/period and distinct reports with recorded CSV hashes. It compares semantic group coverage, known source frequencies and eligible exact numeric histograms. It does not silently transform units or equate clinical concepts. Apply approved harmonisation upstream if the study intends to compare equivalent, differently coded representations.

Use missing/added group lists to identify changed reference metadata or qualifiers; inspect frequency differences for extraction loss/duplicates. Unknown counts cannot supply an occurrence loss ratio. Bounds-only or censored inputs cannot prove exact numeric congruence. Distinct exports cannot establish patient/event correspondence; use institutionally permitted item provenance if that is required.

## Practical laboratory interpretation

LOINC identifies more than an analyte: component, property, time, system/specimen, scale and method can change meaning. A dimensionally convertible unit cannot prove that those axes match. Manufacturer/assay, calibration, age, sex, pregnancy and population may affect reference intervals. Physiologic limits, analytic measurement limits and reference intervals are different concepts.

The core retains supplied context, checks exact terminology and mathematical/numeric consistency, and makes missing evidence visible. Optional research collects grounded candidates for expert review. It does not derive universal plausible patient limits, diagnose disease or remove outliers. Unavailable/draft evidence and missing populations remain unresolved.
