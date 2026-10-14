# Evidence research and clinician feedback

## Research from the checked export

A deterministic check verifies the source set and selected MII target rules. A separate research command reads that audited report, so the hospital database is not queried again. For each exact LOINC/edition it retrieves actual page and study evidence; exactly verified alternative codes can support unmapped LIS sets.

```sh
uv run --no-sync research-evidence --config pipeline.toml --report output/laboratory-review.csv --plan
uv run --no-sync --env-file .env research-evidence --config pipeline.toml --report output/laboratory-review.csv
```

Use `--scope lookup` for AI-assisted terminology recommendations without literature retrieval, or `--scope studies` to add the configured study APIs. Omit it to use TOML `research.scope`. Both modes use the same local model and independent code/unit guards. `--plan` respects the selected scope: lookup mode shows no study queries or study extraction prompts.

Without `--out`, lookup mode produces `<checked-name>.recommendations.csv/html`; studies mode produces `<checked-name>.research.csv/html`. Each has its own audit and trace. For a direct comparison, start both modes from the original checked report, not from the other AI output.

No second configuration is needed. The command selects the checked CSV at TOML `output.path` by default; `--report` is optional. A raw export or an edited CSV must first be checked, and its matching summary/evidence/audit files must remain together. [One-time model setup and exact Docker/Podman/Python commands](shipped-stack.md#ollama).

The plan prints the system/task prompts, profile routing and planned queries without network/model calls. The executed dossier uses the selected output prefix: CSV, HTML, evidence JSONL, summary, trace JSONL and artifact audit. Original check statuses/readiness are retained; the dossier adds review evidence.

`study_evidence_json` contains all retrieved evidence candidates. `reported_measurements_json` contains reported values or units; `reference_candidates_json` contains only reference intervals, analytical intervals and documented extremes. `matched_analyte` comes from the exact LOINC component; `suggested_analyte` and `suggested_name` are conditional target definitions. An absent or misspelled source name can receive a canonical terminology name even without a model inventing a new label. Empty fields mean that no supported extraction was obtained. Retrieved articles remain visible even when extraction fails or abstains. `extraction_unavailable` means no supported extraction was obtained from the local model; a study discovery has no implied numeric interval. Model availability and rejected responses remain visible in the trace.

## What is searched and prompted

The application performs the searches; the local model receives retrieved text and a bounded task. It cannot invent a code, factor, interval or citation and mark it verified. Model query spelling/translation phrases are hypotheses until actual retrieval/exact lookup supports them.

| Task | Example input to the task | Guard / output |
| --- | --- | --- |
| Search spelling | `analyte_name="Hämoglobim"` | At most two lexical query hypotheses; retrieve them through configured sources. |
| LOINC/name selection | Input name/unit + at most five exactly verified candidate codes, full sourced names, axes and unit fit | Select only compatible shortlist codes/names, or abstain. Unit compatibility alone is not analyte identity. |
| Unit spelling | Source spelling + validated catalogue shortlist | Select a literal candidate or abstain; preserve meaningful prefixes, powers and denominators. |
| Study measurements | Real abstract + verified analyte/LOINC | Quote reported values/units; distinguish individual values, summaries, unit-only and unspecified reports. |
| Study intervals | Real abstract/page excerpt | Quote a reference interval, analytical interval or documented extreme only when the text supports that type. A query category alone does not classify an interval. |

For hemoglobin, the generated searches include `(Hemoglobin) AND ("reference interval" OR "reference range")`, analogous analytical/extreme searches and `(Hemoglobin) AND (measurement OR concentration OR units)`. LOINC display brackets are removed from search syntax. PubMed and [Europe PMC](https://europepmc.org/RestfulWebService) are explicitly selectable in `research.sources`; `max_articles_per_loinc=3` caps total unique articles across providers, with small per-query limits. Results are cached per code/edition. This bounded discovery is not a systematic review or evidence-quality grading.

`*.trace.jsonl` records actual query parameters, model instructions, supplied evidence, guarded replies and rejected/unavailable attempts. The HTML/summary distinguishes retrieval requests, literature searches and extraction failures; extraction failures do not count as additional searches. Credentials are omitted. Hospital values/reference bounds and patient IDs are compared locally and stay out of queries/prompts; published measurements in retrieved abstracts are evidence inputs. Source hashes and retrieval times accompany the dossier.

LOINC axes returned as `LP…` part codes are resolved through exact `$lookup` requests in the same edition. Unresolved core axes prevent a model-selectable coding proposal. The primary service's bounded name `$expand` supplies candidates; each candidate still needs exact code/edition lookup and independent unit/context checks. A component-only label such as “Hemoglobin” is identified as `component_label`, not as the complete LOINC Coding display.

## Europe PMC and recommendation sources

Use the same `pipeline.toml` as the check. Edit its existing research tables:

```toml
[research]
enabled = false # research-evidence activates research explicitly
scope = "studies"
sources = ["europe_pmc", "pubmed"]
max_articles_per_loinc = 3
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.europe_pmc]
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_RESULTS = 1
# Optional public search restriction; applied to Europe PMC only and recorded in the trace:
# QUERY_FILTER = '(human OR humans OR patients OR volunteers) NOT (feline OR cats OR canine OR dogs OR rats OR mice OR veterinary)'
```

Europe PMC's public search needs no API key. The current adapter reads metadata/abstracts using `resultType=core`; full-text corpus download and systematic evidence grading are separate work. Article title, DOI when supplied, URL, retrieved-response hash and time come from the provider, not the model. Increase `MAX_RESULTS` and the unique-article cap deliberately; a one-result query can miss the useful study. A search restriction reduces irrelevant hits but does not establish a study's population or applicability.

Extraction uses the sourced analyte definition and source-constrained sentence choices. HTML is removed and abstract whitespace is normalised to plain spaces; the original API response hash is retained. Units/numbers must occur in the selected sentence, which must explicitly name the LOINC component. This conservative rule can miss abbreviations or intervals requiring several sentences. Context fields are literal source sentences or empty, not inferred clinical classifications. Missing numeric intervals stay missing; quotation/schema failures appear as `guard_diagnostic`. Rejected content does not disable independent tasks; three consecutive transport/HTTP failures open the model service circuit.

The HTML table reads **source set → linked terminology → complete proposal**. `source_data_json`, `linked_data_json` and `recommendation_set_json` carry the same data in CSV. The complete set includes code/system/edition, LOINC component/analyte and axes, canonical name, source values, proposed unit, transformation operations for every bound, all original reference contexts and candidate study intervals. A sourced canonical name is conditional on the chosen code; it is not proof of the measured analyte.

The HTML lists **supporting sources beside each proposal**: exact coding lookup and edition, name/translation source, unit catalogue/verification source, or the article and quoted excerpt for an evidence candidate. Missing source information is explicitly labelled for review. API/file checks, model selection and clinician adjudication remain distinct. `study_evidence_json`, `reference_candidates_json`, the evidence JSONL and trace retain the detailed record.

Your downloaded BfArM CodeSystem supplements can be read from sibling `../input/terminologies/bfarm-lab/` or ignored `local/terminologies/bfarm-lab/`. The report records the actual file hash and published canonical URL beside sourced labels. Enable `lookups.bfarm_lab`; retain primary LOINC/UCUM validation. The LOINC 2.83 German supplement applies to base edition 2.83; it is not silently combined with another edition. [Local BfArM configuration and credentials](lookups.md#existing-bfarm-files).

## Unmapped LIS example

The supplied [six illustrative cases](../examples/laboratory-research.csv) include local `LIS-HB` and unit code `GDL`, with display `g/dL`. Their local code/unit membership remains unknown until you provide your **real** source catalogue. A validated UCUM display can support a candidate, but does not establish that `GDL` means the same quantity or supply a verified source conversion.

The whole set is retained: name, code/system/edition, values/bounds, unit/system, every reference interval, specimen/method and qualifiers. `source_code_check`, `source_unit_check`, `source_code_unit_fit` and `source_reference_check_json` describe source checks. Target-profile checks and `fhir_mapping_proposal_json` describe what would need filling in FHIR. A local valid code can coexist with a need for a preferred LOINC coding and UCUM unit.

The eight-row starter maps `analyte_name="source_test"`: this example's `source_test` column actually contains “Hemoglobin” and “Leukocytes”. For your own export, map the column containing the actual analyte name. To try name/code mismatch cases, use `examples/laboratory-research.csv` and replace that mapping with `analyte_name="analyte_name"`; add `method="method"` to the existing `[input.columns]` table. Set a fresh output prefix, check it, then run research on that report.

A malformed `g/dll` can yield validated spelling candidates. A bare `10*6` cannot establish a cell-count denominator. A high value does not justify changing a unit or widening a reference interval. Age/sex, specimen, method, platform, calibration and preanalytical/population context govern clinical applicability.

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

The registry retains history and explicit supersession. It changes mapping rules, not model weights. Hashes verify integrity; the institution remains responsible for who is authorised to approve decisions. Apply approved plans in ETL and run the full FHIR validator on actual individual resources.

Imports require the same profile rules as the checked report. A later decision must have a later timestamp than the one it supersedes, so an old approval cannot undo a revocation. Reports made before this workflow need a new check before review import.

## Future domains and FDPG

Medication needs product/ingredient, strength, dose/unit, route and timing; Condition needs edition, certainty/status, severity and site; Procedure needs edition, status, time, site and technique. Medication terminology and deterministic dose checks now use a [separate adapter](../examples/medication/README.md). Medication AI/evidence research, Condition and Procedure remain unimplemented; laboratory research cannot be applied to these records.

A later FDPG provider must use an authorised interface or permitted aggregate export with selection/cohort provenance. Feasibility counts alone cannot reconstruct value distributions or establish reference intervals. Evidence of practice supports final clinician evaluation.

## Real service tests

```sh
SQC_LIVE_TESTS=1 SQC_LIVE_RESEARCH_TESTS=1 uv run --no-sync python -m unittest discover -s tests -v
```

For a running local model add `SQC_LIVE_MODEL_TESTS=1`, `SQC_TEST_MODEL_BASE_URL=http://127.0.0.1:11434/v1` and `SQC_TEST_MODEL_NAME=ministral-3:3b`. Model-specific proposal accuracy requires clinician adjudication beyond these functional source/guard tests. [Docker/Podman/model setup](shipped-stack.md).
