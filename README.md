# Semantic Plausibility

**Review laboratory exports as complete code–name–specimen–value–unit–reference sets.** Python reads CSV, FHIR or a database, groups equivalent measurements, checks real terminology, and writes CSV + an HTML review table. Optional local AI adds sourced proposals and study evidence. Clinicians approve mappings.

## Scope

| Domain | Current implementation | Operator status |
| --- | --- | --- |
| Laboratory `Observation` | CSV/FHIR/DB, grouping, terminology and whole-set checks, optional FHIR validation, ai-lookup/ai-evidence research, five assessment scopes and clinician feedback | Current documented workflow |
| `Medication`, `MedicationRequest`, `MedicationAdministration`, `MedicationStatement` | Existing adapters, terminology, quantities, timing and conditional dose/rate arithmetic | Development checks with regression examples |

**Medication version 2** will build on those adapters: a consistent product–strength–dose–route–timing assessment, medication-specific AI/evidence, profile setup and clinician review. The laboratory workflows and five assessment scopes below apply to laboratory data. [Existing medication checks](examples/medication/README.md).

**Objective:** identify incomplete or conflicting laboratory sets and provide sourced proposals for clinician review. Terminology agreement, FHIR conformance and clinical plausibility are assessed separately. Clinical sensitivity and recommendation quality require expert evaluation. [Objectives and limitations](docs/workflow.md#objectives-and-limitations).

**Evaluate with clinicians:** `study-template` creates a blank review CSV from a grouped report; `study-metrics` calculates error sensitivity, recommendation acceptability and review effort. Both run locally from saved reports. [Lean study: template, labels and two commands](docs/studies.md).

## What you configure

| File / folder | Purpose |
| --- | --- |
| **`pipeline.toml`** | Your input columns, aggregation, target profile, selected sources, model and output |
| **`catalog.csv`** | Editable directory of services and download locations; selected by `source="ID"` in TOML |
| **`.env`** | Credentials and optional host mount/port settings |
| **`local/`** | Your downloaded terminology, profiles and secret files; ignored by Git |
| **`output/`** | Your reports and audit files; ignored by Git |
| **`compose.yaml`** | The single Docker/Podman stack; start only the services needed |

Terminology concepts, downloaded FHIR definitions and model weights are acquired during setup and kept in runtime storage. The repository contains code, the source directory and illustrative input examples.

## 1. First setup

Run these commands in the repository directory. Existing private files are preserved.

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
mkdir -p local/input local/data local/profiles local/terminologies/loinc local/terminologies/bfarm-lab local/secrets output ../input
```

Open **`pipeline.toml`**. The supplied input is `examples/laboratory.csv`. Choose your LOINC route:

| Route | What to do |
| --- | --- |
| **Official API** — default | Keep `lookups.loinc.source="loinc-api"`. Enter your LOINC account in `.env`: `LOINC_FHIR_USERNAME` and `LOINC_FHIR_PASSWORD`. [Register/access](https://loinc.org/kb/api/auth) |
| **Local release** | Download the full [LOINC CSV](https://loinc.org/downloads/) to `local/terminologies/loinc/Loinc.csv`. Enable `lookups.loinc_local`, enter the actual `version`, and disable `lookups.loinc` for file-only LOINC lookup. |
| **File + API fallback** | Enable both blocks with their file/credentials; keep `terminology.strategy="fallback"`. Local membership is checked first. Use `"all"` to compare all applicable membership sources. |

Keep `lookups.ucum.source="ucum-api"` for the public NLM service. To add your BfArM translations, extract them under `local/terminologies/bfarm-lab/` and enable `lookups.bfarm_lab`. The full base LOINC edition supplies membership and axes; the matching translation supplies German names. [All downloads, local servers and credentials](docs/lookups.md).

**Use your own export:** put it in `local/input/` or `local/data/`, set `input.path`, match its `delimiter`, and map the exact headers on the **right** of `input.columns`. Remove mappings for absent columns. Choose `granularity="individual"` for measurements or `"distinct"` for counts/statistics; `output.mode="distinct"` prepares both for research. The template explains these choices directly beside each setting. [Fill in your own export, step by step](docs/configuration.md#fill-in-your-own-export).

### Choose one runtime

**Podman** — on macOS/Windows, start your Podman machine first:

```sh
podman machine start
podman-compose build run-plausibility-check
podman-compose --profile setup run --rm profile-init
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check check --out output/lookup.csv
```

**Docker** — with Docker running:

```sh
docker compose build run-plausibility-check
docker compose --profile setup run --rm profile-init
docker compose run --rm run-plausibility-check config-check --connections
docker compose run --rm run-plausibility-check check --out output/lookup.csv
```

**Python / uv** — Python and dependencies are selected by `uv.lock`:

```sh
uv sync --locked --extra postgres
uv run --no-sync --env-file .env run-plausibility-check profile-init
uv run --no-sync --env-file .env run-plausibility-check config-check --connections
uv run --no-sync --env-file .env run-plausibility-check check --out output/lookup.csv
```

`profile-init` retrieves the pinned MII Laboratory snapshot and selected HL7 binding into `local/profiles/`; repeat calls verify and reuse them. `config-check --connections` tests the selected sources. Open **`output/lookup.html`** and inspect each field's finding, proposal and source. Use a fresh `--out` name for the next run.

**Read the report:** each field shows its finding, condition, recommendation, short **Why**, and sources. **Conditional match** means, for example, “if the intended system is LOINC, `2823-3` is valid.” Matching name and compatible unit strengthen this finding: **code → code + name → code + name + unit**. These are evidence levels, not probabilities. Missing system URIs stay missing until confirmed; the actual specimen also needs source confirmation. A valid number is a format result, not clinical approval.

Each laboratory item also has five small assessment rows: **profile element QC; code/name/context; values/unit/system; code–quantity compatibility; whole set with reference ranges and evidence**. Each reports its result and completeness separately. A complete supplied set can contain errors; a conditional match can still lack metadata. [Column and status definitions](docs/data-contract.md#assessment-scopes).

## 2. Choose the input and method

| Input | Set in the same TOML | Example and mapping |
| --- | --- | --- |
| Individual CSV | `type="csv"`, `granularity="individual"`, `output.mode="distinct"` | [Individual values](examples/lab/README.md#individual-csv): three measurements → one set |
| Aggregate CSV | `type="csv"`, `granularity="distinct"` | [Aggregate values](examples/lab/README.md#aggregated-csv): numeric count/mean/SD/min/max and one common unit |
| FHIR files or server export | `type="fhir_file"`, `granularity="individual"` | [FHIR](examples/lab/README.md#fhir): JSON, Bundle, NDJSON; select server data upstream with TORCH/CQL |
| Database | `type="sqlite"` or `"postgres"`, table + column map | [SQLite / PostgreSQL](examples/lab/README.md#sqlite): read-only source access |

`output.mode="distinct"` groups equivalent semantic contexts for evidence research. `mode="items"` retains source IDs for lookup review; create a grouped lookup report before ai-lookup/ai-evidence. Units and reference contexts remain part of the group key. Mean, SD, min and max contain **numbers**, with their unit in the row's `unit_code`.

| Method | Command after setup | Result |
| --- | --- | --- |
| `lookup`: terminology + consistency | `check` | Code/name, UCUM, code–unit fit, numeric/reference consistency |
| `ai-lookup`: AI-supported lookup | `research-evidence --scope lookup --report output/lookup.csv --out output/ai-lookup.csv` | Candidate corrections, sourced and awaiting review |
| `ai-evidence`: AI lookup + study evidence | `research-evidence --scope studies --report output/lookup.csv --out output/ai-evidence.csv` | Relevant study quotations, measured values/intervals and applicability context |
| `fhir-validation` | `validation.enabled=true` for complete FHIR input | Separate Matchbox/HL7 conformance results before aggregation; [setup](docs/validation.md) |

Europe PMC evidence includes permitted open-access paragraphs and labelled table cells (`research.europe_pmc.FULL_TEXT=true`). Reports compare your statistics and reference bounds with source-checked study numbers, retaining context and source locations. [Small evidence pipeline and a real potassium example](docs/research.md#small-evidence-pipeline-values-and-reference-intervals).

### Local AI: select, download, run

Set **`models.local.model`** in TOML. The template selects `ministral-3:3b`. `base_url` is the native Python endpoint; `container_base_url` is the Compose endpoint.

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm model-init
podman-compose --profile research run --rm catalog-init
podman-compose --profile research run --rm research-evidence research-evidence --scope lookup --report output/lookup.csv --out output/ai-lookup.csv
podman-compose --profile research run --rm research-evidence research-evidence --scope studies --report output/lookup.csv --out output/ai-evidence.csv
```

Docker uses the same commands with `docker compose` in place of `podman-compose`. Python uses `uv run --no-sync --env-file .env run-plausibility-check` followed by the same subcommands, with your local Ollama running. `model-init` downloads the selected model only if missing; weights persist in `semantic-plausibility-ollama`. `catalog-init` explicitly acquires the official UCUM definition/candidate files for AI spelling suggestions, stored in `local/`. [Own model / LM Studio](docs/shipped-stack.md#models).

### Optional Laya input assessment — experimental

In the existing `[decision]` block of `pipeline.toml`, set `enabled=true`. Laya adds **domain, declared unit-system and next-step suggestions** to each checked laboratory group. Ministral continues to perform ai-lookup/ai-evidence research and extraction.

```sh
mkdir -p local/laya
podman-compose --profile decision up -d --build laya
podman-compose --profile decision logs -f laya
```

When the service reports it is ready, leave the log view with Ctrl+C and run:

```sh
podman-compose run --rm run-plausibility-check check --out output/laya-laboratory.csv
```

Open `output/laya-laboratory.html`: **Input decision** shows the model's choices, scores and abstentions. The exact request/response and pinned model revision are in `*.evidence.jsonl`; `decision.*` CSV columns retain individual scores. Docker uses `docker compose` instead. [Python setup and settings](docs/configuration.md#optional-laya-decisions).

FHIR `resourceType` identifies the resource. CSV/DB columns are mapped explicitly. Declare hospital origin with `input.origin="lis"` or `"cpoe"`; similar content can originate in either system. The initial laboratory evaluation produced uncertain or incorrect domain choices; keep automatic routing disabled. Laya's uncalibrated scores are review hints and retain the configured source, terminology results and chosen research scope.

## 3. Understand the stack and decisions

<!-- architecture:start -->
```mermaid
%% Generated with architecture.svg by tools/render_batch_diagrams.py.
flowchart TD
    INPUT["Selected laboratory input<br/>CSV / FHIR export / read-only SQLite or PostgreSQL<br/>TORCH / CQL selects server exports upstream<br/>Mapped columns or native FHIR fields"]
    CONFIG["One pipeline.toml + catalog.csv<br/>Mapping, grouping, profile, sources and model<br/>Downloaded profiles / terminology under local/<br/>Credentials in .env or secret files"]
    LOOKUP["lookup: Python + embedded DuckDB<br/>Preflight → optional validation → normalize → group → check<br/>Preserve code / system, name, specimen, values and units<br/>Check reference context and cross-field consistency"]
    TX["Terminology evidence<br/>Files + APIs: fallback or all<br/>Optional Blaze: imported content<br/>UCUM: NLM API OR local service"]
    VALIDATE["Optional fhir-validation<br/>Complete file / FHIR-in-CSV → Matchbox / HL7<br/>Pinned guide + local Blaze terminology; separate outcomes"]
    LAYA["Optional local Laya<br/>Advisory domain / next step<br/>Configured source is retained"]
    R1["lookup: audited CSV + HTML<br/>Field decisions + five laboratory assessment scopes<br/>Evidence JSONL + summary + hash-chained audit"]
    MODEL["Ollama / own local API<br/>Candidate selection + extraction<br/>Bounded texts; retained weights"]
    RESEARCH["ai-lookup / ai-evidence: Python research<br/>Read grouped lookup report and its verified sidecars<br/>Text/tables → parser or local model → source guards<br/>ai-evidence includes AI lookup plus study extraction"]
    WEB["Selected source APIs / pages<br/>ai-lookup: terminology / pages<br/>ai-evidence: literature APIs<br/>Python retrieves source text"]
    OUT["CSV + HTML: clinical review<br/>Field → finding / condition → recommendation + why → sources<br/>Clinician approval → local registry; recheck targets and factors on later lookup runs"]
    INPUT --> LOOKUP
    CONFIG --> LOOKUP
    LOOKUP --> |"lookup"| TX
    LOOKUP -.-> |"if selected and applicable"| VALIDATE
    LOOKUP -.-> |"advisory"| LAYA
    LOOKUP --> |"check results"| R1
    VALIDATE --> |"outcomes"| R1
    LAYA -.-> |"hints"| R1
    R1 -.-> |"AI research selected"| RESEARCH
    R1 --> |"lookup review"| OUT
    RESEARCH -.-> |"inference"| MODEL
    RESEARCH --> |"retrieval"| WEB
    RESEARCH --> |"sourced proposals"| OUT
    classDef normal fill:#f3f7fa,stroke:#c4d5df,color:#142d42
    class INPUT,CONFIG,TX,VALIDATE,LAYA,MODEL,WEB normal
    classDef teal fill:#eef7f4,stroke:#a8cfc6,color:#142d42
    class LOOKUP,R1,RESEARCH,OUT teal
```
<!-- architecture:end -->

<!-- decision-flow:start -->
```mermaid
%% Generated with decision-flow.svg by tools/render_batch_diagrams.py.
flowchart TD
    INPUT["Configure and map laboratory input<br/>Code + system + name + specimen/method + value/statistics + unit/system + reference context<br/>Preflight checks mappings, types, paths, profile and source editions"]
    VALIDATION{"Validate complete FHIR?<br/>Enabled + file / FHIR-in-CSV"}
    FHIR["fhir-validation: Matchbox / HL7<br/>Installed profile + binding terminology<br/>Retain OperationOutcome; continue semantic checks<br/>Failed validation affects the configured exit status"]
    SKIP["Disabled / not applicable<br/>Scalar CSV/DB: not_applicable when selected<br/>DB JSON: export for complete-document validation"]
    GROUP["Aggregate or retain items<br/>distinct: group contexts; calculate / pool statistics<br/>items: retain source IDs for lookup review"]
    LOOKUP["lookup: check the whole laboratory set<br/>Declared systems → lookup; missing systems → verify exact candidates<br/>Supported candidate → conditional match; source systems/specimen stay unchanged<br/>Check name/context, numeric quantities, code–unit fit and reference consistency"]
    REPORT["Persist the audited lookup report<br/>CSV + HTML + field decisions + five scope results / completeness / reasons<br/>Distinguish missing metadata, unverified findings and demonstrated errors"]
    METHOD{"Select the research command<br/>Research commands require audited distinct semantic sets"}
    AI_LOOKUP["ai-lookup: --scope lookup<br/>Read grouped lookup and sidecars; retrieve candidates<br/>Local model → check against actual terminology<br/>Record sources, proposals and review conditions"]
    AI_EVIDENCE["ai-evidence: --scope studies<br/>Read the same grouped lookup; include AI lookup<br/>Open-access text/tables; guarded extraction<br/>Verify claims; compare values/bounds locally"]
    REVIEW["Clinician review and scoped reuse<br/>Review field and whole-set findings, short reasons, source support and applicability<br/>A numeric value is a format check; study evidence needs clinical interpretation<br/>Approve / reject / defer in a review copy; recheck approved targets and factors on reuse"]
    INPUT --> VALIDATION
    VALIDATION --> |"yes"| FHIR
    VALIDATION --> |"no"| SKIP
    FHIR --> GROUP
    SKIP --> GROUP
    GROUP --> LOOKUP
    LOOKUP --> REPORT
    REPORT --> METHOD
    METHOD -.-> |"lookup: grouped lookup"| AI_LOOKUP
    METHOD -.-> |"studies: grouped lookup"| AI_EVIDENCE
    METHOD --> |"lookup only / items"| REVIEW
    AI_LOOKUP --> REVIEW
    AI_EVIDENCE --> REVIEW
    classDef normal fill:#f3f7fa,stroke:#c4d5df,color:#142d42
    class INPUT,VALIDATION,FHIR,SKIP,GROUP,METHOD,AI_LOOKUP,AI_EVIDENCE normal
    classDef teal fill:#eef7f4,stroke:#a8cfc6,color:#142d42
    class LOOKUP,REPORT,REVIEW teal
```
<!-- decision-flow:end -->

Both diagrams are generated from the same definitions as the HTML manual. Python retrieves terminology/pages/studies and verifies model claims; Ollama performs local inference. ai-lookup and ai-evidence each read the original grouped lookup CSV and audited sidecars. ai-evidence includes AI lookup and study extraction.

The runner is a one-shot batch command. Laya, Ollama, Blaze, UCUM and Matchbox are optional persistent services. Start Blaze only to serve your imported terminology or support FHIR validation. Direct file lookup reads local files without a server. [Containers and persistent storage](docs/shipped-stack.md).

## 4. Working examples and tests

```sh
bash tools/run-podman-examples.sh
```

This separate example suite uses `examples/lab/aggregated.csv`, explicitly selected **public test LOINC + NLM APIs**, real profile downloads, Matchbox with Blaze’s explicitly enabled built-in LOINC, and Ollama. It creates four separate report folders and prints the index path. `fhir-validation` uses a complete FHIR-in-CSV example; `ai-lookup` and `ai-evidence` reuse the grouped `lookup` report. [Inputs and expected results per workflow](examples/README.md#laboratory-workflows). Your private input/configuration stays selected only for your own runner commands. Preview with `--dry-run`; run lookup alone with `--lookup-only`. Cached images and volumes are reused.

For library tests, install the actual profile once, then:

```sh
uv run --no-sync run-plausibility-check profile-init --config pipeline.example.toml
uv run --no-sync python -m unittest discover -s tests -v
```

Tests cover grouping, numeric statistics, input validation, configuration, report/audit integrity and source routing. Live API/model/validator tests are explicit opt-ins; they use real services. [Test details and code responsibilities](docs/implementation.md).

**Manual:** [sources/downloads](docs/lookups.md) · [input recipes](examples/lab/README.md) · [TOML reference](docs/configuration.md) · [FHIR validation](docs/validation.md) · [research and quotations](docs/research.md) · [HTML manual](docs/index.html).
