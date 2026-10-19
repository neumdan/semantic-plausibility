# Semantic Plausibility

**Review laboratory exports as complete code–name–specimen–value–unit–reference sets.** Python reads CSV, FHIR or a database, groups equivalent measurements, checks real terminology, and writes CSV + an HTML review table. Optional local AI adds sourced proposals and study evidence. Clinicians approve mappings.

## Scope

| Domain | Current implementation | Operator status |
| --- | --- | --- |
| Laboratory `Observation` | CSV/FHIR/DB, grouping, terminology and whole-set checks, optional FHIR validation, A2/A3 research, five assessment scopes and clinician feedback | Current documented workflow |
| `Medication`, `MedicationRequest`, `MedicationAdministration`, `MedicationStatement` | Existing adapters, terminology, quantities, timing and conditional dose/rate arithmetic | Development checks with regression examples |

**Medication version 2** will build on those adapters: a consistent product–strength–dose–route–timing assessment, medication-specific AI/evidence, profile setup and clinician review. The four laboratory modes and five assessment scopes below apply to laboratory data. [Existing medication checks](examples/medication/README.md).

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
mkdir -p local/profiles local/terminologies/loinc local/terminologies/bfarm-lab local/secrets output ../input
```

Open **`pipeline.toml`**. The supplied input is `examples/laboratory.csv`. Choose your LOINC route:

| Route | What to do |
| --- | --- |
| **Official API** — default | Keep `lookups.loinc.source="loinc-api"`. Enter your LOINC account in `.env`: `LOINC_FHIR_USERNAME` and `LOINC_FHIR_PASSWORD`. [Register/access](https://loinc.org/kb/api/auth) |
| **Local release** | Download the full [LOINC CSV](https://loinc.org/downloads/), place `Loinc.csv` in `local/terminologies/loinc/`. Set `source="loinc-file"`, `version="YOUR_RELEASE"`, `complete=true` in the existing LOINC block. |

Keep `lookups.ucum.source="ucum-api"` for the public NLM service. To add your BfArM translations, extract them under `local/terminologies/bfarm-lab/` and enable `lookups.bfarm_lab`. The full base LOINC edition supplies membership and axes; the matching translation supplies German names. [All downloads, local servers and credentials](docs/lookups.md).

### Choose one runtime

**Podman** — on macOS/Windows, start your Podman machine first:

```sh
podman machine start
podman-compose build run-plausibility-check
podman-compose --profile setup run --rm profile-init
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

**Docker** — with Docker running:

```sh
docker compose build run-plausibility-check
docker compose --profile setup run --rm profile-init
docker compose run --rm run-plausibility-check config-check --connections
docker compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

**Python / uv** — Python and dependencies are selected by `uv.lock`:

```sh
uv sync --locked --extra postgres
uv run --no-sync --env-file .env run-plausibility-check profile-init
uv run --no-sync --env-file .env run-plausibility-check config-check --connections
uv run --no-sync --env-file .env run-plausibility-check check --out output/01-lookup.csv
```

`profile-init` retrieves the pinned MII Laboratory snapshot and selected HL7 binding into `local/profiles/`; repeat calls verify and reuse them. `config-check --connections` tests the selected sources. Open **`output/01-lookup.html`** and inspect each field's finding, proposal and source. Use a fresh `--out` name for the next run.

**Read the report:** each field shows its finding, condition, recommendation, short **Why**, and sources. **Conditional match** means, for example, “if the intended system is LOINC, `2823-3` is valid.” Matching name and compatible unit strengthen this finding: **code → code + name → code + name + unit**. These are evidence levels, not probabilities. Missing system URIs stay missing until confirmed; the actual specimen also needs source confirmation. A valid number is a format result, not clinical approval.

Each laboratory item also has five small assessment rows: **profile element QC; code/name/context; values/unit/system; code–quantity compatibility; whole set with reference ranges and evidence**. Each reports its result and completeness separately. A complete supplied set can contain errors; a conditional match can still lack metadata. [Column and status definitions](docs/data-contract.md#assessment-scopes).

## 2. Choose the input and method

| Input | Set in the same TOML | Example and mapping |
| --- | --- | --- |
| Individual CSV | `type="csv"`, `granularity="individual"`, `output.mode="distinct"` | [Individual values](examples/lab/README.md#individual-csv): three measurements → one set |
| Aggregate CSV | `type="csv"`, `granularity="distinct"` | [Aggregate values](examples/lab/README.md#aggregated-csv): numeric count/mean/SD/min/max and one common unit |
| FHIR files or server export | `type="fhir_file"`, `granularity="individual"` | [FHIR](examples/lab/README.md#fhir): JSON, Bundle, NDJSON; select server data upstream with TORCH/CQL |
| Database | `type="sqlite"` or `"postgres"`, table + column map | [SQLite / PostgreSQL](examples/lab/README.md#sqlite): read-only source access |

`output.mode="distinct"` groups equivalent semantic contexts for evidence research. `mode="items"` retains source IDs for A1 review; create a grouped A1 report before A2/A3. Units and reference contexts remain part of the group key. Mean, SD, min and max contain **numbers**, with their unit in the row's `unit_code`.

| Method | Command after setup | Result |
| --- | --- | --- |
| A1: lookup + consistency | `check` | Code/name, UCUM, code–unit fit, numeric/reference consistency |
| A2: AI-supported lookup | `research-evidence --scope lookup --report output/01-lookup.csv --out output/03-ai.csv` | Candidate corrections, sourced and awaiting review |
| A3: AI lookup + study evidence | `research-evidence --scope studies --report output/01-lookup.csv --out output/04-evidence.csv` | Relevant study quotations, measured values/intervals and applicability context |
| Full FHIR validation | `validation.enabled=true` for complete FHIR input | Separate Matchbox/HL7 conformance results before aggregation; [setup](docs/validation.md) |

### Local AI: select, download, run

Set **`models.local.model`** in TOML. The template selects `ministral-3:3b`. `base_url` is the native Python endpoint; `container_base_url` is the Compose endpoint.

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm model-init
podman-compose --profile research run --rm catalog-init
podman-compose --profile research run --rm research-evidence research-evidence --scope lookup --report output/01-lookup.csv --out output/03-ai.csv
podman-compose --profile research run --rm research-evidence research-evidence --scope studies --report output/01-lookup.csv --out output/04-evidence.csv
```

