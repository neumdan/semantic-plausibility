# Laboratory studies

## 1. Semantic quality of a LIS export

Predefine cohort, period, result/version policy, tests and retained fields. Export code/system, values or bounds, unit/system, full reference sets, context and known frequencies. Use the same selected-element contract at every source.

Freeze input/configuration hashes, FHIR profile release, returned terminology versions, lookup evidence and local model digest when used. Measure verified code/unit coverage, supported code–unit fit, numeric/reference availability and error/unclear/suggestion counts. Show group and occurrence-weighted denominators separately. Catalogue coverage is not clinical accuracy.

Laboratory physicians review suggested mappings, transformations and evidence applicability. Record `review_status`, `review_complete`, `review_correct`, `review_notes` against `tuple_id` in a separate CSV copy. Predefine adjudication and a held-out set for suggestion precision/coverage.

## 2. Congruence along LIS → FHIR → CDS Hub

Match cohort, period/timezone, status selection, corrected versions, censoring, specimen/method/population context, component policy and deduplication. The export manifest records operator declarations; the runner cannot infer that selections are equivalent.

```sh
plausibility compare --baseline output/lis.csv \
  --target output/fhir.csv --target output/hub.csv --out output/comparison
```

Comparison uses distinct signatures, known frequencies and eligible numeric histograms with verified CSV hashes. Missing/added groups can reflect changed reference metadata; changed frequencies can reflect extraction loss or duplicates. Unknown frequencies cannot establish event loss; distinct sets cannot prove patient/event correspondence.

## Laboratory interpretation

LOINC component, property, time, system/specimen, scale and method define meaning. The configured FHIR server may expose part codes without resolved axis text; the runner leaves unavailable axis information unresolved. A convertible unit alone does not establish full clinical equivalence.

Manufacturer/assay, calibration, age, sex, pregnancy and population affect applicability of reference intervals. Numeric consistency and out-of-reference findings are descriptive. Actual published evidence plus expert review is needed for clinical plausibility. Patient Observations are never generated from aggregate means.

## Example input

`examples/laboratory.csv` contains 8 illustrative measurement rows representing 84 occurrences, grouped into 7 sets. These are input cases, not clinical reference evidence. All terminology verdicts come from the configured real services. The local example code `LIS-HB` stays unverified until its actual catalogue is configured; no example mapping is supplied. The equivalent illustrative FHIR Bundle is `examples/laboratory.bundle.json`.
