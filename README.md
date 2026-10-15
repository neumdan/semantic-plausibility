# Semantic plausibility

Review laboratory and medication exports locally. The Python library writes a **CSV and HTML report: source data → linked terminology → findings and sourced proposals**. Clinicians review semantic correctness; input records remain unchanged.

## Choose four things

| Category | Choices | Configure / run |
| --- | --- | --- |
| **A. Plausibility level** | Lookup-only; AI-supported lookup; AI-supported evidence | `check`, then optionally `research-evidence --scope lookup` or `--scope studies` |
| **B. Input** | CSV; PostgreSQL/SQLite; FHIR files; resources exported from a FHIR server | `[input]`, column mapping, `[database]` |
| **C. Terminology sources** | Downloaded catalogues; APIs; a combination | `[lookups]`; credentials in `.env` or secret files |
| **D. Clinical domain** | Laboratory Observation; Medication, Request, Administration or Statement | `general.RESOURCE_TYPE`, `[specification]`; optional `[validation]` |

**Start with A1 + CSV + public APIs + laboratory Observation.** Then add your BfArM files, source mapping and local AI. Full FHIR validation is an independent option for complete resources; it is not another AI level.

[**Step-by-step manual: all four categories and every supplied profile**](docs/index.html) · [TOML field reference](docs/configuration.md)

## First test: Podman

Run inside the repository. Install Podman and `podman-compose`; on macOS/Windows initialise/start your Podman machine if needed. The following copy commands preserve existing settings:

```sh
cp -n pipeline.example.toml pipeline.toml
cp -n .env.example .env
chmod 600 .env
mkdir -p ../input local output
podman-compose build run-plausibility-check
podman-compose run --rm run-plausibility-check config-check
podman-compose run --rm run-plausibility-check check --out output/01-lookup.csv
```

Open **`output/01-lookup.html`**. With the unchanged example, expect **8 input rows → 7 distinct sets, representing 84 source occurrences**. Inputs are synthetic; terminology responses are real. A successful process can report errors or unresolved sets: those are findings, not necessarily a failed installation.

An existing `pipeline.toml` keeps its selected input and services. Inspect it before running. `config-check` checks configuration/files; `--connections` additionally contacts selected APIs; a model is probed only when research is enabled. No credentials are required by the fresh example's public endpoints. Secrets are required only by an API you explicitly select.

Build once after installation or code updates. Ordinary runs reuse `semantic-plausibility-stack:local`. Each check exits after writing its report. Use a fresh output name for another run.

<details>
<summary>Docker or Python instead</summary>

Docker uses the same files and commands; replace `podman-compose` with `docker compose`.

Python with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv sync --locked --no-editable
uv run --no-sync --env-file .env run-plausibility-check config-check
uv run --no-sync --env-file .env run-plausibility-check --out output/01-lookup.csv
```

The direct script is equivalent: `uv run --no-sync --env-file .env python run-plausibility-check.py --config pipeline.toml`. Add `--extra postgres` to `uv sync` for PostgreSQL. [Runtime details](docs/shipped-stack.md).

</details>

## A. Plausibility level

| Level | What happens | Availability |
| --- | --- | --- |
| **A1 — Lookup-only** | Exact code/unit lookup, names, numeric/context checks, code–unit fit and supported transformations | Labs and all four medication types |
| **A2 — AI-supported lookup** | A1 report → real terminology/name/unit candidates → local model → exact rechecks; no studies | Labs |
| **A3 — AI-supported evidence** | A1 report → same AI pass plus bounded Europe PMC/PubMed retrieval and cited extraction | Labs |

A1 already includes deterministic plausibility checks. A2/A3 are independent second passes on the **original A1 CSV and its sidecars**, not successive edits to source data. Lack of evidence stays explicit. Medication AI/evidence and clinical dose safety are not implemented.

### Prepare Ollama once, then choose A2 or A3

The starter is [Ministral 3B](https://ollama.com/library/ministral-3:3b), an Apache-2.0 model from Mistral. Allow approximately 3 GB for weights plus runtime memory. CPU inference in a macOS/Windows VM can take minutes; native Ollama is an optional faster local route on supported hardware.

```sh
podman-compose --profile research up -d ollama
podman-compose --profile research run --rm model-init
podman-compose --profile research run --rm catalog-init
podman-compose --profile research exec -T ollama ollama list