Docker uses the same commands with `docker compose` in place of `podman-compose`. Python uses `uv run --no-sync --env-file .env run-plausibility-check` followed by the same subcommands, with your local Ollama running. `model-init` downloads the selected model only if missing; weights persist in `semantic-plausibility-ollama`. `catalog-init` explicitly acquires the official UCUM definition/candidate files for AI spelling suggestions, stored in `local/`. [Own model / LM Studio](docs/shipped-stack.md#models).

### Optional Laya input assessment — experimental

In the existing `[decision]` block of `pipeline.toml`, set `enabled=true`. Laya adds **domain, declared unit-system and next-step suggestions** to each checked laboratory group. Ministral continues to perform A2/A3 research and extraction.

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
    A1["A1: Python runner + embedded DuckDB<br/>Preflight → optional validation → normalize → group → check<br/>Preserve code / system, name, specimen, values and units<br/>Check reference context and cross-field consistency"]
    TX["Terminology evidence<br/>Direct files OR selected APIs<br/>Optional Blaze: imported content<br/>UCUM: NLM API OR local service"]
    VALIDATE["Optional full FHIR validation<br/>Complete file / FHIR-in-CSV → Matchbox / HL7<br/>Pinned guide + local Blaze terminology; separate outcomes"]
    LAYA["Optional local Laya<br/>Advisory domain / next step<br/>Configured source is retained"]
    R1["A1: audited CSV + HTML<br/>Field decisions + five laboratory assessment scopes<br/>Evidence JSONL + summary + hash-chained audit"]
    MODEL["Ollama / own local API<br/>Candidate selection + extraction<br/>Bounded texts; retained weights"]
    RESEARCH["A2 / A3: Python research runner<br/>Read grouped A1 report and its verified sidecars<br/>Retrieve source text → local model → verify claims<br/>A3 includes AI lookup plus study extraction"]
    WEB["Selected source APIs / pages<br/>A2: terminology + LOINC pages<br/>A3: Europe PMC / PubMed<br/>Python retrieves source text"]
    OUT["CSV + HTML: clinical review<br/>Field → finding / condition → recommendation + why → sources<br/>Clinician approval → local registry; recheck targets and factors on later A1 runs"]
    INPUT --> A1
    CONFIG --> A1
    A1 --> |"lookup"| TX
    A1 -.-> |"if selected and applicable"| VALIDATE
    A1 -.-> |"advisory"| LAYA
    A1 --> |"check results"| R1
    VALIDATE --> |"outcomes"| R1
    LAYA -.-> |"hints"| R1
    R1 -.-> |"A2 / A3 selected"| RESEARCH
    R1 --> |"A1 review"| OUT
    RESEARCH -.-> |"inference"| MODEL
    RESEARCH --> |"retrieval"| WEB
    RESEARCH --> |"sourced proposals"| OUT
    classDef normal fill:#f3f7fa,stroke:#c4d5df,color:#142d42
    class INPUT,CONFIG,TX,VALIDATE,LAYA,MODEL,WEB normal
    classDef teal fill:#eef7f4,stroke:#a8cfc6,color:#142d42
    class A1,R1,RESEARCH,OUT teal
```
<!-- architecture:end -->

<!-- decision-flow:start -->
```mermaid
%% Generated with decision-flow.svg by tools/render_batch_diagrams.py.
flowchart TD
    INPUT["1. Configure and map laboratory input<br/>Code + system + name + specimen/method + value/statistics + unit/system + reference context<br/>Preflight checks mappings, types, paths, profile and source editions"]
    VALIDATION{"Validate complete FHIR?<br/>Enabled + file / FHIR-in-CSV"}
    FHIR["Validate with local Matchbox / HL7<br/>Installed profile + binding terminology<br/>Retain OperationOutcome; continue semantic checks<br/>Failed validation affects the configured exit status"]
    SKIP["Disabled / not applicable<br/>Scalar CSV/DB: not_applicable when selected<br/>DB JSON: export for complete-document validation"]
    GROUP["Aggregate or retain items<br/>distinct: group contexts; calculate / pool statistics<br/>items: retain source IDs for A1 review"]
    A1["2. A1: check the whole laboratory set<br/>Declared systems → lookup; missing systems → verify exact candidates<br/>Supported candidate → conditional match; source systems/specimen stay unchanged<br/>Check name/context, numeric quantities, code–unit fit and reference consistency"]
    REPORT["3. Persist the audited A1 report<br/>CSV + HTML + field decisions + five scope results / completeness / reasons<br/>Distinguish missing metadata, unverified findings and demonstrated errors"]
    METHOD{"4. Select the next command<br/>A2/A3 require audited distinct semantic sets"}
    A2["A2: research-evidence --scope lookup<br/>Read grouped A1 and sidecars; retrieve candidates<br/>Local model → check against actual terminology<br/>Record sources, proposals and review conditions"]
    A3["A3: research-evidence --scope studies<br/>Read the same grouped A1; include AI lookup<br/>Python retrieves studies; local model extracts<br/>Verify quote / analyte / number / unit / interval type"]
    REVIEW["5. Clinician review and scoped reuse<br/>Review field and whole-set findings, short reasons, source support and applicability<br/>A numeric value is a format check; study evidence needs clinical interpretation<br/>Approve / reject / defer in a review copy; recheck approved targets and factors on reuse"]
    INPUT --> VALIDATION
    VALIDATION --> |"yes"| FHIR
    VALIDATION --> |"no"| SKIP
    FHIR --> GROUP
    SKIP --> GROUP
    GROUP --> A1
    A1 --> REPORT
    REPORT --> METHOD
    METHOD -.-> |"lookup: grouped A1"| A2
    METHOD -.-> |"studies: grouped A1"| A3
    METHOD --> |"A1 only / items"| REVIEW
    A2 --> REVIEW
    A3 --> REVIEW
    classDef normal fill:#f3f7fa,stroke:#c4d5df,color:#142d42
    class INPUT,VALIDATION,FHIR,SKIP,GROUP,METHOD,A2,A3 normal
    classDef teal fill:#eef7f4,stroke:#a8cfc6,color:#142d42
    class A1,REPORT,REVIEW teal
```
<!-- decision-flow:end -->

Both diagrams are generated from the same definitions as the HTML manual. Python retrieves terminology/pages/studies and verifies model claims; Ollama performs local inference. A2 and A3 each read the original grouped A1 CSV and audited sidecars. A3 includes AI lookup and study extraction.

The runner is a one-shot batch command. Laya, Ollama, Blaze, UCUM and Matchbox are optional persistent services. Start Blaze only to serve your imported terminology or support FHIR validation. Direct file lookup reads local files without a server. [Containers and persistent storage](docs/shipped-stack.md).

## 4. Working examples and tests

```sh
bash tools/run-podman-examples.sh
```

This separate example suite uses `examples/lab/aggregated.csv`, explicitly selected **public test LOINC + NLM APIs**, real profile downloads, Matchbox with Blaze’s explicitly enabled built-in LOINC, and Ollama. It creates four separate report folders and prints the index path. Mode 02 uses a complete FHIR-in-CSV example; modes 03/04 reuse the grouped mode-01 report. [Inputs and expected results per mode](examples/README.md#four-laboratory-modes). Your private input/configuration stays selected only for your own runner commands. Preview with `--dry-run`; run A1 alone with `--lookup-only`. Cached images and volumes are reused.

For library tests, install the actual profile once, then:

```sh
uv run --no-sync run-plausibility-check profile-init --config pipeline.example.toml
uv run --no-sync python -m unittest discover -s tests -v
```

Tests cover grouping, numeric statistics, input validation, configuration, report/audit integrity and source routing. Live API/model/validator tests are explicit opt-ins; they use real services. [Test details and code responsibilities](docs/implementation.md).

**Manual:** [sources/downloads](docs/lookups.md) · [input recipes](examples/lab/README.md) · [TOML reference](docs/configuration.md) · [FHIR validation](docs/validation.md) · [research and quotations](docs/research.md) · [HTML manual](docs/index.html).
