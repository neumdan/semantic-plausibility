# Terminologies and lookup services

Two separate catalogues are supplied:

- [Terminology catalogue](terminology-catalog.csv): identifiers and canonical systems.
- [Lookup catalogue](lookups.csv): endpoints, access requirements, credentials and implemented adapters.

The larger [KDS-derived inventory](terminologies.csv) remains available as a reference. An entry in any catalogue does **not** mean that its content is installed, freely licensed or validated by this batch runner.

## Choose local files or an API

Choose **per lookup**; a run can combine local labels with API validation. Laboratory input remains a separate CSV/FHIR/database export.

| Route | One-time setup | Reference in TOML | During a batch |
| --- | --- | --- | --- |
| **A — downloaded/local data** | Manually download/extract official FHIR JSON, or provide an actual CSV/SQLite catalogue | `type="fhir_artifacts"`, `"loinc_csv"`, `"csv"` or `"sqlite"`; `path`, `systems`, `enabled=true` | Read the selected local files; no download occurs |
| **B — API** | Choose the appropriate adapter/endpoint; obtain credentials when required | `type`, `endpoint`, `enabled=true`; `systems` for generic sources; credential environment/file references | Request and cache results from the explicitly enabled service |

For A, keep installed terminology under `local/terminologies/`. A package's `[packages.NAME].path` and its `[lookups.NAME].path` must refer to the same installation. Existing FHIR artifacts need only the lookup table. CSV catalogues require `system,code`; optional `version,display` are supported. SQLite requires `codes(system,code,version,display)`.

```toml
# A: add this table after installing the selected real package.
[lookups.loinc_de]
enabled = true
type = "fhir_artifacts"
path = "local/terminologies/loinc-de-2.82.0"
systems = ["http://loinc.org"]
```

```toml
# B: replace the existing lookups.loinc table to select the official API.
[lookups.loinc]
enabled = true
type = "loinc"
endpoint = "https://fhir.loinc.org"
username_env = "LOINC_FHIR_USERNAME"
password_env = "LOINC_FHIR_PASSWORD"
```

For B, put actual credential values in ignored `.env` using those names, or use `username_file` / `password_file`. Generic FHIR sources use `token_env` / `token_file` or `client_cert` / `client_key` paths for mTLS; WHO uses client ID/secret references. Public NLM, the public FHIR test service and SemLookP need no credentials. Keep only one dedicated `type="loinc"` and one `type="ucum"` service active.

**Offline or combined:** `network.mode="lookups"` permits selected APIs alongside local files. `network.mode="offline"` disables remote adapters; set `input.snapshot="local/reference-pack.json"` to reuse actual UCUM/LOINC responses, examples and verified factors. Capture this pack in a connected run with `output.lookup_pack="output/reference-pack.json"`, then transfer it with your export and any local artifacts. It covers only queries from that run; absent evidence stays unverified. A BfArM label supplement alone cannot validate arbitrary UCUM expressions or establish conversions. There is no fallback to an unconfigured API.

## Laboratory defaults

### Existing BfArM files

Place your actual downloaded FHIR JSON files in ignored `local/terminologies/bfarm-lab/`. For the German laboratory supplements, the supplied configuration expects:

- `CodeSystem-loinc-linguistic-variant-de-de-2-83-0.json` (LOINC base edition 2.83).
- `CodeSystem-ucum-common-units-translation-de-de-1-5-0.json` (common-unit German labels).

```toml
[lookups.bfarm_lab]
enabled = true
role = "optional"
type = "fhir_artifacts"
path = "local/terminologies/bfarm-lab"
systems = ["http://loinc.org", "http://unitsofmeasure.org"]
```

