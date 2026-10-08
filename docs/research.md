# Optional local research

Enable this only after source mapping and deterministic lookups work. The application retrieves evidence; the local model extracts constrained JSON with supporting quotations. Candidates are validated independently and retained for physician review. No patient values/IDs are sent to model prompts.

The implemented order is **catalogue spelling → configured terminology/LOINC pages → optional PubMed abstracts**. A candidate unit must come from supplied evidence and validate against its system. A spelling guess cannot establish a conversion factor. Distinguish analytical limits, documented extremes and population-specific reference intervals.

## Configure

In your existing `pipeline.toml`, remove the synthetic `input.snapshot`. Replace the network section and add these optional sections. Credentials use ignored `.env` or secret files.

```toml
[network]
mode = "lookups"
allowed_http_hosts = ["ollama", "host.containers.internal"]

[lookups.ucum]
enabled = true
type = "ucum"
endpoint = "https://ucum.nlm.nih.gov/ucum-service/v1"

[lookups.loinc]
enabled = true
type = "loinc"
endpoint = "https://fhir.loinc.org"
username_env = "LOINC_FHIR_USERNAME"
password_env = "LOINC_FHIR_PASSWORD"

[models.local]
runtime = "ollama"
base_url = "http://ollama:11434/v1"       # uv/native: http://127.0.0.1:11434/v1
model = "ministral-3:3b"
temperature = 0
max_tokens = 2048
timeout_seconds = 180
response_mode = "json_schema"

[research]
enabled = true
scope = "lookup"                        # studies adds PubMed; lookup skips it
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"

[research.pubmed]
BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
API_KEY_ENV = "PUBMED_API_KEY"            # optional
EMAIL = ""
MAX_RESULTS = 3
```

For real data also replace/remove the synthetic ConceptMap and local-code catalogue. Configure each declared system against an appropriate reviewed source. [Lookup options](lookups.md).

## Provision and test

[Ollama / LM Studio setup](shipped-stack.md#optional-local-models) gives uv/Compose endpoint choices. With native Ollama and uv:

```sh
uv run --no-sync --env-file .env plausibility model-init --config pipeline.toml
uv run --no-sync --env-file .env plausibility catalog-init --config pipeline.toml
uv run --no-sync --env-file .env plausibility model-test --config pipeline.toml
uv run --no-sync --env-file .env plausibility batch --config pipeline.toml
```

Any local OpenAI-compatible model can be configured by its exact served ID. Check its licence, memory needs, runtime and digest. Eleven synthetic extraction cases test operational behaviour; medical pretraining or a passing smoke test does not establish clinical accuracy. Schema/quotation checks constrain claims but do not guarantee their applicability.

UCUM definitions/common examples are downloaded from the official ucum-org repository by `catalog-init`; they are not a complete enumeration of its generative grammar. Exact syntax validation remains separate.

**Scope:** bounded page and PubMed-abstract extraction is implemented. Europe PMC, NFDI4Health, Health Atlas, study portals and a broad web/deep-search orchestrator remain future adapters. No LangChain dependency is needed for the present bounded tasks.