# A2: terminology/name/unit recommendations
podman-compose --profile research run --rm research-evidence research-evidence --scope lookup --report output/01-lookup.csv --out output/02-ai-lookup.csv

# A3: recommendations with study evidence
podman-compose --profile research run --rm research-evidence research-evidence --scope studies --report output/01-lookup.csv --out output/03-ai-evidence.csv
```

Open the HTML beside each CSV. Add `--plan` to either research command to inspect prompts/queries first. Model selection is `[models.local]`; evidence APIs are `[research]`. Europe PMC needs no key. **`model-init` checks/downloads weights; it does not itself prove inference quality.** [Detailed setup and troubleshooting](docs/index.html#model).

Weights persist in a shared named volume, also used by the example script. Set `SQC_OLLAMA_VOLUME` in `.env` to an existing volume if migrating; otherwise it defaults to `semantic-plausibility-ollama`. Run one Ollama service at a time against that store. `stop ollama` preserves it; `down --volumes` does not.

## B. Input

[Input recipes](docs/index.html#input): CSV, PostgreSQL/SQLite, FHIR JSON/Bundle/NDJSON or TORCH/CQL exports. Direct FHIR-server querying is upstream; this runner reads the resulting resources.

Map **your** CSV/DB column names in `[input.columns]`; choose the table/view in `[database]`. The IG supplies target rules, not a mapping of unknown source columns. `output.mode="distinct"` groups complete sets; `"items"` retains IDs from individual inputs. The current output is CSV/HTML, not a FHIR/OMOP/database writer.

Full FHIR validation requires complete resources and the pinned guide: [Matchbox/HL7 setup](docs/index.html#validation). Scalar lab aggregates cannot establish full FHIR conformance.

## C. Lookup and terminology sources

| Needed source | Download / local route | API route |
| --- | --- | --- |
| LOINC membership, names, axes, example units | Official full `Loinc.csv`, with actual edition | Official LOINC FHIR API + account; public test API for an initial trial |
| German laboratory names | BfArM LOINC/UCUM FHIR supplements | A terminology server hosting the corresponding edition |
| German ATC/DDD | BfArM `bfarm.terminologien.atcgm`, licensed edition | A server covering the German ATC canonical system |
| UCUM syntax and conversions | Previously captured real responses for the required queries | NLM UCUM service |
| SNOMED CT / RxNorm / UNII | Licensed national exports or actual published FHIR/CSV data | Configured FHIR terminology server; public trial covers selected international codes |
| German products / ingredients / forms | Authorised PZN, ASK, EDQM/CAS catalogues | Own server or [MII SU-TermServ with client certificate](docs/lookups.md#mii-client-certificates), according to coverage |
| Local LIS codes/units | Your actual CSV/SQLite/FHIR catalogue | Your terminology API |

[Download paths, credentials and exact TOML entries](docs/index.html#terminology) · [Full source inventory](docs/lookups.csv).

BfArM translations are **supplements**: they do not replace full LOINC membership or a UCUM grammar service. DDD is classification context, not a patient dose limit. Use `terminology.strategy="fallback"` for local-first routing; `"all"` cross-checks every **enabled source matching the actual system**. Missing coverage and disagreements remain visible.

## D. Clinical domain and target guide

| Data to review | Starter | What to preserve |
| --- | --- | --- |
| Laboratory Observation | `pipeline.example.toml`, MII Laboratory 2026.0.3 | Code/system/edition, analyte, value or observed bounds, unit/system, all reference ranges/context |
| Medication product | `examples/medication/product.toml` | Product/form/ingredients, strength, status; no dose required |
| MedicationRequest | `examples/medication/request.toml` | Product plus every prescribed dose/unit/route/timing instruction |
| MedicationAdministration | `examples/medication/administration.toml` | Product plus given dose/rate, actual duration, status |
| MedicationStatement | `examples/medication/statement.toml` | Product plus reported use/dosage/status; not evidence of administration |

Medication starters select MII Medication 2026.0.1. [Each profile's commands and limits](docs/index.html#profiles) include its native FHIR recipe. Quantitative laboratory Observations are supported; other Observation domains are not yet implemented.

The guide defines target elements and bindings; it does not supply API credentials or prove that an enabled server covers a system. Use the code's **actual system and edition**. For medication, ATC classifies; PZN identifies a German pack; SNOMED/UNII/ASK and selected RxNorm concepts identify ingredients; EDQM describes form/route; UCUM checks quantities. RxNorm is an optional international source, not a required MII binding. [Exact roles, codes and sources](examples/medication/README.md#terminology-walkthrough).

### Run the examples across domains

```sh
# Preview, then run A1 for lab + four medication types + a terminology showcase
bash tools/run-podman-examples.sh --lookup-only --dry-run
bash tools/run-podman-examples.sh --lookup-only

