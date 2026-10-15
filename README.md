# Semantic plausibility

Review **laboratory exports** locally: source data → real terminology evidence → findings and sourced proposals. The Python library writes CSV and HTML; input records remain unchanged. A laboratory physician decides semantic correctness.

## Which data do you have?

| Your data | Choose this workflow | What you can check today |
| --- | --- | --- |
| Numeric laboratory results or their aggregate statistics | **Quantitative laboratory Observation** → [laboratory examples](examples/lab/README.md) | Codes, names, units, numbers, reference context and statistics; optional local AI, literature and expert feedback |
| A medicine/product description | **Medication** → [product example](examples/medication/README.md#choose-your-data) | Product/ingredient codes, units and source-declared strength |
| A prescription/order | **MedicationRequest** → [request example](examples/medication/README.md#first-medication-test) | Terminology, structured dosage/timing and conditional strength calculations |
| A documented administration | **MedicationAdministration** → [administration example](examples/medication/README.md#choose-your-data) | The above, plus rate × duration when constant delivery is explicitly asserted |
| Reported medication use | **MedicationStatement** → [statement example](examples/medication/README.md#choose-your-data) | Terminology and dosage structure; a statement is not proof of administration |

**Laboratory is the primary workflow. Medication A1 is available for technical terminology and quantity review; further medication development is deferred.** Medication AI, evidence search, clinical dose assessment and review-registry reuse are not implemented. Other Observation domains (such as vital signs or qualitative findings), diagnoses and procedures have no general plausibility workflow here.

For a first run, follow the laboratory test below. It uses supplied synthetic input and real APIs; no BfArM download or model is needed. Then choose your input recipe and edit its mappings.

## Four configuration choices

| Category | Start here | Other supported options |
| --- | --- | --- |
| **A · Check level** | A1 lookup and deterministic checks | A2 local AI recommendations; A3 local AI + Europe PMC/PubMed |
| **B · Input** | CSV of distinct laboratory sets | Individual CSV, SQLite/PostgreSQL view, FHIR JSON/Bundle/NDJSON/resource CSV |
| **C · Sources** | Real LOINC and NLM UCUM APIs | Manually downloaded BfArM/LOINC data; own terminology server |
| **D · Domain/profile** | Observation / MII KDS Laboratory 2026.0.3 | Base FHIR R4; medication A1 checks with the limits above |

**Current focus: quantitative laboratory values, units, names, codes and reference context.** Full FHIR validation is a separate switch for complete resources. Direct hospital-server extraction remains upstream (for example TORCH/CQL); this runner reads the export or an explicitly mapped database view.

[Step-by-step manual](docs/index.html) · [Working laboratory input recipes](examples/lab/README.md) · [TOML reference](docs/configuration.md)

## First test — Podman

Install Podman and `podman-compose`; on macOS/Windows start your Podman machine. In the repository:

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
chmod 600 .env
mkdir -p ../input local output
podman-compose build run-plausibility-check
podman-compose run --rm run-plausibility-check config-check
podman-compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

Open **`output/01-lookup.html`**. The unchanged starter reads **`examples/laboratory.csv`: 8 synthetic input rows → 7 distinct sets / 84 source occurrences**. All terminology responses come from real services. **A successful test produces those seven rows and their CSV/HTML, even though the deliberately problematic examples contain `error` and `unclear` findings.** The contradictory reference bounds are an intended error case. `unknown` means validity was not established; inspect source coverage or connection errors. It is different from a failed command that produces no completed report.

An existing `pipeline.toml` retains its settings. `config-check` checks configuration/files; `--connections` also contacts selected services. Choose a fresh output name for another run. Build only after code/dependency changes; the image, downloaded weights and named volumes are reused.

**Docker:** replace `podman-compose` with `docker compose` in all commands.

**Python:** same TOML, without containers:

```sh
uv sync --locked --no-editable --extra postgres
uv run --no-sync --env-file .env run-plausibility-check config-check
uv run --no-sync --env-file .env run-plausibility-check check --out output/01-lookup.csv
```

Direct script: `uv run --no-sync --env-file .env python run-plausibility-check.py --config pipeline.toml`. PostgreSQL support is optional for native Python. No shell `export` commands are required.

## Read the result before adding AI

- **Terminology/semantic findings:** `passed` means the implemented checks found no issue. Laboratory `suggestion` and `unclear`, or medication `needs_review`, require review; `error` records a supported inconsistency.
- **Full FHIR validation:** separate structural/profile result for complete resources. It does not run on scalar CSV summaries.
- **Clinical assessment:** remains with the physician. A consistent calculation or a valid code is not clinical approval.

[Status definitions, including medication `dose_check`](docs/data-contract.md#status-meanings-across-workflows). For medication, read the plain-language item summary before its technical evidence.

## A · Add local AI, then study evidence

| Level | Work performed | Output |
| --- | --- | --- |
| A1 | Exact membership/syntax, code–unit fit, numeric/reference consistency, supported transformations | Checked CSV/HTML + evidence/audit |
| A2 | A1 report → real candidate lookups → local model → exact guards | Code, analyte/name and unit proposals |
| A3 | A1 report → A2 tasks + bounded article retrieval/extraction | Sourced statistics/intervals for clinician review |

A2 and A3 both start from the **original A1 report and sidecars**. They do not overwrite source data or turn a published interval into an approved local reference interval.

The starter uses local Ollama **`ministral-3:3b`**. Provision once:

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm model-init
podman-compose --profile research run --rm catalog-init

# A2: no study search
podman-compose --profile research run --rm research-evidence research-evidence --scope lookup --report output/01-lookup.csv --out output/02-ai-lookup.csv

# A3: Europe PMC + PubMed
podman-compose --profile research run --rm research-evidence research-evidence --scope studies --report output/01-lookup.csv --out output/03-ai-evidence.csv
```

Add `--plan` to inspect prompts and queries first. Research logs the group, article URL, task, elapsed model time every 15 seconds, and acceptance/rejection. CPU inference can take minutes per article. One extraction request covers that article's statistics and intervals using short source-sentence IDs; exact quotations are restored locally; results are reused per LOINC/edition. The starter limits retrieval to four distinct articles per code, not a systematic review.

**Open `output/03-ai-evidence.html` and `output/03-ai-evidence.studies.csv`.** The study table contains actual quotations, units, statistic kind, source URL/DOI and extraction state. A retrieved article with no supported extraction is a discovery, not numeric evidence. The report counts these separately. [Research methods and limitations](docs/research.md).

## B · Input and statistics

Map your headers in `[input.columns]`; choose the input type/path or database table in `[input]`/`[database]`. The profile supplies target rules, not a mapping of unknown source columns.

For distinct lab exports, preserve **code + code system/edition + name + unit/system + reference ranges + specimen/method/context**. Optional summary columns are **count, mean, sample SD, min, max**, each statistic with its unit. The supplied [aggregate example](examples/lab/aggregated.csv) and [TOML](examples/lab/aggregated.toml) show the complete mapping.

- `output.mode="distinct"`: guarded semantic groups, known occurrence counts and available statistics.
- `output.mode="items"`: individual records with their IDs; not a way to recreate IDs from aggregates.
- Mean/SD pooling requires disjoint source groups and sufficient counts. SD means sample SD (`n−1`), not standard error. No median/IQR or reference interval is invented from summary data.
- Statistic units must match the source measurement unit. A verified linear conversion scales mean, SD, min/max and reference bounds together; a spelling hypothesis alone is not a conversion.

[Working CSV, SQLite, PostgreSQL and FHIR examples](examples/lab/README.md) · [Input contract](docs/data-contract.md).

## C · Sources and explicit downloads

| Source | How obtained | What it establishes |
| --- | --- | --- |
| LOINC | Public trial FHIR API by default; official API/account or local full catalogue configurable | Code, display, axes and documented example units |
| NLM UCUM | Live validation/conversion API | Syntax and dimensional conversion; not the correct analyte |
| BfArM German LOINC/UCUM supplements | **You download/extract them manually**, then configure their paths | German labels for the stated base edition; not a complete code catalogue or syntax engine |
| UCUM candidate catalogue | Explicit `catalog-init`: official UCUM definitions + published common expressions | Candidates for spelling review; UCUM has infinitely many expressions |
| Ollama model | Explicit `model-init`: configured Ollama model registry | Local inference; no terminology authority |
| MII/FHIR packages | Explicit `validator-init`: pinned package registry versions | Structure/binding rules for local Matchbox validation |
| Europe PMC / PubMed | APIs during A3; abstracts and metadata | Quoted reported measurements/intervals, with applicability still to review |

[Download origins, manual BfArM setup and optional local Blaze server](docs/index.html#terminology). Credentials belong in ignored `.env` or secret files; TOML holds their variable/file names. Public starter APIs need no credentials. A bearer-token setting is only needed for the selected authenticated service.

Blaze can run in the same Compose stack and retain its database. It serves built-in LOINC membership and supported installed CodeSystems/ValueSets. The pinned service's LOINC lookup lacks axes/example units, so the local-server recipe keeps a full LOINC API for those checks. BfArM translation supplements remain local label evidence; do not assume that uploading a supplement installs its base terminology. NLM remains the conversion API. Optional `package-check` checks release metadata without downloading archives or changing pinned versions.

## D · Scope and review

The active workflow is quantitative **laboratory Observation**, with MII KDS Laboratory 2026.0.3 or base FHIR R4. Full-resource validation uses local Matchbox/HL7 with pinned packages, before aggregation. Scalar summaries cannot prove full FHIR conformance. [Validation setup](docs/validation.md).

The report records configuration, input mapping, assumptions, methods and actual sources per item. Review source → linked definition → proposed code/name/unit/transform → quoted evidence. Confirm specimen, method, population and every reference range before approving. Out-of-range results alone do not prove a semantic error. [Clinician feedback](docs/research.md#clinician-feedback) · [Position alongside MII quality checks](docs/mii-quality.md).

**Reading the HTML:** One table per item: **Data field | Input value | Method + finding | Recommendation | Source → Proposal**. Source colours mean checked / unresolved / problem. Proposal colours mean **green: already correct within the stated check; yellow: review the proposal; red: no supported proposal found**. Grey means not applicable. Sources are numbered beside the findings; **Sources & details** expands the reasons, quotations and original fields. Laboratory and medication reports use the same layout. Green is a field result, not clinical approval; `Approved mapping ready` remains separate. [Examples and colour meanings](docs/data-contract.md#reading-the-recommendation).

“Approved mapping ready: 0 / N” means no reusable clinician-approved laboratory mapping was established. Expand **Details & sources** for field comparisons, quotations and provenance; **Configuration, methods & audit details** contains setup and scope. A retrieved paper without supported numerical extraction cannot supply a replacement reference range.

Medication A1 is available for technical terminology and quantity review; further medication development is deferred. [Choose a medication example, run it and interpret its open points](examples/medication/README.md).

## Run examples and inspect reports

```sh
# Four active laboratory scenarios: A1, full FHIR validation, A2, A3
bash tools/run-podman-examples.sh --lab-only

# All nine scenarios, including five medication examples
bash tools/run-podman-examples.sh
```

Add `--dry-run` for the plan or `--lookup-only` to omit model/validator setup. The script uses public example TOMLs, writes a new `output/podman-examples-…/index.html`, and stops its own services afterward. It retains images and volumes. Stop a manually started Ollama service first if it uses the same model volume:

```sh
podman-compose --profile research stop ollama
```

Each report has separate CSV/HTML, summary, evidence and audit; research also has `.studies.csv` and `.trace.jsonl`. `summary.tsv` reports process outcomes, not clinical correctness. Keep sidecars together. Logs support `--log-level DEBUG|INFO|WARNING|ERROR`; values/IDs are not sent to public searches.

## Architecture and decision flow

![Architecture](docs/architecture.svg)

![Decision flow](docs/decision-flow.svg)

[Input → grouping → report](docs/batch-flow.svg). No live message interception is implemented.

## Library and publishing

```python
from semantic_plausibility import run_plausibility_check
result = run_plausibility_check("pipeline.toml", output="output/review.csv")
```

Python 3.14; DuckDB 1.5.6; optional psycopg 3.3.6. Exact versions: `uv.lock`. [Implementation/tests](docs/implementation.md).

```sh
git status --short
git diff --check
git add -A
git diff --cached --stat
git diff --cached --check
git commit -m "Improve laboratory statistics, evidence transparency and input examples"
git push origin main
```

Inspect staged files. `.env`, private root TOMLs, `local/`, `output/`, caches and builds are ignored. Keep hospital exports and downloaded catalogues in `local/` or sibling `../input/`. Do not prune volumes or use `down --volumes` to reclaim image space. [Storage](docs/shipped-stack.md#storage-and-persistent-data).

Apache-2.0; separate terminology/model licences apply.
