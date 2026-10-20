# Terminology sources: choose, provide, check

**`catalog.csv` is the only source directory.** It contains service URLs, download pages, adapter types and default paths. Your `pipeline.toml` selects sources by ID. The selected source's endpoint/path appears in the log; artifact hashes and actual lookup evidence appear in the report.

## 1. Choose how to look up

| Route | How it works | Services to start |
| --- | --- | --- |
| **API** | Python sends terminology code/name queries to the selected endpoint | The one-shot runner; an existing public/institutional API |
| **Files** | Python indexes your extracted FHIR JSON or full LOINC CSV in memory and reads it directly | The runner only |
| **Local server** | An explicit import loads your full CodeSystems and ValueSets into persistent Blaze storage; clients query its FHIR API | `blaze`, plus `terminology-init` once per selected edition |
| **Local UCUM** | The optional UCUM container uses the official NLM grammar/conversion library | `ucum` |

Keep `network.mode="lookups"` for API calls, including local container APIs. Set `network.mode="offline"` for file/captured-response-only runs. Source data and inference stay local; enabled external services receive terminology/search context.

## 2. Standard locations and downloads

Create the applicable folder, download from the publisher yourself, and retain licence/release metadata beside the extracted data. Checking a directory reads it; it never accepts download conditions or updates its contents.