Already downloaded files need no download credentials. A raw official `Loinc.csv` has different headers; it is not the generic `system,code,version,display` catalogue format. These JSON supplements enrich labels and reverse name candidates; they do not establish arbitrary UCUM syntax/conversions or clinical ranges. Exact code and edition checks remain necessary. [BfArM UCUM supplement](https://terminologien.bfarm.de/CodeSystem-ucum-common-units-translation-de-de-1-5-0.html).

`TX_FHIR_BEARER_TOKEN` is an optional credential name for a protected FHIR terminology API. It is unnecessary for local files, public Europe PMC/NLM services or ordinary local Ollama. Generic FHIR lookups explicitly select `token_env`/`token_file`; the official LOINC API uses username/password references. Keep credentials in `.env` or secret files, not TOML values or reports.

| Need | Default | Configuration |
| --- | --- | --- |
| Unit grammar and documented linear conversion | NLM UCUM | `lookups.ucum.endpoint` |
| LOINC identity and example units | Public FHIR test server; official/site server for studies | `username_env` and `password_env`, or corresponding secret files |
| Local/other code systems | Own FHIR server or CSV/SQLite | `systems`, `endpoint`, `token_env`/`token_file`, or `path` |
| Quoted lookup-page evidence | Configured website / LOINC page | `research.enabled=true`, model and catalogue |
| Study evidence | PubMed / Europe PMC abstracts | `research.scope="studies"`, `research.sources`, optional PubMed `API_KEY_ENV` |

NLM exposes unit validation and conversion services. A mathematically supported factor is required before proposing a value/reference transformation. [NLM documentation](https://ucum.nlm.nih.gov/ucum-service.html).

LOINC's API requires a registered account and Basic Authentication. Public lookup pages can provide candidate evidence but cannot substitute for a successful exact code validation. [LOINC authentication](https://loinc.org/kb/api/auth).

## Medication terminology sources

The [MII Medication terminology guide](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Medikation_2026/MIIIGModulMedikation-TechnischeImplementierung-Terminologien.html) distinguishes classification, product, ingredient, form/route and quantities. Configure the **actual canonical system**, edition and source. An IG's ValueSet binding is not a server address or a list of installed/licensed content. Server coverage must be tested separately.

| Source | Supported route | Setup / limit |
| --- | --- | --- |
| WHO ATC | Generic FHIR; real local export | Public trial alias `atc_who`; independent from German ATC |
| German ATC/DDD | BfArM FHIR package; own/MII FHIR server | [Pinned download setup](#local-atcddd); DDD text is not a dose limit |
| SNOMED CT | Generic FHIR; authorised FHIR/CSV/SQLite export | Public trial covers an international edition. German edition/licensing must be supplied explicitly; raw RF2 is not read by this runner |
| RxNorm | Generic FHIR; real local export | Alias `rxnorm`; specify ingredient/product concepts correctly. The dedicated RxNav API is a known alternative, not a separate implemented adapter |
| UNII | Generic FHIR; real local export | Alias `unii`; [FDA downloads](https://precision.fda.gov/uniisearch). Raw FDA files require a documented map to `system,code,version,display`; no automatic FDA importer |
| EDQM Standard Terms | Generic FHIR; authorised FHIR/CSV/SQLite | Alias `edqm`; production catalogue access/terms apply. Check form and route independently |
| ASK / PZN / CAS | Own or MII FHIR server where covered; authorised exports | Enable `german_medication_files` or your server. No licensed product/substance catalogue ships with the code |
| UCUM | NLM API; captured real responses | Dose/strength/rate expressions and supported conversions, not a medication catalogue |

`terminology.example.toml` provides single-source aliases and disabled national/local routes. [Probe commands and synthetic source example](../examples/medication/README.md#terminology-walkthrough). The public server is for trial interoperability; returned versions are preserved and never called the latest national edition. If you change sources, configure the **same tables in the domain recipe you run**.

Downloaded FHIR packages/resources can be used unchanged with `type="fhir_artifacts"`. For another publisher format, perform an explicit, documented conversion into the catalogue schema or import it into your terminology server. Preserve the code system and release; set `complete=false` for a subset. A generic unit label, ATC class or ingredient lookup cannot prove a product's actual strength.

### MII client certificates

The [SU-TermServ access page](https://www.ontoserver.mii-termserv.de/) requires an authorised client certificate for read access; a bearer token alone does not provide it. Obtain the certificate through your institution or SU-TermServ. Put the PEM certificate chain and matching unencrypted private key in ignored `local/secrets/`, restrict the key to its owner, and mount this directory read-only (the standard Compose runner already does so). Pass file paths, never key contents, in your selected TOML:

```toml
[lookups.mii_termserv]
enabled = true
type = "fhir"
role = "primary"
endpoint = "https://ontoserver.mii-termserv.de/fhir"
client_cert = "local/secrets/mii-client.crt"
client_key = "local/secrets/mii-client.key"
systems = ["http://fhir.de/CodeSystem/bfarm/atc", "http://fhir.de/CodeSystem/ask", "http://fhir.de/CodeSystem/ifa/pzn", "http://snomed.info/sct", "http://standardterms.edqm.eu"]
```

Paths above assume root `pipeline.toml`; use `../../local/secrets/…` in a nested `examples/medication/` TOML. Only declare systems your deployment covers. Then:

```sh
chmod 600 local/secrets/mii-client.key
podman-compose run --rm run-plausibility-check config-check --connections
podman-compose run --rm run-plausibility-check lookup --lookup mii_termserv --system http://fhir.de/CodeSystem/ask --code 00002
```

`config-check` loads the certificate/key pair locally; `--connections` also probes access. An exact lookup is still needed to check code/edition coverage. Use the same `--config` argument on both commands when selecting another mounted TOML. No certificate was available in the public example test; that integration remains unverified until configured locally. Full FHIR validation uses its own local validator configuration.

## Germany and Europe

BfArM/gematik publishes versioned FHIR R4 [packages](https://terminologien.bfarm.de/fhirpackages.html). For the laboratory workflow, **download and extract these manually** after reviewing the conditions. Reference the actual directory with `lookups.bfarm_lab`; the check never downloads BfArM archives or accepts conditions. Keep package metadata, licences and exact edition alongside the JSON. [Step-by-step setup and optional local Blaze](index.html#terminology).

`package-check --config …` checks registry release metadata only. Enable `[terminology] check_updates=true` plus exact `[packages.*]` pins to record that check at the start of each run. A newer edition is a notice, not an automatic update. `package-init` remains a separate legacy explicit installer; it is not part of this manual laboratory workflow.


## German packages and labels

Use [terminology.example.toml](../terminology.example.toml) as an optional setup example, or copy its selected tables into your existing `pipeline.toml`. The sample uses the illustrative FHIR Bundle and keeps all additional sources disabled until configured.

```sh
cp -n terminology.example.toml terminology.toml
```

1. In `terminology.toml`, select `[packages.loinc_de]` and/or `[packages.ucum_de]`: set `enabled=true`, pin the edition and local `path`.
2. Read the [LOINC download conditions](https://terminologien.bfarm.de/bfarm.terminologien.loinc-conditions.html) and [UCUM conditions](https://terminologien.bfarm.de/bfarm.terminologien.ucum-conditions.html). Set `accept_download_conditions=true` only if you accept the conditions for that package. BfArM then issues its download token. Alternatively reference an existing `token_env` or `token_file`; acceptance remains explicit. Tokens are never saved in receipts or reports.
3. Install the selected packages, then enable the corresponding `[lookups.loinc_de]` / `[lookups.ucum_de]` artifact sources. Their `path` must equal the installation path.

```sh
uv run --no-sync --env-file .env run-plausibility-check package-init --config terminology.toml
# Or, with the read/write local volume used only for installation:
# For containers, set SQC_CONFIG_FILE=terminology.toml in .env first.
podman-compose --profile terminology run --rm package-init
# Docker alternative:
docker compose --profile terminology run --rm package-init
```

Ordinary `batch` runs never download packages. `package-init` checks the publisher SHA-1, records SHA-256, preserves the original archive/licence files and installs atomically. Repeated installation verifies the existing files. Dependencies are recorded, not automatically downloaded. Package version and CodeSystem version can differ; code checks use the CodeSystem edition. Installed contents stay under ignored `local/`; transfer this folder to a disconnected review machine. For offline LOINC axes/example units use the full official `loinc_csv` below or a real captured API pack. For UCUM syntax/factors supply the captured pack. A translation package alone is insufficient.

**Supplements supply labels, not independent code membership.** A base `CodeSystem.content="complete"` can establish membership/absence for its edition; a fragment cannot disprove an absent code. Only explicit ValueSet expansions are evaluated locally; unevaluated `compose` filters stay unverified. UCUM has a generative grammar: Common UCUM Units is a useful finite subset, not a complete list of every valid expression.

The configuration pins the BfArM LOINC translation to `2.82.0`, matching the public test server's observed LOINC `2.82`. BfArM also publishes `2.83.0`; select matching API/base/translation releases for your study. UCUM common-unit translation is `1.5.0`; this is its list version, not the UCUM grammar version. Nothing follows `latest` automatically.

## Analyte names and translation candidates

Map CSV/SQL columns explicitly:

```toml
[input.columns]
# Add these to your existing column map; do not replace the other roles.
analyte_name = "test_name"          # internal Observation.code.text
code_display = "coding_display"  # optional Observation.code.coding[].display
```

Native FHIR preserves both fields. Source names remain in guarded groups; different names are not silently merged. The report adds `analyte_name`, `name_check`, `unit_label_check` and `name_evidence_json`. Read these alongside the independent code/unit checks:

| Finding | Meaning |
| --- | --- |
| `exact_alias` | Supplied name matches a sourced display/designation/synonym, ignoring case and whitespace |
| `alias_version_unverified` | A supplement supplies the alias but does not establish the requested base edition |
| `unverified` | Name/translation is not established; review it, rather than assuming the code is wrong |
| `same_as_code` | Unit display equals the machine code; its syntax is checked separately |
| `not_provided` | No label was supplied |

For example, the real FHIR LOINC lookup for `718-7` supports “Hämoglobin” and “Hb”; “Glucose” is not an established alias for that code. Alias agreement does not prove that the source measurement used the right specimen, property, time, scale or method. UCUM describes units; it does not identify the measured analyte. A translation cannot establish a conversion factor.

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc --code 718-7 --name 'Hämoglobin'
# After installing/enabling the German package, reverse-search its real aliases:
uv run --no-sync --env-file .env run-plausibility-check lookup --config terminology.toml --lookup loinc_de --name 'Hämoglobin'
```

Reverse package lookup returns up to ten exact label/translation candidates, retaining their code, canonical system, language and edition. Multiple matches stay ambiguous. It neither supplies a missing LOINC axis nor approves a local LIS-to-LOINC mapping. Search indexes are built lazily and remain local.

## SemLookP and TS4NFDI

Enable `[lookups.semlook]` in the optional TOML to query the real [SemLookP OLS API](https://semanticlookup.zbmed.de/about/api) at `https://semanticlookup.zbmed.de/ols/api`. Select an explicit `ontology` and `limit`; no API credential is needed for the public service. The adapter caches bounded searches. It returns quoted labels, IRIs and response hashes as **candidates**; a LOINC coding candidate is included only when the result's LOINC IRI and identifier agree. Every candidate still needs exact code/version and clinical-context review. German coverage varies; no automatic model translation is invented.

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config terminology.toml --lookup semlook --name hemoglobin
```

In a batch, configured name searches run only for supplied, unresolved analyte labels, independently of optional model research. Add your local LIS canonical to `systems` if you explicitly want its names searched for LOINC candidates. The underlying model stays local; the enabled API receives the terminology name, never a measurement value or resource ID.

The [TS4NFDI Terminology Service Suite](https://github.com/ts4nfdi/terminology-service-suite) supplies React/TypeScript widgets and plain-HTML adapters for a future expert interface. Its underlying APIs are useful here; the frontend suite adds no dependency to this Python batch library.

## WHO ICD-11 MMS API

The optional `[lookups.icd11]` uses `type="who_icd"`, canonical `http://id.who.int/icd/release/11/mms`, a pinned `release`, `language`, API endpoint and OAuth endpoint. Set `WHO_ICD_CLIENT_ID` and `WHO_ICD_CLIENT_SECRET` in ignored `.env`, or use `client_id_file` / `client_secret_file`. Register for credentials at the [WHO ICD API](https://icd.who.int/icdapi); [authentication uses client credentials and scope `icdapi_access`](https://icd.who.int/docs/icd-api/API-Authentication/).

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config terminology.toml --lookup icd11 --code 1A00 --version 2026-01
```

The adapter confirms the release, exact stem code and entity, requests `API-Version: v2`, uses HTTPS and caches the short-lived OAuth token in memory. WHO MMS is distinct from uncoded Foundation entities and from ICD-10-GM. Postcoordination stays unverified in this first adapter; no code simplification is applied. English is the example default; [WHO marks German in 2026-01 as prerelease](https://icd.who.int/docs/icd-api/SupportedClassifications/). This is a standalone terminology lookup, not implemented diagnosis/severity/Condition assessment.

## Credentials and own servers

```toml
[lookups.own_terminology]
enabled = true
type = "fhir"
endpoint = "https://your-server.example/fhir"
systems = ["http://loinc.org", "http://snomed.info/sct"]
token_file = "local/secrets/terminology-token"

[lookups.local_codes]
enabled = true
type = "csv"
path = "local/codes.csv"
systems = ["https://your-organisation.example/CodeSystem/lis-tests"]
complete = false
```

A CSV catalogue needs `system,code` and may include `version,display`. SQLite needs `codes(system,code,version,display)`. A partial list cannot prove that an absent code is invalid. Generic FHIR lookups currently support bearer tokens; the dedicated LOINC adapter supports Basic Authentication. Unsupported authentication requires an adapter or your gateway.

For services requiring credentials, reference them in TOML and put their values in `.env` or mounted secret files. Complete release imports and licence acquisition are explicit setup steps, not autonomous model tasks.

Catalogue documentation was reviewed on 8 October 2026. Live tests cover NLM, LOINC FHIR, BfArM public package installation and SemLookP. Protected German translation downloads and authenticated WHO lookups additionally require explicit licence acceptance/credentials and their opt-in tests; they are not claimed as tested without those prerequisites.

## Downloads beside the repository

Use the following operator-owned layout; none of these downloads is published with the code:

```text
dev/
  semantic-plausibility/
    pipeline.toml       # source selection, pinned profile, lookup/model settings
    .env                # credentials only; ignored
    local/secrets/      # optional secret files; ignored
    output/             # local reports; ignored
  input/
    laboratory.csv
    terminologies/bfarm-lab/
      CodeSystem-loinc-linguistic-variant-de-de-2-83-0.json
      CodeSystem-ucum-common-units-translation-de-de-1-5-0.json
```

For these actual downloaded FHIR JSONs, edit the existing lookup table:

```toml
[lookups.bfarm_lab]
enabled = true
role = "optional"
type = "fhir_artifacts"
path = "../input/terminologies/bfarm-lab"
systems = ["http://loinc.org", "http://unitsofmeasure.org"]
```

Python resolves the sibling path against the TOML directory; Compose mounts it read-only at `/input`. Keep primary LOINC/UCUM APIs configured. Translation supplements enrich matching editions only. Local-file SHA-256, supplement/base editions and the publisher's canonical resource URL accompany label evidence. Use `type="loinc_csv"` for the unchanged official `Loinc.csv`; use `type="csv"` only for the generic `system,code,version,display` catalogue.

## Full local LOINC table

Download a release from [LOINC downloads](https://loinc.org/downloads/) under its account/licence conditions. Unpack the full **Loinc.csv**, preserve its headers, and record the actual release. The adapter reads identity, canonical name, all six axes, status, synonyms and example units using the [official table structure](https://loinc.org/kb/users-guide/loinc-database-structure/loinc-table-structure/).

```toml
[lookups.loinc_local]
enabled = true
role = "primary"
type = "loinc_csv"
systems = ["http://loinc.org"]
path = "../input/terminologies/loinc/Loinc.csv"
version = "2.83" # Replace with the downloaded release; not a row's VersionLastChanged.
complete = true # Only the full edition; false for a subset.
```

A full table establishes code membership and provides example-unit context locally; example units are not exhaustive clinical rules. Disable the remote LOINC lookup for a strictly local code check, or keep it as an explicit fallback. `network.mode="offline"` disables all remote lookups; absent UCUM syntax/conversion evidence then remains unknown. Optional BfArM translations must match the base edition. The file hash and release are retained. Missing or duplicate codes and malformed files fail visibly.

```sh
uv run --no-sync --env-file .env run-plausibility-check lookup --config pipeline.toml --lookup loinc_local --code 718-7
```

## Local ATC/DDD

BfArM publishes the German ATC/DDD package **`bfarm.terminologien.atcgm`**; package **2026.0.0** contains CodeSystem edition **2026**, canonical **`http://fhir.de/CodeSystem/bfarm/atc`**. Its `ddd` concept property is a publisher-provided string. See the [package catalogue](https://terminologien.bfarm.de/fhirpackages.html) and [official ATC resource description](https://terminologien.bfarm.de/CodeSystem-atcgm-2026.html).

For the existing medication A1 checks, use a **manual download**, just as for the laboratory supplements:

1. Open the official package catalogue and the [ATC download conditions](https://terminologien.bfarm.de/bfarm.terminologien.atcgm-conditions.html). Review and accept the conditions yourself, if appropriate.
2. Download the required exact edition and extract its actual FHIR JSON files into ignored `local/terminologies/atc-2026/`. Retain metadata and licences. Skip this if your files are already installed; no synthetic catalogue is provided.
3. Enable a file lookup in the **medication check's TOML** as below, or enable its existing `lookups.downloads` block with that path/system. Do not add two identical sources accidentally.
4. Run that recipe's `config-check`, then `check`. Local reading needs no API secret. An authenticated terminology API is an alternative; configure its endpoint/system/credential reference explicitly.

```toml
[lookups.atc_de]
enabled = true
role = "primary"
type = "fhir_artifacts"
systems = ["http://fhir.de/CodeSystem/bfarm/atc"]
path = "local/terminologies/atc-2026" # Nested example TOMLs use ../../local/…
```

This exact CodeSystem is different from WHO ATC's `http://www.whocc.no/atc`. Preserve the source's actual system/edition; don't relabel it merely to obtain a match. A local read needs no API credentials. The report retains canonical names and available DDD strings in `ddd_references_json`, alongside code, edition, source and artifact hash. A missing DDD is a missing catalogue value. DDD is a drug-utilization reference, not an individual prescribed dose, a safety threshold or product concentration; no automatic dose correction uses it.
