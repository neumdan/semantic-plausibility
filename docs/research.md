# Evidence research and clinician feedback

## Research from the checked export

Start with `input.granularity="individual"` for measurements or `"distinct"` for supplied statistics, and keep `output.mode="distinct"`. The check aggregates individual CSV/FHIR/DB measurements by semantic context before research. The audited report must use context grouping, with `value` omitted from `aggregation.group_by`.

A deterministic check verifies the source set and selected MII target rules. A separate research command reads that audited report, so the hospital database is not queried again. For each exact LOINC/edition it retrieves actual page and study evidence; an exactly verified, selected alternative or clinician-approved target can support an unmapped LIS set. Unselected shortlist hypotheses remain review candidates and do not each trigger a literature search.

```sh
uv run --no-sync research-evidence --config pipeline.toml --report output/laboratory-review.csv --plan
uv run --no-sync --env-file .env research-evidence --config pipeline.toml --report output/laboratory-review.csv
```

Use `--scope lookup` for AI-assisted terminology recommendations without literature retrieval, or `--scope studies` to add the configured study APIs. Omit it to use TOML `research.scope`. Both modes use the same local model and independent code/unit guards. `--plan` respects the selected scope: lookup mode shows no study queries or study extraction prompts.

Without `--out`, lookup mode produces `<checked-name>.recommendations.csv/html`; studies mode produces `<checked-name>.research.csv/html`. Each has its own audit, trace and readable `.studies.csv` table. The table links extracted statistics/intervals to the checked tuple IDs and retains unsupported discoveries explicitly. For a direct comparison, start both modes from the original checked report, not from the other AI output.

