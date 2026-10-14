# Medication terminology and dose checks

Executable adapters for **MedicationRequest** and **MedicationAdministration**, using FHIR R4. The examples select the published **MII Medication 2026.0.1** guide. All example clinical/formulation data are synthetic input. No synthetic terminology responses or drug catalogue are supplied.

## Run once

From the repository root, after the [standard setup](../../docs/shipped-stack.md#first-setup):

```sh
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/request.toml
uv run --no-sync --env-file .env run-plausibility-check --config examples/medication/administration.toml
```

Equivalent container calls (same files and output):

```sh
docker compose run --rm run-plausibility-check check --config /app/examples/medication/request.toml
podman-compose run --rm run-plausibility-check check --config /app/examples/medication/administration.toml
```

Open `output/medication-request.html` or `output/medication-administration.html`, with the matching CSV. Each set shows source data → terminology check/name → findings → conditional calculations, with source references. Repeat with a new `--out ../../output/medication-request-2.csv`; paths resolve relative to the selected TOML. Python can also call `semantic_plausibility.run_plausibility_check(config_path)` or the repository's `run-plausibility-check.py` wrapper.

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

## Input contract

One TOML selects one resource type. `[input.columns]` maps the following logical roles to actual CSV or database columns. Unknown roles fail before scanning. Do not relabel an ATC code as a PZN or assume that an ATC class proves a product's strength.

| Role | Meaning |
| --- | --- |
| `product_code`, `product_system`, `product_version`, `product_name` | Source coding/edition/label. Minimum flat product fields: code and system. All alternate codings use `medication_json`. |
| `form_text`, `ingredient_text`, `strength_numerator_value/code/system`, `strength_denominator_value/code/system` | Convenience map for **one** source-declared ingredient/strength. Does not create catalogue evidence. |
| `medication_json` | Alternative to all flat product fields: complete FHIR Medication including all ingredients, codings, form, strength, amount and extensions. Nested ingredient references require further review. |
| `status`, `intent` | Resource status; intent additionally required for requests. These are not the report status. |
| `dosage_instructions_json` | Request: full array of R4 `dosageInstruction` objects, including all doseAndRate entries, timing, PRN and maximum-dose constraints. |
| `dosage_json` | Administration: R4 dosage object with dose, rate, route/site/method. |
| `duration_value`, `duration_code`, `duration_system`, `constant_rate` | Administration: actual elapsed duration and explicit constant/uninterrupted assertion, if known. No invented duration. |
| `do_not_perform` | Request: optional true/false prohibition flag. Prohibited/inactive records do not get strength transformations. |
| `count`, `id`, `resource_type` | Optional true frequency, source ID, resource-type cross-check. Individual input permits count 1 only; absent distinct counts remain unknown. |

Missing, nonnumeric, nonpositive, censored and non-UCUM quantities are distinguished. Every supplied coding is sent only to a configured service for its actual system. Required profile bindings/slicing remain the full FHIR validator's responsibility. Timing primitives use [FHIR UnitsOfTime](https://hl7.org/fhir/R4/valueset-units-of-time.html); they are not arbitrary UCUM expressions.

The grouping key retains **product/ingredients/strength + all dosages/routes/timing + status/intent + duration + modifier/context fields**. Different regimens are never averaged into one dose. `output.mode="items"` retains IDs/locators and requires `input.granularity="individual"`. Distinct output excludes top-level resource IDs; free-text fields may still identify people, so treat all reports as confidential source-derived data.

### FHIR or database input

- Use `request-fhir.toml` / `administration-fhir.toml` for the supplied complete synthetic Bundles. They enable local full FHIR validation, followed by the medication semantic checks. These minimal examples can have MII findings; they are not asserted to conform.
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

Normative sources: [MII Medication 2026.0.1](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Medikation_2026/MIIIGModulMedikation.html), [R4 MedicationRequest](https://hl7.org/fhir/R4/medicationrequest.html), [R4 MedicationAdministration](https://hl7.org/fhir/R4/medicationadministration.html), [R4 Dosage](https://hl7.org/fhir/R4/dosage.html), [WHO ATC N02BE01](https://atcddd.fhi.no/atc_ddd_index/?code=N02BE01).
