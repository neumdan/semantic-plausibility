# Medication terminology and dose checks

Executable adapters for **Medication**, **MedicationRequest**, **MedicationAdministration** and **MedicationStatement**, using FHIR R4. The examples select the published **MII Medication 2026.0.1** guide. All example clinical/formulation data are synthetic input. No synthetic terminology responses or drug catalogue are supplied.

## Run once

From the repository root, after the [standard setup](../../docs/shipped-stack.md#first-setup):

```sh
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/request.toml
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/administration.toml
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/statement.toml
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/product.toml
```

Equivalent container calls (same files and output):

```sh
docker compose run --rm run-plausibility-check check --config /app/examples/medication/request.toml
podman-compose run --rm run-plausibility-check check --config /app/examples/medication/administration.toml
```

Open `output/medication-request.html`, `medication-administration.html`, `medication-statement.html` or `medication-product.html`, with the matching CSV. Each set shows source data → terminology check/name → findings → conditional calculations, with source references. Repeat with a new `--out ../../output/medication-request-2.csv`; paths resolve relative to the selected TOML. Python can also call `semantic_plausibility.run_plausibility_check(config_path)` or the repository's `run-plausibility-check.py` wrapper.

These checks do **not** need Ollama. AI/evidence research and clinician-registry reuse currently support laboratory Observations only; medication research is rejected explicitly. Patient-specific dose safety, interactions, renal/weight adjustments and evidence-based dose limits are not implemented.

## What the examples demonstrate

| Source set | Implemented result |
| --- | --- |
| ATC `N02BE01`, source strength 20 mg/1 mL, dose 5 mL | Real code lookup can return **paracetamol**. With verified UCUM units: conditional `5 × 20 = 100 mg`, offset 0; source strength still requires product verification. |
| Local product, same source strength, dose 100 mg | Conditional `100 × 0.05 = 5 mL`. Local code stays `unknown` until a real local catalogue is configured. |
| Liquid product with “tablet” dose text and no unit coding | Review form and unit identity; no tablet-to-volume conversion. This lexical warning is not an exhaustive coded pharmaceutical-form check. |
| Administration: 10 mL, 20 mL/h, 30 min, explicitly constant uninterrupted delivery | Rate × time ≈ 10 mL; arithmetic consistent. Product/form/route verification can still require review. |
| Same administration with 15 min | Rate × time ≈ 5 mL conflicts with 10 mL: **error** when constant uninterrupted delivery is explicitly asserted. |

`constant_rate=true` is a **source assertion**, not inferred from FHIR `effectivePeriod` or from `status="completed"`. Without it, the comparison is conditional and requires review. Interrupted or variable-rate infusions must not set it. UCUM conversion precision is preserved; arithmetic comparison uses relative tolerance `0.000001`, not a clinical tolerance. A mismatch does not propose a replacement dose.

## Terminology walkthrough

Pick **D** independently of **A** (check level), **B** (input) and **C** (sources). These resource adapters support A1. MII Medication 2026.0.1 defines the target profile; the configured API or installed catalogue supplies the actual terminology content. Choose source systems explicitly, without relabelling a WHO ATC code as German ATC.

| Semantic role | Source systems | How to verify |
| --- | --- | --- |
| Lab test and quantities | LOINC; UCUM | Laboratory recipes, LOINC metadata + unit grammar/conversion |
| Medication classification | WHO ATC; German ATC/DDD | Separate systems and editions; DDD is context, not a dosing limit |
| German product/pack | PZN | Authorised catalogue or server with actual coverage |
| Ingredient | ASK, SNOMED substance, UNII, CAS; optional RxNorm ingredient concept | Exact code lookup; equivalence between codings still needs product/clinical evidence |
| Pharmaceutical form and route | EDQM Standard Terms | Form/route code identity, separately from product/dose compatibility |
| Dose, strength, rate | UCUM | Unit identity/dimension and conditional arithmetic using source-declared strength |

**Run:** `podman-compose run --rm run-plausibility-check check --config /app/examples/medication/terminology.toml`. Open `output/medication-terminology.html`. Docker uses `docker compose`; Python uses `uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/terminology.toml`.

This reads `terminology-bundle.json` (two synthetic source formulations) and performs real lookups:

1. Paracetamol: WHO ATC `N02BE01`; ingredient SNOMED `387517004`, RxNorm `161`, UNII `362O9ITL9D`; EDQM `10219000` (tablet); source strength `500 mg / 1`. The public trial server covers these codes, subject to availability and its returned editions.
2. Aspirin: PZN `06312077`, German ATC `B01AC06` **2023**, SNOMED `387458008`, EDQM tablet; source strength `100 mg / 1`, adapted from the [published MII example](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Medikation_2026/MIIIGModulMedikation-TechnischeImplementierung-FHIR-Profile-Medication.html). No licensed product table ships here. Missing PZN/German ATC coverage remains unknown. A downloaded 2026 ATC catalogue cannot verify a requested 2023 edition.

Code identities do not verify formulation, strength, cross-coding equivalence or clinical appropriateness. RxNorm is an optional international terminology, not an additional MII requirement. These resources do not claim full MII conformance; enable formal validation only after setting up its guide.

### Recorded example run — 15 October 2026

The six Podman A1 scenarios completed with separate CSV/HTML reports and verified audit hashes. Live laboratory tests also verified NLM UCUM and LOINC. The medication example returned:

| Lookup | Observed result / edition |
| --- | --- |
| WHO ATC `N02BE01` | valid / 2025 |
| SNOMED `387517004` | valid / international edition 20250201 |
| RxNorm `161` | valid / server version `03022026` |
| UNII `362O9ITL9D` | valid / 2024-06-22 |
| EDQM `10219000` | valid / 5 February 2025 |
| UCUM `mg`, `1` | valid syntax |
| PZN `06312077`, German ATC `B01AC06` version 2023 | unknown: public server lacks the requested system/coverage |

Both medication formulations remained `needs_review`: source strength is not verified product evidence. Request calculations were conditional; the inconsistent Administration produced an `error`. Product/Statement CSVs retained missing German ATC 2026 coverage as unknown. These are observed service editions, not a claim of current national coverage. ASK, CAS and the certificate-protected MII server were configured/documented but **not verified** in this run. Real BfArM ATC/DDD, ASK/PZN catalogues and a MII client certificate still need to be supplied.

### Probe one service at a time

The separate root `terminology.example.toml` enables public trial aliases. These commands never start a batch or model:

```sh
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup ucum --code mg
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup loinc --code 718-7
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup atc_who --code N02BE01
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup snomed --code 387517004
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup rxnorm --code 161
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup unii --code 362O9ITL9D
podman-compose run --rm run-plausibility-check lookup --config /app/terminology.example.toml --lookup edqm --code 10219000
```

JSON includes state, source, canonical display and returned edition where available. API coverage and licensing differ. [National downloads, ASK/PZN and MII mTLS](../../docs/lookups.md#medication-terminology-sources) explain the additional setup. Supply licensed RF2 data to a suitable terminology server first; this runner does not ingest RF2 itself.

## Input contract

One TOML selects one resource type. `[input.columns]` maps the following logical roles to actual CSV or database columns. Unknown roles fail before scanning. Do not relabel an ATC code as a PZN or assume that an ATC class proves a product's strength.

| Role | Meaning |
| --- | --- |
| `product_code`, `product_system`, `product_version`, `product_name` | Source coding/edition/label. Minimum flat product fields: code and system. All alternate codings use `medication_json`. |
| `form_text`, `ingredient_text`, `strength_numerator_value/code/system`, `strength_denominator_value/code/system` | Convenience map for **one** source-declared ingredient/strength. Does not create catalogue evidence. |
| `medication_json` | Alternative to all flat product fields: complete FHIR Medication including all ingredients, codings, form, strength, amount and extensions. Nested ingredient references require further review. |
| `status`, `intent` | Resource status (optional for standalone Medication); intent additionally required for requests. These are not the report status. |
| `dosage_instructions_json` | Request: full array of R4 `dosageInstruction`; Statement: full array of R4 `dosage` objects, including all doseAndRate entries, timing, PRN and maximum-dose constraints. |
| `dosage_json` | Administration: R4 dosage object with dose, rate, route/site/method. |
| `duration_value`, `duration_code`, `duration_system`, `constant_rate` | Administration: actual elapsed duration and explicit constant/uninterrupted assertion, if known. No invented duration. |
| `do_not_perform` | Request: optional true/false prohibition flag. Prohibited/inactive records do not get strength transformations. |
| `count`, `id`, `resource_type` | Optional true frequency, source ID, resource-type cross-check. Individual input permits count 1 only; absent distinct counts remain unknown. |

Missing, nonnumeric, nonpositive, censored and non-UCUM quantities are distinguished. Every supplied coding is sent only to a configured service for its actual system. Required profile bindings/slicing remain the full FHIR validator's responsibility. Timing primitives use [FHIR UnitsOfTime](https://hl7.org/fhir/R4/valueset-units-of-time.html); they are not arbitrary UCUM expressions.

The grouping key retains **product/ingredients/strength + all dosages/routes/timing + status/intent + duration + modifier/context fields**. Different regimens are never averaged into one dose. `output.mode="items"` retains IDs/locators and requires `input.granularity="individual"`. Distinct output excludes top-level resource IDs; free-text fields may still identify people, so treat all reports as confidential source-derived data.

### FHIR or database input

- Use `request-fhir.toml` / `administration-fhir.toml` / `statement-fhir.toml` / `product-fhir.toml` for the supplied complete synthetic Bundles. They enable local full FHIR validation, followed by the medication semantic checks. These minimal examples can have MII findings; they are not asserted to conform.
- A resource, resource array, Bundle or NDJSON export is accepted. Linked `Medication` must be contained or included in the export. References are resolved locally; missing references are flagged, never fetched from a public server. Conflicting versions and duplicate IDs fail.
- For full FHIR JSON in a CSV: `type="csv"`, `granularity="individual"`, and **only** `[input.columns] resource_json="resource_json"`.
- PostgreSQL/SQLite use the same medication mappings and [database connection settings](../../docs/configuration.md#postgresql-and-sqlite). Select a site-provided view; include all required dosing fields as JSON columns. A database `resource_json` cell must be a self-contained resource/Bundle/array with its linked Medication. Direct DB JSON receives semantic checks; export it first for formal validation.

### Full validation and real catalogues

Prepare the validator with the **same medication TOML** before running its FHIR recipe. [Validator setup](../../docs/validation.md#docker-or-podman-setup):

```sh
uv run --no-sync --env-file .env run-plausibility-check validator-init --config examples/medication/request-fhir.toml --overwrite
docker compose --profile validation up -d --build blaze matchbox
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/request-fhir.toml
```

Podman: replace `docker compose` with `podman-compose`. `validator-init` generates files only; rebuild Matchbox after changing guide packages. Its `--overwrite` replaces generated validator configuration, not source input. Without a selected guide, validation falls back to the matching international R4 resource. Scalar CSV rows never count as full-resource validation.

`request.toml` and `administration.toml` enable actual NLM UCUM and a public FHIR terminology test server. Public coverage/availability is not guaranteed, and failure remains `unknown`. For your server, edit `endpoint`, `systems` and `token_env` (secret in `.env`). For downloaded BfArM/other real artifacts, enable `lookups.downloads`, point `path` to their directory and list their **actual** canonical systems. A supplement cannot establish complete membership, and a partial list cannot disprove a missing code. Local CSV catalogues use `system,code,version,display`; SQLite uses the same `codes` table. No strength is inferred from their labels or an ATC lookup.

## Output and implemented limits

- `status`: `error` for supported inconsistencies, `needs_review` for unknowns/conditional evidence, `passed` only when the implemented checks found no issue. Never clinical approval.
- CSV keeps the source set, product codings, dosages, counts, terminology states, canonical names, findings, sources and transformations. HTML presents these beside each set. Summary/evidence/audit files accompany it; `verify` checks their hashes.
- Positive finite quantities, timing counts/periods/maxima, dose-range ordering, FHIR choice conflicts, complete strength ratios and supplied code/unit membership are checked.
- Volume↔ingredient mass proposals require compatible verified UCUM units and exact positive source strength. Multi-ingredient mass-to-volume ambiguity abstains. Modifiers, inactive/prohibited records or errors suppress strength transformations. Every proposed conversion remains conditional.
- PRN schedules, titration, rate ranges/ratios and max-dose constraints are preserved and marked for review; no daily total or patient-specific limit is invented. Only rateQuantity × elapsed duration is integrated.
- Product/form/route **compatibility** is not established merely by valid individual codes. No comprehensive product catalogue, dose appropriateness model, medication AI research or automatic FHIR rewrite is claimed.

A standalone Medication has no dose requirement (`dose_check=not_applicable`). A Statement describes reported medication use; it is not evidence of an administration. `not-taken`, inactive/prohibited states and modifiers suppress dose transformations. Missing structured dosage remains a review task. The two new CSVs contain two distinct source rows each.

The product/statement examples use **German ATC 2026**. Install `bfarm.terminologien.atcgm#2026.0.0`, then enable their `lookups.downloads` pointing at `local/terminologies/atc-2026` (the supplied nested TOMLs use `../../local/…`). Until then code membership remains unknown. Their `ddd_references_json` contains exact published DDD text, edition and source where available; it is never a patient dose limit or a product strength. [Local ATC/DDD setup](../../docs/lookups.md#local-atcddd).

Normative sources: [MII Medication 2026.0.1](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Medikation_2026/MIIIGModulMedikation.html), [R4 MedicationRequest](https://hl7.org/fhir/R4/medicationrequest.html), [R4 MedicationAdministration](https://hl7.org/fhir/R4/medicationadministration.html), [R4 MedicationStatement](https://hl7.org/fhir/R4/medicationstatement.html), [R4 Medication](https://hl7.org/fhir/R4/medication.html), [R4 Dosage](https://hl7.org/fhir/R4/dosage.html), [WHO ATC N02BE01](https://atcddd.fhi.no/atc_ddd_index/?code=N02BE01).