No second configuration is needed. The command selects the checked CSV at TOML `output.path` by default; `--report` is optional. A raw export or an edited CSV must first be checked, and its matching summary/evidence/audit files must remain together. [One-time model setup and exact Docker/Podman/Python commands](shipped-stack.md#compose-ollama).

The plan prints the system/task prompts, profile routing and planned queries without network/model calls. The executed dossier uses the selected output prefix: CSV, HTML, evidence JSONL, summary, trace JSONL and artifact audit. Original check statuses/readiness are retained; the dossier adds review evidence.

Structured detail is retained in `.evidence.jsonl` under `report_row`: `study_evidence_json` contains all retrieved evidence candidates. `reported_measurements_json` contains reported values or units; `reference_candidates_json` contains only reference intervals, analytical intervals and documented extremes. `matched_analyte` comes from the exact LOINC component; `suggested_analyte` and `suggested_name` are conditional target definitions. An absent or misspelled source name can receive a canonical terminology name even without a model inventing a new label. Empty fields mean that no supported extraction was obtained. Retrieved articles remain visible even when extraction fails or abstains. `extraction_unavailable` means no supported extraction was obtained from the local model; a study discovery has no implied numeric interval. Model availability and rejected responses remain visible in the trace.

## Statistics, counters and runtime

Aggregated input can supply mean, sample SD, min and max with explicit units. These remain local and are shown beside terminology proposals. A verified positive linear conversion scales every statistic; pooling only uses disjoint source groups with sufficient counts/SD. Summary statistics do not establish a population reference interval.

A **study record** is one sourced candidate (a statistic, interval, unit statement, or unsupported discovery). Several records can come from one article. The HTML and summary count distinct articles retrieved, extracted measurements, analytical limits (LoD/LoQ/LoB), extracted intervals, unclassified numeric ranges, unit-only records, articles without supported extraction and failed extractions separately. The `.studies.csv` includes LOINC, linked tuple IDs, statistic kind, numbers, unit, source URL/DOI, exact quotation, hash and retrieval time. The evidence sidecar retains per-group detail; `.studies.csv` exposes individual values in separate columns. A discovery without numbers must not be counted as a verified range.

Analytical detection/quantification/blank limits have their own `analytical_limit` category and HTML row. Their labels, numbers and units must be explicitly linked in the source. They describe instrument performance and do not enter patient-value or reference-interval comparisons. Interference/spiking concentrations are excluded. Reference bounds must form one printed interval; male/female limits cannot be mixed. For explicitly parenthesized sex strata, the application offers complete literal pairs as `printed_bounds`; the model selects one ID or abstains. The restored record retains its exact stratum and quotation. Selection does not establish that the stratum applies to the input population.

MCHC research uses the specific terminology labels (MCHC / mean corpuscular hemoglobin concentration), rather than the generic hemoglobin component. The source guard retains parenthesized units and population strata, and requires the quoted quantity to belong to that analyte. “Mean” within an analyte name is not interpreted as a statistical mean.

The CLI announces each group and article, then prints model elapsed time every 15 seconds. The trace records task duration and source-guard rejection reasons. It is written with the completed dossier; console progress is available while it runs. CPU inference is normally the slowest part, particularly in a VM. A combined model call per article covers prose statistics and intervals using compact passage IDs; explicit table cells are also extracted deterministically; repeated LOINC/edition groups reuse its evidence. Larger article limits cost more time and can still produce no usable extraction. Neither article retrieval nor a model HTTP success is evidence acceptance.

## What is searched and prompted

The application performs the searches; the local model receives retrieved text and a bounded task. It cannot invent a code, factor, interval or citation and mark it verified. Model query spelling/translation phrases are hypotheses until actual retrieval/exact lookup supports them.

| Task | Example input to the task | Guard / output |
| --- | --- | --- |
| Search spelling | `analyte_name="Hämoglobim"` | At most two lexical query hypotheses; retrieve them through configured sources. |
| LOINC/name selection | Input name/unit + at most five exactly verified candidate codes, full sourced names, axes and unit fit | Select only compatible shortlist codes/names, or abstain. The response schema enumerates those verified choices; rejected candidates and provenance remain in the report instead of being repeated in the prompt. Unit compatibility alone is not analyte identity. |
| Unit spelling | Source spelling + validated catalogue shortlist | Select a literal candidate or abstain; preserve meaningful prefixes, powers and denominators. |
| Study measurements | Real abstract/full-text passages + verified analyte/LOINC | Quote reported values/units; distinguish individual values, summaries, unit-only and unspecified reports. |
| Study intervals | Real abstract/full-text/table excerpt | Quote a reference interval, analytical interval or documented extreme only when the text supports that type. A query category alone does not classify an interval. |

For hemoglobin, searches use the verified component and sourced spelling aliases (for example Hemoglobin/Haemoglobin), then reference-interval, summary-statistic and bounded fallback queries. PubMed searches title/abstract. Europe PMC searches full text when `research.europe_pmc.FULL_TEXT=true` (the default); `false` restricts it to title/abstract. Only analyte labels derived from the verified terminology are used: general LOINC related terms such as “Blood” or “Level” are not analyte synonyms. PubMed and [Europe PMC](https://europepmc.org/RestfulWebService) remain separately configurable.

Candidates are deduplicated by DOI/identifier and screened **before** the model budget is spent. A usable passage must contain an analyte-specific numeric/unit statement, or a table cell with explicit row/unit/statistic headers. Relevant open-access papers are inspected before rejecting a missing numeric abstract. Reference-interval titles and explicit analyte titles rank first. The starter retrieves up to six Europe PMC and three PubMed results per query, then selects at most four articles per code/edition. Search stops after a completed query category supplies that budget, or after the bounded query categories. Excluded URLs, scores and reasons are retained in the trace; screened/selected counts are article–code pairs, while extracted article counts are globally deduplicated. Screening is a transparent heuristic, not study-quality or clinical-applicability grading. This is a bounded evidence search; screening does not establish study quality or clinical applicability.

Method differences/bias, diagnostic cutoffs and outcome-risk/incidence statements are excluded as measured analyte results. Censored values such as `<95` cannot become an exact measurement without their comparator; this extraction schema therefore rejects them. Reference-interval endpoints cannot also be counted as individual patient values. Percentages cannot support a LOINC mass, substance or number **concentration** (`MCnc`, `SCnc`, `NCnc`): a hemoglobin fraction in percent does not verify total hemoglobin in g/dL. These rules apply during screening and after model extraction. They are conservative and may miss relevant sentences; they do not resolve every subtype or prove clinical equivalence. A documented reference interval can still mention a threshold in its discussion. Review its exact bounds, population, specimen and method.

Each proposed interval/statistic passes its own source guards. A failed claim is omitted; independently supported claims from the same article remain. The raw model reply and each `extraction_rejections` reason stay in the trace. A mean/median cannot also be counted as an individual patient value; exact duplicate statistics are removed. The report counts calls with rejected parts separately from wholly failed article extractions. Unit statements with no numeric value are counted as unit-only evidence, even when the model calls their role “unspecified”.

### Source units and quantitative evidence

HTML/XML superscripts are retained explicitly: `10<sup>3</sup>/µL` becomes `10^3/µL` in the rendered source text, never `103/µL`. Whitespace is collapsed; the rendering rule and provider-response hash are recorded. The quotation must remain an exact span of that rendered source.

An unambiguous typographic match may restore a model unit such as `×10³/μL` to the exact supplied `× 10^3/µL`. The raw model answer and the applied rule remain in the trace. Case, prefix, exponent and denominator cannot change: `mg/dL` is not `g/dL`; `103/uL` is never guessed to mean `10^3/uL`. Numbers remain literal source tokens, and the numeric/unit passage must belong to the same analyte clause; an adjacent biomarker cannot supply its bounds. Combined lists such as “total hemoglobin and HbF … respectively” remain unresolved: the guards do not guess which number belongs to which analyte. Separate labelled clauses can still support extraction.

The report's **`unit` is the quoted spelling**. Separate `unit_ucum`, `unit_validation_state` and `unit_validation_source` fields show the typography-derived spelling only after validation by the configured UCUM service. For example `× 10^3/µL` → `10*3/uL` preserves the numerical scale. No new default service is contacted when UCUM is disabled. This does not establish that the study population, specimen or assay is applicable locally. Every interval/statistic remains a yellow review candidate; it never replaces the source data automatically.

`*.trace.jsonl` records actual query parameters, model instructions, supplied evidence, guarded replies and rejected/unavailable attempts. The HTML/summary distinguishes retrieval requests, literature searches and extraction failures; extraction failures do not count as additional searches. Credentials are omitted. Hospital values/reference bounds and patient IDs are compared locally and stay out of queries/prompts; published measurements in retrieved abstracts are evidence inputs. Source hashes and retrieval times accompany the dossier.

LOINC axes returned as `LP…` part codes are resolved through exact `$lookup` requests in the same edition. Unresolved core axes prevent a model-selectable coding proposal. The primary service's bounded name `$expand` supplies candidates; each candidate still needs exact code/edition lookup and independent unit/context checks. A component-only label such as “Hemoglobin” is identified as `component_label`, not as the complete LOINC Coding display.

## Europe PMC and recommendation sources

Use the same `pipeline.toml` as the check. Edit its existing research tables:

```toml
[research]
enabled = false # research-evidence activates research explicitly
scope = "studies"
sources = ["europe_pmc", "pubmed"]
max_articles_per_loinc = 4
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.europe_pmc]
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESULTS = 6
FULL_TEXT = true # Include permitted open-access XML paragraphs and labelled tables.
# Optional public search restriction; applied to Europe PMC only and recorded in the trace:
# QUERY_FILTER = '(human OR humans OR patients OR volunteers OR healthy OR population) NOT (feline OR cats OR canine OR dogs OR cattle OR rats OR mice OR veterinary)'
```

Europe PMC's public search needs no API key. The adapter reads metadata/abstracts using `resultType=core` and, with `FULL_TEXT=true`, retrieves a bounded selection of permitted open-access articles as `fullTextXML`. Article title, DOI when supplied, URL, retrieved-response hash and time come from the provider, not the model. Increase `MAX_RESULTS` and the unique-article cap deliberately; a one-result query can miss the useful study. A search restriction reduces irrelevant hits but does not establish a study's population or applicability.

Extraction uses the sourced analyte definition and source-constrained passage IDs. The model returns compact IDs; the application restores the original text before checking every numeric/unit token. Both compact replies and restored accepted records are retained in the trace. HTML is removed and abstract whitespace is normalised to plain spaces; the original API response hash is retained. Units/numbers must occur in the selected sentence, which must explicitly name the verified analyte or a terminology-derived alias. Reference-interval abbreviations are accepted only when explicitly defined in that same abstract (for example “reference intervals (RIs)”). Other abbreviations and ambiguous table layouts and context outside the selected passages can still be missed. Context fields are literal source sentences or empty, not inferred clinical classifications. Compact prompts restrict these choices to sentences with explicit participant, specimen or assay terms; a blood-cell name alone does not establish the specimen. This conservative English-language filter can miss context and does not establish applicability. Missing numeric intervals stay missing; quotation/schema failures appear as `guard_diagnostic`. Rejected content does not disable independent tasks; three consecutive transport/HTTP failures open the model service circuit.

CSV and HTML use the same field decisions, traffic lights and sources, including the selected research scope.

The HTML table reads **source set → linked terminology → complete proposal**. Scalar `input.*`, `assessment.*` and proposal columns carry the readable CSV projection. Complete `source_data_json`, `linked_data_json` and `recommendation_set_json` objects are retained in the audited evidence record’s `report_row`. The complete set includes code/system/edition, LOINC component/analyte and axes, canonical name, source values, proposed unit, transformation operations for every bound, all original reference contexts and candidate study intervals. A sourced canonical name is conditional on the chosen code; it is not proof of the measured analyte.

The HTML lists **supporting sources beside each proposal**: exact coding lookup and edition, name/translation source, unit catalogue/verification source, or the article and quoted excerpt for an evidence candidate. Missing source information is explicitly labelled for review. API/file checks, model selection and clinician adjudication remain distinct. `study_evidence_json`, `reference_candidates_json`, the evidence JSONL and trace retain the detailed record.

Your downloaded BfArM CodeSystem supplements can be read from sibling `../input/terminologies/bfarm-lab/` or ignored `local/terminologies/bfarm-lab/`. The report records the actual file hash and published canonical URL beside sourced labels. Enable `lookups.bfarm_lab`; retain primary LOINC/UCUM validation. The LOINC 2.83 German supplement applies to base edition 2.83; it is not silently combined with another edition. [Local BfArM configuration and credentials](lookups.md#3-api-files-and-fallback-in-one-toml).

## Unmapped LIS example

The supplied [six illustrative cases](../examples/laboratory-research.csv) include local `LIS-HB` and unit code `GDL`, with display `g/dL`. Their local code/unit membership remains unknown until you provide your **real** source catalogue. A validated UCUM display can support a candidate, but does not establish that `GDL` means the same quantity or supply a verified source conversion.

The whole set is retained: name, code/system/edition, values/bounds, unit/system, every reference interval, specimen/method and qualifiers. CSV `source_code_check`, `source_unit_check` and `source_code_unit_fit`, plus structured `source_reference_check_json` in the evidence sidecar, describe source checks. Target-profile checks and `fhir_mapping_proposal_json` describe what would need filling in FHIR. A local valid code can coexist with a need for a preferred LOINC coding and UCUM unit.

The eight-row starter maps `analyte_name="analyte_name"`. For your own export, map the actual source analyte-name column. To try name/code mismatch cases, use `examples/laboratory-research.csv` and add `method="method"` to the existing `[input.columns]` table. Set a fresh output prefix, check it, then run research on that report.

The four-mode Podman example includes a deliberate `g/dLL` typo in `examples/lab/aggregated.csv`. AI lookup selects from independently validated spelling candidates; review the proposed `g/dL` before applying any change. A bare `10*6` cannot establish a cell-count denominator. A high value does not justify changing a unit or widening a reference interval. Age/sex, specimen, method, platform, calibration and preanalytical/population context govern clinical applicability.

Candidate discovery uses the configured primary LOINC API as well as optional name services. Exact membership and numerical unit conversion are necessary but insufficient: a candidate conflicting with any known axis of an existing LOINC is excluded from model selection and retained for clinician review. For example, LOINC `6690-2` (leukocyte concentration) cannot be replaced by `26471-3` (other leukocytes/leukocytes, a fraction) to accommodate a bare `10*6`. Its UCUM syntax can be valid while the intended denominator remains unknown. This conservative guard does not certify the existing coding; correcting that coding requires resolving the contradiction with the clinician.

## Small evidence pipeline: values and reference intervals

Use the existing `ai-evidence` command and `[research]` section. Keep `sources=["europe_pmc"]` for a single literature provider, `max_articles_per_loinc=4`, and in the existing `[research.europe_pmc]` table set `MAX_RESULTS=6`, `FULL_TEXT=true`. Preserve the API URL and your search filter. No new service, model, package or configuration file is required.

1. Run `check` to create an audited distinct report. Individual CSV/FHIR/DB inputs are aggregated first.
2. Preview with `research-evidence --config pipeline.toml --report output/lookup.csv --scope studies --plan`.
3. Execute with `research-evidence --config pipeline.toml --report output/lookup.csv --scope studies --out output/evidence.csv`. Use the same runner/Compose commands as above and the configured local Ollama model.
4. Read `output/evidence.html` for field findings and local comparisons; open `output/evidence.studies.csv` for each sourced statistic/interval. Each numeric value, bound and unit has its own column. The matching trace retains the exact requests, model replies, guarded rejections and skipped table layouts.

Python searches the configured [Europe PMC API](https://europepmc.org/RestfulWebService), retrieves abstracts and permitted open-access `fullTextXML`, and selects bounded analyte passages. Explicit nonhuman titles are excluded as a heuristic; population applicability still needs review. Full-text fetch attempts and model articles are each capped by `max_articles_per_loinc`; retrieved XML is reused across codes within the run. There are no image/PDF/OCR or supplementary-file downloads. XML has the existing 4 MiB HTTP limit, rejects entity declarations, and never loads external references. At most 24 passages / 12,000 characters enter the extraction dossier per full-text article.

**Tables:** Python reads explicit headers, units, row spans and column spans. It extracts scalar means/SDs/medians/extrema, explicitly labelled mean–SD pairs and complete reference intervals printed in one cell. Each claim keeps its row/column location, original labels and stratum. Confidence intervals for reference limits and sample counts cannot become analyte results. Irregular layouts, ambiguous units and bounds split across cells are left unresolved. Table evidence is explicitly labelled as a rendering of cells and headers, not a contiguous prose quotation. Source hash, XML URL, licence text and reported sample size accompany it. A reference interval cited from a manufacturer remains labelled by its source column; it is not relabelled as a new population estimate.

**Model:** Ollama extracts from the supplied passages and may abstain. Table claims are additionally checked against independently parsed cell roles; unsupported parts of a response are rejected without deleting independently supported claims. The full text is untrusted data, never instructions.

**Compare locally:** the report compares like statistics (mean with mean, SD with SD), and supplied reference bounds with each published reference interval. Verified positive linear conversions are explicit. Differences and overlap are descriptive findings; no arbitrary cut-off, pooled interval or automatic correction is produced. Missing source systems can support a conditional comparison only with verified literal code/name/unit candidates. The report states that condition. Source population, specimen and assay applicability remain unverified; a value outside a reference interval is not itself a data error. No comparable evidence means insufficient evidence, not implausibility. Analytical limits stay separate.

The table also compares your observed minimum, mean and maximum with published reference bounds. A source median stays a median: it is not compared as if it were your mean. The same article may support several LOINCs; article/LOINC record counts therefore do not count independent studies. `articles_screened` counts screening decisions per LOINC, while the evidence table lists the distinct papers retained in the dossier.

**Real test case:** [Verification of reference intervals for routine biochemical tests using the reflimR in Turkish adults](https://europepmc.org/article/MED/41671238), DOI `10.1371/journal.pone.0342530`, has potassium reference intervals in Table 2. The tests retrieve its actual XML and check the male and female cells separately; the confidence interval of a reference limit is excluded. Set `QUERY_FILTER='DOI:10.1371/journal.pone.0342530'` temporarily for a reproducible extraction check, then restore your broader filter. This focused test establishes parser behaviour, not search recall or local applicability.

**Later sources:** NHANES distributions and authorised MII/FDPG aggregate exports can use the same sourced statistic/interval representation. No connector to those datasets is required for this Europe PMC workflow.

## Clinician feedback

The check CSV contains empty review columns. Return either that copy with only review cells edited, or a small CSV containing `tuple_id` and review fields. The original report/audit stays intact; import verifies its artifact hashes and protects supplied source/check cells.

| Field | Fill with |
| --- | --- |
| `review_decision` | `approved`, `rejected`, `deferred` or `revoked` |
| `reviewer`, `reviewed_at` | Responsible reviewer and ISO timestamp with timezone |
| `review_evidence` | Rationale and source documents/citations used for adjudication |
| `approved_code`, `approved_code_system`, `approved_code_version`, `approved_name` | Target code/system/edition and optional sourced full Coding name |
| `approved_unit`, `approved_unit_system`, `approved_factor` | Target unit/system and explicit positive factor |
| `review_basis` | `identity`, `normalization`, `linear_conversion` or `semantic_reinterpretation` |

**Functionality example:** the supplied hemoglobin set `140 g/L` with illustrative bounds `120–160 g/L` has an independently checked factor `0.1` to `g/dL`. A reviewer can approve target LOINC `718-7`, unit `g/dL`, factor `0.1`, basis `linear_conversion`; the bound plan becomes `12–16 g/dL`. These numbers illustrate conversion, not clinical reference recommendations.

For local or semantically mistyped units, `semantic_reinterpretation` explicitly records a clinician-declared interpretation. It is not labelled as a verified conversion from the original notation. Reference bounds with different units are checked independently; unresolved bounds/context keep the proposal under review.

```sh
uv run --no-sync --env-file .env run-plausibility-check review-import --config pipeline.toml --report output/laboratory-review.csv --review local/clinician-review.csv --registry local/clinician-mappings.json
```

Set `input.source_namespace` before collecting reviews. Enable reuse:

```toml
[quality]
review_registry = "local/clinician-mappings.json"
```

Select a fresh `output.path`, then run another check. Approved decisions are reused only for the same source/site, codes/names/units/editions, reference/context set, catalogue fingerprint and profile rules/edition. Measurement values may vary; mapping semantics may not. Every reuse rechecks target identity, unit, factor and reference transformations. A changed source/context/profile or revoked decision prevents reuse. `mapping_review_state`, `approved_mapping_ready` and `clinician_mapping_json` show the outcome. Original source checks remain visible.

All four laboratory reports prominently show **Approved mapping ready: approved / selected groups**. Without a review registry the numerator is zero. Research refreshes this decision state rather than inheriting an old approval. Numerical readiness, formal FHIR conformance and clinician approval remain separate measures. The expandable field table distinguishes deterministic lookup proposals from model-selected changes and supported study extractions; “not run” and “no supported extraction” never imply a successful check.

Missing metadata is reported separately through `metadata_status`, `missing_metadata` and `detected_error_count`. lookup tests undeclared literal code/unit candidates against the configured LOINC/UCUM services, within the target profile's allowed systems. A unique exact code/name match with compatible units becomes a yellow review proposal. The report shows the definition's specimen as expected context; the source specimen and missing URIs remain unchanged. Explicitly wrong systems, invalid declared codes/units and invalid numbers remain errors. The original FHIR rule violations remain in the evidence; full resource conformance is assessed by the separate validator. An AI review can supplement the exact candidate evidence. Confirming source identity and approving a mapping remain clinician decisions.

Evidence reports show article, numerical-extraction and rejection counts above the item tables. An accepted model reply may contain an empty result: this is not evidence for a reference range. Rejected/unavailable extractions are also flagged in the affected value/reference fields. Exact source quotations stay mandatory; the bounded typography matcher restores literal unit spelling and records every change, while numerical conversion and guessing remain forbidden. `elapsed_seconds` records the current research run; `source_check_elapsed_seconds` separately retains the original lookup duration.

The registry retains history and explicit supersession. It changes mapping rules, not model weights. Hashes verify integrity; the institution remains responsible for who is authorised to approve decisions. Apply approved plans in ETL and run the full FHIR validator on actual individual resources.

Imports require the same profile rules as the checked report. A later decision must have a later timestamp than the one it supersedes, so an old approval cannot undo a revocation. Reports made before this workflow need a new check before review import.

## Future domains and FDPG

Medication needs product/ingredient, strength, dose/unit, route and timing; Condition needs edition, certainty/status, severity and site; Procedure needs edition, status, time, site and technique. Medication terminology and deterministic dose checks use a [development adapter](../examples/medication/README.md). Medication AI/evidence, domain-specific assessment scopes and operator onboarding are planned for version 2. Use this research workflow for laboratory Observations; the catalogue also lists sources for later Condition/Procedure work.

A later FDPG provider must use an authorised interface or permitted aggregate export with selection/cohort provenance. Feasibility counts alone cannot reconstruct value distributions or establish reference intervals. Evidence of practice supports final clinician evaluation.

## Real service tests

```sh
SQC_LIVE_TESTS=1 SQC_LIVE_RESEARCH_TESTS=1 uv run --no-sync python -m unittest discover -s tests -v
```

For a running local model add `SQC_LIVE_MODEL_TESTS=1`, `SQC_TEST_MODEL_BASE_URL=http://127.0.0.1:11434/v1` and `SQC_TEST_MODEL_NAME=ministral-3:3b`. Model-specific proposal accuracy requires clinician adjudication beyond these functional source/guard tests. [Docker/Podman/model setup](shipped-stack.md).

## Focused, reproducible study lookup

To inspect an already identified paper, select the [individual CSV input](../examples/lab/README.md#individual-csv) in `pipeline.toml` with its three synthetic Hb values. In the existing research tables set `sources=["europe_pmc"]`, `max_articles_per_loinc=1`, and `research.europe_pmc.QUERY_FILTER='DOI:10.1002/jcla.70061'`. This explicitly selects the published Northern Sudan reference-interval study; it is a functional extraction example, not a recommendation to adopt its intervals locally. Keep the API URL unchanged.

Run the check first, then `research-evidence` with that same TOML and a fresh output name as shown in the [laboratory recipes](../examples/lab/README.md#run-every-source-the-same-way). The query, quotation, population wording and unit are retained. Default bounded search may find no numeric support; a focused DOI test does not establish retrieval recall or general clinical correctness. Open-access XML paragraphs and explicitly labelled table cells are supported; ambiguous layouts remain visible as skipped in the trace.