| Source | Where to obtain it | Standard location | TOML catalogue ID |
| --- | --- | --- | --- |
| Full LOINC | [Regenstrief downloads](https://loinc.org/downloads/) — account/licence | `local/terminologies/loinc/Loinc.csv` | `loinc-file` |
| German LOINC / UCUM labels | [BfArM FHIR packages](https://terminologien.bfarm.de/fhirpackages.html) | `local/terminologies/bfarm-lab/` | `bfarm-lab` |
| German ATC / DDD | [BfArM FHIR packages](https://terminologien.bfarm.de/fhirpackages.html) — choose ATC-GM and exact edition | `local/terminologies/atc-de/` | `atc-de-files` |
| IFA / PZN | [IFA](https://www.ifaffm.de/) or your authorised terminology provider; obtain the actual product catalogue and use conditions | `local/terminologies/ifa-pzn/` | `ifa-pzn-files` |
| ICD-10-GM | [BfArM FHIR packages](https://terminologien.bfarm.de/fhirpackages.html) | `local/terminologies/icd10-gm/` | `icd10-gm-files` |
| OPS | [BfArM FHIR packages](https://terminologien.bfarm.de/fhirpackages.html) | `local/terminologies/ops/` | `ops-files` |
| MII lab profile / selected bindings | `profile-init` uses pinned publisher URLs in TOML | `local/profiles/` | `[specification]` |
| UCUM grammar / conversion | [NLM API](https://ucum.nlm.nih.gov/ucum-service.html) or [NLM library](https://github.com/LHNCBC/ucum-lhc) in the local container | Library installed during image build | `ucum-api` / `ucum-local` |

For `fhir_artifacts`, extract the package's actual `.json` resources, including nested `package/` directories. A complete base CodeSystem establishes membership. A supplement adds labels for its matching base edition. A package that only declares a code-system URI needs a covering server or full authorised export to establish membership. For IFA, obtain a complete permitted catalogue or use your institution's API.

**BfArM's German LOINC linguistic variant is a supplement.** Pair it with the same full LOINC edition/API. BfArM UCUM Common Units supplies labels for a finite list; `ucum-api` or `ucum-local` evaluates arbitrary UCUM expressions and conversions. ATC DDD strings are drug-utilization context for future medication work.

All paths can be overridden with `path` in TOML. Native Python resolves relative paths from the TOML directory. Compose mounts `local/` at `/app/local/` and sibling `../input/` at `/input/`. To store downloads elsewhere, set `SQC_LOCAL_DIR` or `SQC_INPUT_DIR` in `.env`; use the corresponding container path in TOML. [Mount reference](configuration.md#paths-and-container-mounts).

## 3. API, files and fallback in one TOML

Edit the existing lookup tables in `pipeline.toml`. Each table name is a unique **alias**; its `source` selects a row in `catalog.csv`. The template includes an API alias `loinc`, a disabled file alias `loinc_local`, and a separate `ucum` service. There is one dedicated LOINC API and one UCUM grammar/conversion endpoint; additional local files and generic FHIR sources can participate in membership routing.

| Desired lookup | `lookups.loinc.enabled` | `lookups.loinc_local.enabled` | `terminology.strategy` |
| --- | --- | --- | --- |
| Official LOINC API | `true` — credentials below | `false` | `"fallback"` |
| Your full LOINC file | `false` | `true` — file and edition below | `"fallback"` |
| File first, API as fallback | `true` | `true` | `"fallback"` |
| Compare file and API membership | `true` | `true` | `"all"` |

Keep `network.mode="lookups"` when using any API, including local UCUM/Blaze. File-only **LOINC** can still use the NLM UCUM API. The source selection and actual attempts are recorded separately in each report.

### Official LOINC API

```toml
[lookups.loinc]
enabled = true
role = "primary"
source = "loinc-api"
```

Set **`LOINC_FHIR_USERNAME`** and **`LOINC_FHIR_PASSWORD`** in ignored `.env`. The catalogue supplies `https://fhir.loinc.org` and the credential variable names. [Official authentication](https://loinc.org/kb/api/auth).

### Full local LOINC table

Download and extract the full `Loinc.csv`, preserving its publisher headers. Set the release you downloaded, for example:

```toml
[lookups.loinc_local]
enabled = true
role = "primary"
source = "loinc-file"
path = "local/terminologies/loinc/Loinc.csv"
version = ""                         # REQUIRED when enabled: your downloaded edition, e.g. "2.83".
complete = true
```

This reads the file directly and provides codes, names, axes and example units. `version` is the release edition; each row's `VersionLastChanged` is different metadata. Code absence is invalid only for a declared complete edition.

### Fallback or multiple membership checks

After providing the file and API credentials, enable **both** tables above and edit the existing routing table:

```toml
[terminology]
strategy = "fallback"                # Change to "all" for all applicable membership sources.
```

For a declared LOINC code, fallback checks the local file first. A valid result stops the membership search; unknown, unavailable or invalid results continue to the next applicable source. `all` asks every enabled membership source covering that canonical system. A returned valid/invalid disagreement is retained as `unknown` with a conflict reason. An incomplete catalogue's missing code stays unknown. Local sources come before APIs; roles `primary`, `source`, `optional` determine order within each group, then configuration order breaks ties.

**Setup precedes fallback:** provide every enabled file and configured secret. Fix missing files/credentials when `config-check` reports them. These are setup failures, separate from a running source returning no coverage or being unavailable. UCUM syntax/conversion uses the single service in `lookups.ucum`; selecting `all` adds membership cross-checks, not multiple conversion engines. Label/candidate searches and profile-binding checks record their own sources.

To add another API, use a new alias and its covered canonical systems. Example for an institutional FHIR terminology server:

```toml
[lookups.institution]
enabled = true
role = "optional"
source = "own-fhir"
endpoint = "https://your-institution.example/fhir" # Replace with your real service URL.
systems = ["http://loinc.org"]                    # Only the systems it serves.
# token_env = "TX_FHIR_BEARER_TOKEN"              # Enable if required; value goes in .env.
```

Use `strategy="all"` to include this source alongside the installed file and primary API for membership cross-checks. Add more uniquely named tables using adapters from `catalog.csv`. `role="source"` is appropriate for your LIS's actual local vocabulary; its canonical URI must match the input. [Bearer tokens, secret files and mTLS](#5-own-servers-and-extending-the-catalogue).

### German labels from existing downloads

```toml
[lookups.bfarm_lab]
enabled = true
role = "optional"
source = "bfarm-lab"
path = "local/terminologies/bfarm-lab"
```

This keeps primary LOINC/UCUM checks and adds publisher labels. Exact source/version/hash accompanies the result; differing base editions remain visible for review.

### UCUM via NLM or locally

```toml
[lookups.ucum]
enabled = true
role = "primary"
source = "ucum-api"
```

For local syntax/conversion, change `source` to `"ucum-local"` and run:

```sh
podman-compose --profile terminology up -d --build ucum
podman-compose run --rm run-plausibility-check lookup --lookup ucum --code 'g/dL'
```

The image installs pinned `@lhncbc/ucum-lhc` from npm. Subsequent calls need no external UCUM connection. The service listens on host loopback port 8084; Compose clients use `http://ucum:8080/ucum-service/v1`. It accepts exact valid expressions, returns supported numeric conversions, and keeps spelling substitutions as proposals. Library calculations use floating-point arithmetic. The Python checker accepts only positive multiplicative conversions with zero offset; analyte-dependent mass/molar changes require additional evidence and review.

## 4. Optional local terminology server

Use this route to share your downloaded full catalogues with other FHIR clients. For one local CSV review, direct file lookup is the simpler route.

Configure the file to import separately from the server to query:

```toml
[terminology]
strategy = "fallback"
import_lookups = ["loinc_download"]

[lookups.loinc_download]
enabled = false                     # Explicit import input; normal checks use the server below.
source = "loinc-file"
path = "local/terminologies/loinc/Loinc.csv"
version = "2.83"                     # Your full release.
complete = true

[lookups.loinc]
enabled = true
role = "primary"
source = "loinc-local-server"
```

Run after `profile-init`:

```sh
podman-compose --profile terminology up -d blaze
podman-compose --profile terminology run --rm terminology-init
podman-compose run --rm run-plausibility-check lookup --lookup loinc --code 718-7
```

Blaze's default configuration serves your imported CodeSystems and selected profile ValueSets. `terminology-init` projects a full official LOINC table into a FHIR CodeSystem with its names, axes and example units, or imports complete FHIR CodeSystems unchanged apart from a deterministic server ID. It records source/submission hashes and editions in `local/validation/terminology-installation.json`. Supplement labels continue through the direct file adapter. The persistent `validation_blaze` volume retains imports between runs.

For ATC, IFA, ICD or OPS, add a file lookup with the catalogue ID from the download table, list its alias in `import_lookups`, then use a `local-fhir` lookup with the appropriate canonical `systems`. Example for German ATC:

```toml
[lookups.atc_download]
enabled = false
source = "atc-de-files"

[lookups.atc]
enabled = true
source = "local-fhir"
systems = ["http://fhir.de/CodeSystem/bfarm/atc"]
```

Add `"atc_download"` to `terminology.import_lookups`, import, then inspect one actual code from your release:

```sh
podman-compose --profile terminology run --rm terminology-init
podman-compose run --rm run-plausibility-check lookup --lookup atc --code A10BA02
```

The server API is `http://127.0.0.1:8082/fhir` for native clients, `http://blaze:8080/fhir` inside Compose. `CodeSystem/$lookup` and `$validate-code` are [Blaze terminology operations](https://blaze-server.org/terminology-service.html). API routing preserves the selected canonical system and returned edition.

## 5. Own servers and extending the catalogue

Add a row to `catalog.csv` with a unique `id`, supported `type`, canonical `systems` separated by `;`, and endpoint or default local path. `download_url` and `description` guide operators; they never trigger a download. Credentials are **variable names**, never values. New rows can reuse any supported adapter: `loinc`, `ucum`, `fhir`, `fhir_artifacts`, `loinc_csv`, `csv`, `sqlite`, `ols`, `who_icd`, `website`.

Select the row in TOML with `source="your-id"`; explicit TOML fields override catalogue defaults. Set `[catalog] path="local/my-catalog.csv"` for a private directory. For a new API protocol, implement/test an adapter before adding its type. An inline lookup with `type` remains available for library users.

```toml
[lookups.institution]
enabled = true
role = "primary"
source = "own-fhir"
endpoint = "https://your-institution.example/fhir"
systems = ["http://loinc.org", "http://fhir.de/CodeSystem/bfarm/atc"]
token_file = "local/secrets/terminology-token"
```

`TX_FHIR_BEARER_TOKEN` is an optional environment variable name for a token issued by your own server. Configure it with `token_env="TX_FHIR_BEARER_TOKEN"` instead of `token_file`. For [MII TermServ](https://www.ontoserver.mii-termserv.de/), use `source="mii-termserv"`, covered `systems`, `client_cert` and `client_key` paths for your institution's authorised certificate pair. `chmod 600 local/secrets/*` restricts secret files to their owner.

The catalogue also points to SNOMED CT, RxNorm, UNII, EDQM, SemLookP and WHO ICD-11. Enable only sources covering the actual systems/editions. A source-directory entry describes where to obtain or query content; each exact lookup records what was actually established.

## 6. Verify before the batch

```sh
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check lookup --lookup loinc --code 718-7 --name 'Hemoglobin'
podman-compose run --rm run-plausibility-check lookup --lookup ucum --code 'g/dL'
podman-compose run --rm run-plausibility-check check --out output/lookup.csv
```

`lookup --lookup ALIAS` tests the selected source directly; use `--lookup loinc_local` for the file alias. The `check` command applies the configured combination and routing. The report records enabled sources plus actual per-item attempts, so configuration and source use can be distinguished. APIs receive terminology/search context; source failures and missing coverage remain visible.

Version updates are explicit: select a new release, download/import it and run a fresh check. Optional pinned `[packages.*]` metadata can be inspected with `package-check`; the batch never silently replaces a release. Every report records the selected configuration and catalogue fingerprint plus actual supporting sources.
