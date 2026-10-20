# Lean laboratory validation study

**Question:** which incorrect laboratory sets are detected, how useful are recommendations, and how much clinician review time is needed?

One case is one **distinct set**: code/system, name, specimen/method, values/statistics, unit/system and reference context. [Objectives and limitations](workflow.md#objectives-and-limitations).

## 1. Freeze the pilot and judge the source

1. Write a short local study note: site, cohort/period, selected tests, profile, grouping, sampling and review instructions. Select cases before seeing results. Keep a representative sample separate from deliberately enriched error cases; precision and workload depend on this mix.
2. Keep inputs, configuration, profile/terminology versions, model digest and original audited reports. Separate development from evaluation; keep related analytes/mappings together when splitting cases.
3. Have a laboratory clinician judge each **source set before viewing pipeline findings**, using local assay information and appropriate evidence. For validation, obtain a second independent review and resolve disagreements into an adjudicated label; retain both originals.
4. Reveal the report, rate its recommendations/sources and time that review in seconds per case. Include source checking; record setup and adjudication time separately. Use the same source judgements across workflows.

Compare `lookup`, `ai-lookup` and `ai-evidence` on the same cases. For `fhir-validation`, use corresponding complete resources and assess formal conformance separately. Pair cases explicitly when exports change grouping. This is a pilot protocol; choose required precision/sample size and acceptance thresholds before a confirmatory study.

## 2. Generate and complete the template

After a grouped check, run:

```sh
podman-compose run --rm run-plausibility-check study-template \
  --report output/lookup.csv --out output/study/lookup-review.csv
```

For Docker, replace `podman-compose` with `docker compose`. For Python, replace the entire prefix `podman-compose run --rm run-plausibility-check` with `uv run --no-sync run-plausibility-check`. After updating code, rebuild once with `podman-compose build run-plausibility-check` (or Docker's equivalent); for Python, run `uv sync --locked`.

Study commands use saved reports locally: no new TOML, lookup or model call. Keep the original report and its sidecars. In the generated CSV, import the first five identity/source columns as **text** and keep them, all rows and headers unchanged. Code/name/unit help locate the case; review its **complete source set**, not just those cells.

Fill these seven columns; retain UTF-8 comma-separated CSV:

| Column | Allowed values / instructions |
| --- | --- |
| `reference_assessment` | `correct`: enough context, no semantic error. `error`: established incorrect/conflicting set, even if metadata is also missing. `metadata_only`: missing context prevents confirmation, without an established contradiction. `indeterminate`: other unresolved uncertainty. Use the independent source judgement. |
| `issue_fields` | Affected fields, e.g. `unit_code;reference_range`; required for `error` and `metadata_only`. |
| `recommendation_assessment` | Judge the **whole action plan**, including conditions: `acceptable`, `partly_correct`, `incorrect`, `unassessable`, or `no_proposal` when there is no concrete keep/correct/confirm proposal. |
| `evidence_assessment` | `supported_applicable`: citations support the claims and fit the local context. `unsupported_or_inapplicable`: a claim or its applicability fails. `uncertain`: unresolved. `not_present`: no study evidence. Check quantities, units, interval type, population, specimen and method. |
| `review_seconds` | Measured report-review time; leave unknown time blank. Zero is an actual measurement. |
| `reviewer` | Local reviewer/adjudicator identifier. |
| `notes` | Short reason, needed correction and source reference. |

Leave unfinished rows blank in the review columns. Source judgement may be completed before recommendation/evidence review; each completion count remains explicit. A real abnormal laboratory value can still be correctly represented. “Confirm UCUM” can be an acceptable conditional proposal while the source system remains unconfirmed.

**Example:** if the clinician establishes a wrong unit, enter `error` and `unit_code`; judge whether the proposed unit **and conversion of values/reference bounds together** are acceptable. An omitted system URI with unresolved meaning is `metadata_only`.

These labels evaluate performance. Use the separate [clinician mapping review](research.md#clinician-feedback) and `review-import` to approve reusable mappings.

## 3. Calculate and read the metrics

```sh
podman-compose run --rm run-plausibility-check study-metrics \
  --report output/lookup.csv --review output/study/lookup-review.csv \
  --out output/study/lookup-metrics
```

Open **`output/study/lookup-metrics.html`**. The JSON contains counts, confusion matrix, report/review hashes and configuration fingerprint. Use separate filenames for each workflow.

**Fixed rules:** an error flag is `detected_error_count > 0` or any of the five persisted `qc` scopes in state `error`. A review request is an error flag or any yellow, red or conditional field proposal. Only reference labels `correct` and `error` enter error accuracy metrics; metadata-only, indeterminate and unfinished cases are counted separately.

| Metric | Calculation |
| --- | --- |
| Error-detection sensitivity | TP / (TP + FN): confirmed erroneous sets given an error flag |
| Error-flag precision | TP / (TP + FP): assessable error flags that were true errors |
| Review-capture sensitivity | Confirmed erroneous sets sent to review / all confirmed erroneous sets |
| Recommendation acceptability | `acceptable` / (`acceptable` + `partly_correct` + `incorrect`); whole plans, including “keep” |
| Proposal availability | Assessed plans except `no_proposal` / all recommendation-assessed sets |
| Evidence applicability | `supported_applicable` / (`supported_applicable` + `unsupported_or_inapplicable`) |
| Review effort | Pipeline review-queue fraction; timed sets, median seconds/set and total minutes |
| Coverage | Reference/recommendation review completion; error-evaluable sets / all sampled sets |

TP = erroneous set flagged; FN = erroneous set missed; FP = correct set flagged; TN = correct set unflagged. This is **set-level** detection: even a flag on another field counts. Use `issue_fields` and notes to examine whether the actual problem was identified. Metrics are unweighted by source occurrence counts.

**Arithmetic example only:** three of four confirmed errors flagged gives sensitivity 3/4. With one correct set also flagged, precision is 3/4. “Partly correct” proposals stay in the denominator and do not count as acceptable.

Every rate shows its denominator; an empty denominator is **Not estimated**. Unassessable/absent recommendations and evidence remain visible as counts. Sensitivity, precision and recommendation acceptability include 95% Wilson intervals assuming independent sets. Related analytes/site clusters need study-specific analysis; an enriched pilot does not establish population performance. Increasing warnings can increase review capture and workload together.

## Optional: LIS → FHIR → CDS Hub comparison

Match cohort/period/timezone, corrected-result/status policy, specimen/method/reference context and deduplication. Declare these in the export manifests, then:

```sh
uv run --no-sync run-plausibility-check compare --baseline output/lis.csv \
  --target output/fhir.csv --target output/hub.csv --out output/comparison
```

This checks distinct signatures, known frequencies and eligible numeric distributions. Changed context can create different groups; review differences before treating them as conversion loss. Distinct sets cannot establish patient/event correspondence.

## Tests and examples

`test_validation_study` covers controlled review-label arithmetic, empty/uncertain denominators, partial reviews, duplicate/changed identities, invalid times, output protection and template → metrics on an actual offline pipeline run. No synthetic terminology responses or clinical accuracy estimates are supplied.

The laboratory files in `examples/` exercise the software. Clinician-adjudicated local exports provide the study evidence.
