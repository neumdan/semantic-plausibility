# Optional local research

Start with the real deterministic lookups. The application retrieves evidence; a local model extracts schema-constrained candidates with supporting quotations. A model cannot mark a code valid or invent a conversion factor.

The configured order is **real UCUM catalogue → spelling candidates → configured terminology/LOINC pages → optional PubMed abstracts**. Candidate units are validated against their system. Numeric ranges retain source, population, specimen and method for physician review. Analytical limits, documented extremes and reference intervals remain different evidence types.

## Enable

The example TOML already contains `[models.local]` and `[research]`. Follow [Ollama setup](shipped-stack.md#ollama), then set:

```toml
[research]
enabled = true
scope = "lookup"  # studies additionally queries PubMed
unit_catalog = "local/unit-catalog.json"
loinc_website_base_url = "https://loinc.org"
```

For studies optionally add `[research.pubmed]` with `BASE_URL="https://eutils.ncbi.nlm.nih.gov/entrez/eutils"`, `API_KEY_ENV="PUBMED_API_KEY"`, `EMAIL` and `MAX_RESULTS=3`. The key is optional and belongs in `.env`.

`LOCAL_MODEL_BASE_URL` selects a local Ollama/LM Studio/OpenAI-compatible endpoint. `models.local.model` selects the served model; `ministral-3:3b` is the small EU-origin example ([Ollama model catalogue](https://ollama.com/library/ministral-3)). Record its digest and assess proposal accuracy with physicians before routine application. This project does not claim a clinical model benchmark.

Only actual fetched text/catalogue entries are supplied. Source/schema checks reject unsupported claims; missing evidence remains unresolved. No patient values or IDs go into model prompts. Broad web/deep-search orchestration and Europe PMC/NFDI/Health Atlas adapters remain later work; bounded page and PubMed retrieval needs no LangChain stack.
