# Terminologies and lookup services

Two separate catalogues are supplied:

- [Terminology catalogue](terminology-catalog.csv): identifiers and canonical systems.
- [Lookup catalogue](lookups.csv): endpoints, access requirements, credentials and implemented adapters.

The larger [KDS-derived inventory](terminologies.csv) remains available as a reference. An entry in any catalogue does **not** mean that its content is installed, freely licensed or validated by this batch runner.

## Laboratory defaults

| Need | Default | Configuration |
| --- | --- | --- |
| Unit grammar and documented linear conversion | NLM UCUM | `lookups.ucum.endpoint` |
| LOINC identity and example units | Public FHIR test server; official/site server for studies | `username_env` and `password_env`, or corresponding secret files |
| Local/other code systems | Own FHIR server or CSV/SQLite | `systems`, `endpoint`, `token_env`/`token_file`, or `path` |
| Quoted lookup-page evidence | Configured website / LOINC page | `research.enabled=true`, model and catalogue |
| Study evidence | PubMed abstracts | `research.scope="studies"`, optional `API_KEY_ENV` |

NLM exposes unit validation and conversion services. A mathematically supported factor is required before proposing a value/reference transformation. [NLM documentation](https://ucum.nlm.nih.gov/ucum-service.html).

LOINC's API requires a registered account and Basic Authentication. Public lookup pages can provide candidate evidence but cannot substitute for a successful exact code validation. [LOINC authentication](https://loinc.org/kb/api/auth).

## Germany and Europe

BfArM/gematik's central terminology service currently documents public browsers and versioned FHIR **packages**. This catalogue does not invent a `$validate-code` endpoint: import the appropriate release into your own server/list, or use its public page as candidate evidence. [Official description](https://ref.terminologien.bfarm.de/).

Austria's ELGA TerminoloGit offers public terminology artifacts and versioned releases. Import the exact relevant edition; the old `termpub` service is not the current publication channel. [ELGA documentation](https://www.elga.gv.at/technischer-hintergrund/technische-elga-leitfaeden/).

WHO ATC offers a free searchable index, while complete downloads use registered access and its ordering portal. It is a medication reference and outside the current laboratory workflow. [WHO Centre access](https://atcddd.fhi.no/atc_ddd_index_and_guidelines/atc_ddd_index/).

EMA SPOR has read-only browser access and version-specific account/role requirements for its APIs. Its dedicated API is listed as **known, not implemented**; it is not a FHIR terminology endpoint. [EMA SPOR](https://spor.ema.europa.eu/).

SNOMED CT content needs the appropriate licence and edition. A public browser is not a production service guarantee. EBI OLS provides ontology lookup; its dedicated API also requires an adapter rather than being treated as FHIR.

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

For every enabled service, credentials must be referenced in the TOML. Put their values in `.env` or mounted secret files. Complete release imports and licence acquisition are explicit setup steps, not autonomous model tasks.

Catalogue documentation was reviewed on 6 October 2026. Availability, edition coverage and credentials must be checked for the actual deployment; not every listed service was contacted by a live integration test.