# Optional: also run lab FHIR validation, A2 and A3 (nine reports total)
bash tools/run-podman-examples.sh
```

Open the new `output/podman-examples-…/index.html`; every scenario has its own CSV/HTML and audit. The script uses **public example TOMLs**, not your private root configuration. It reuses images and persistent model volumes. Lookup-only needs neither Ollama nor Matchbox. Missing German/licensed data stays unknown; synthetic source data never supplies its own lookup evidence. [Single-example Python/Docker/Podman commands](docs/index.html#profiles).

## Read, audit and review

Every report starts with **Configuration and methods**: selected A–D choices, input mapping/defaults, target profile, configured sources, model/research settings and output. Credential values and connection strings are excluded.

Each row has **How this item was assessed**: source counts/IDs where retained, applied methods/results, assumptions and limitations, actual lookup evidence/editions, and the next improvement step. The CSV stores this in `assessment_context_json`; the summary stores the shared `run_context`. A configured service is not evidence that it was used. AI reports identify inherited A1 verdicts separately from newly researched proposals.

HTML shows source → linked data → proposal and citations beside each set. CSV is the full result; HTML previews at most 2,000 rows. Keep `.summary.json`, `.evidence.jsonl`, `.audit.jsonl` and any AI `.trace.jsonl` together. Logs support `--log-level DEBUG|INFO|WARNING|ERROR`; console progress omits credentials and clinical values.

```sh
podman-compose run --rm run-plausibility-check verify /app/output/01-lookup.audit.jsonl
```

[Report interpretation](docs/index.html#output) · [Clinician feedback](docs/research.md#clinician-feedback) · [Position alongside MII quality checks](docs/mii-quality.md).

## Architecture and decision flow

![Architecture](docs/architecture.svg)

![Decision flow](docs/decision-flow.svg)

[Input → grouping → report](docs/batch-flow.svg). The batch walkthrough above covers all implemented domains; live message interception remains future work. [Operation and storage](docs/shipped-stack.md#storage-and-persistent-data).

## Library, versions and publishing

```python
from semantic_plausibility import run_plausibility_check
result = run_plausibility_check("pipeline.toml", output="output/review.csv")
```

Python 3.14; DuckDB 1.5.6; optional psycopg 3.3.6. Exact dependencies: `uv.lock`. [Implementation and tests](docs/implementation.md).

```sh
git status --short
git diff --stat
git add -A
git diff --cached --stat
git diff --cached --check
git commit -m "Add domain examples and transparent assessment reports"
git push origin main
```

After pushing, test all **nine** shipped examples:

```sh
podman info
# If your manual Ollama service is running, stop it before the example stack uses the same model volume.
podman-compose --profile research stop ollama
bash tools/run-podman-examples.sh --dry-run
bash tools/run-podman-examples.sh
```

If `podman info` fails on macOS/Windows, start your existing machine with `podman machine start`. The script builds changed images once, prepares the pinned lab validator, reuses/downloads the configured Ollama model (`ministral-3:3b`) and runs literature retrieval with **Europe PMC and PubMed**. It uses the public example TOMLs, not private `pipeline.toml`. No API key is needed for those default public services. CPU inference can take many minutes.

Open the new `output/podman-examples-…/index.html`. Its `summary.tsv` records command outcomes; each scenario has a separate report directory. Inspect configuration, per-item evidence and model-call failures inside each report: “completed” does not mean every finding passed or every AI answer was accepted. The script stops its own services at the end and keeps images, model weights and volumes. Do not use volume pruning or `down --volumes` for this workflow.

Inspect staged files. `.env`, private root TOMLs, `local/`, `output/`, caches and builds are ignored. Store exports/downloads in `local/` or sibling `../input/`. If macOS reports “Operation canceled”/“mmap failed”, inspect the affected file with `ls -lO`; iCloud placeholders need **Keep Downloaded**, including `.git`. Prefer future clones outside synced Documents/Desktop.

Apache-2.0; separate terminology and model licences apply.
