# Terminologies and lookup services

Two separate catalogues are supplied:

- [Terminology catalogue](terminology-catalog.csv): identifiers and canonical systems.
- [Lookup catalogue](lookups.csv): endpoints, access requirements, credentials and implemented adapters.

The larger [KDS-derived inventory](terminologies.csv) remains available as a reference. An entry in any catalogue does **not** mean that its content is installed, freely licensed or validated by this batch runner.

## Choose local files or an API

Choose **per lookup**; a run can combine local labels with API validation. Laboratory input remains a separate CSV/FHIR/database export.

| Route | One-time setup | Reference in TOML | During a batch |
| --- | --- | --- | --- |
| **A — downloaded/local data** | Download a pinned FHIR package with `package-init`, install real FHIR JSON yourself, or provide an actual CSV/SQLite catalogue | `type="fhir_artifacts"`, `"csv"` or `"sqlite"`; `path`, `systems`, `enabled=true` | Read the selected local files; no download occurs |
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

For B, put actual credential values in ignored `.env` using those names, or use `username_file` / `password_file`. Generic FHIR sources use `token_env` / `token_file`; WHO uses client ID/secret references. Public NLM, the public FHIR test service and SemLookP need no credentials. Keep only one dedicated `type="loinc"` and one `type="ucum"` service active.

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

## Germany and Europe

BfArM/gematik publishes versioned FHIR R4 **packages** through an [NPM-compatible package API](https://terminologien.bfarm.de/fhirpackages.html). The runner now downloads pinned editions explicitly and reads their CodeSystems, label supplements and expanded ValueSets locally. The registry is a package service; it is not assumed to expose `$validate-code`.

Austria's ELGA TerminoloGit offers public terminology artifacts and versioned releases. Import the exact relevant edition; the old `termpub` service is not the current publication channel. [ELGA documentation](https://www.elga.gv.at/technischer-hintergrund/technische-elga-leitfaeden/).

WHO ATC offers a free searchable index, while complete downloads use registered access and its ordering portal. It is a medication reference and outside the current laboratory workflow. [WHO Centre access](https://atcddd.fhi.no/atc_ddd_index_and_guidelines/atc_ddd_index/).

EMA SPOR has read-only browser access and version-specific account/role requirements for its APIs. Its dedicated API is listed as **known, not implemented**; it is not a FHIR terminology endpoint. [EMA SPOR](https://spor.ema.europa.eu/).

SNOMED CT content needs the appropriate licence and edition. A public browser is not a production service guarantee. EBI OLS provides ontology lookup; its dedicated API also requires an adapter rather than being treated as FHIR.

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
podman-compose --profile terminology run --rm --build package-init
# Docker alternative:
docker compose --profile terminology run --rm --build package-init
```

Ordinary `batch` runs never download packages. `package-init` checks the publisher SHA-1, records SHA-256, preserves the original archive/licence files and installs atomically. Repeated installation verifies the existing files. Dependencies are recorded, not automatically downloaded. Package version and CodeSystem version can differ; code checks use the CodeSystem edition. Installed contents stay under ignored `local/`; transfer this folder to a disconnected review machine. For offline LOINC axes/units and UCUM syntax/factors, also supply a pack captured from real APIs; a translation package alone is insufficient.

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

Python resolves the sibling path against the TOML directory; Compose mounts it read-only at `/input`. Keep primary LOINC/UCUM APIs configured. Translation supplements enrich matching editions only. Local-file SHA-256, supplement/base editions and the publisher's canonical resource URL accompany label evidence. A raw `Loinc.csv` with official LOINC column names is not the generic `system/code/display/version` lookup CSV; it needs an adapter/conversion, and must not be passed as that catalogue unchanged.
