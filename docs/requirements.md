# Goal and laboratory pipeline requirements

## Goal

Support correct semantic filling of the selected MII KDS laboratory Observation from LIS, FHIR or research exports. A complete set is **code + code system/edition + analyte name + value or observed bounds + unit + unit system + every reference interval and its context**. Professionals decide semantic correctness using source definitions, terminology and evidence of reported practice.

The first deployment uses MII Laboratory **2026.0.3**, a stable published release. Its resolved snapshot supplies the selected rules. The referenced laboratory ValueSet makes LOINC a preferred binding; the quantitative value system is fixed to UCUM. A preferred binding is not a universal prohibition of local codings. [Official module](https://github.com/medizininformatik-initiative/kerndatensatzmodul-labor), [terminology guidance](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Laborbefund_2026/MIIIGModulLaborbefund-TechnischeImplementierung-Terminologien.html).

## Required behaviour

| Requirement | Implementation or boundary |
| --- | --- |
| Explicit input and configuration | Mapped CSV/SQLite/PostgreSQL or native R4 Bundle/NDJSON; LIS APIs, TORCH/CQL and vendor exports remain upstream. |
| Whole-set analysis | Preserve alternate codings, versions, names, comparators, specimen/method/status and complete reference qualifiers. DuckDB groups guarded sets and eligible distributions; items mode retains source IDs. |
| Check source meaning first | Actual source code/unit membership and documented source unit expectations are separate from target-profile compatibility. Numeric references can be compared in an exact verified source unit. Missing source catalogues yield unknown, not invented definitions. |
| Full resource/guide validation | For actual FHIR inputs, call the selected local Matchbox/HL7 or MII validator before aggregation; retain OperationOutcomes and unresolved coverage. CSV/DB aggregates are not patient resources. |
| Derive target systems from the profile | Import fixed/pattern systems and bindings; supplied real ValueSets resolve their systems. Unspecified selected elements use R4 fallback; explicit prohibitions remain effective. Endpoint URLs/credentials are deployment configuration. |
| Explicit terminology roles | Primary services verify targets, source services describe the LIS, optional sources provide extra labels/candidates. An optional positive result cannot reverse a confirmed primary rejection. |
| Propose interoperable coding | Retrieve candidates from actual names/aliases/catalogues; verify code/edition, units and available axes. Local unit displays may be UCUM candidates, but a display does not establish source-code equivalence or a conversion factor. |
| Transform the whole set coherently | A plan includes the value and each reference bound with its own unit. Verified linear conversions and clinician-declared reinterpretations have different provenance. Missing denominators, mixed units, offsets and unsupported context require explicit resolution. |
| Separate evidence research | Research a checked export per exact LOINC/edition with cached, bounded actual page/PubMed/Europe PMC retrieval. Retain reported values, units and interval quotations plus population/material/method. No automatic semantic-correctness verdict or widening of intervals to fit observations. |
| Professional feedback | Import approved/rejected/deferred/revoked decisions, reviewer/time/evidence and full context. Reuse only matching approvals; recheck targets, retain history and revocations. No automatic model training or resource rewrite. |
| Transparent execution | Preview exact prompts/planned searches without network calls; save actual query/model traces, source hashes and a chained artifact audit. Local measurement values/reference bounds and patient IDs stay out of public requests/model inputs. Public study measurements are part of retrieved evidence. |
| Portable local operation | One Python library/runner; uv/direct Python, Docker Compose and Podman Compose. Ollama/LM Studio are optional local inference services; secrets/data/registries stay outside Git. |
| Reviewable output | CSV and HTML show source checks, target checks, proposals, transformations, evidence and review state. Original numeric readiness and approved-mapping readiness have separate denominators. |

## Scope of the decision

The project produces a mapping plan, not a patient Observation from aggregate statistics. Applying that plan requires source resource identity, subject, timing and other mandatory fields in the institution's ETL. The configured local FHIR/IG validator checks actual input and resulting individual resources. FDE availability/completeness measures remain complementary.

A reference interval, analytical measurement range and reported extreme are different evidence types. Age/sex, assay/platform, calibration, preanalytical conditions and population can limit applicability. Literature quotations and unit compatibility support plausibility; clinician adjudication establishes semantic correctness in the defined source context.

## Later domain adapters

Medication needs ingredient/product identity, strength numerator/denominator, dose/unit, route, frequency/timing and status; ATC alone does not identify all of these. Condition needs code/edition, verification/clinical status, severity/stage and body site. Procedure needs code/edition, status, timing, site and technique. Timing primitives can use FHIR units-of-time while dose Quantities use an IG-selected unit system; do not assume every unit element is UCUM. Each adapter must load its actual MII profile/bindings and source catalogues before mapping.

FDPG access is not a public literature lookup. A future provider must use an authorised feasibility/research interface or permitted aggregate export, retain selection/cohort provenance and keep observed practice separate from reference-interval validation.

## Medication checks

MedicationRequest and MedicationAdministration are implemented for source terminology, unit/quantity checks, timing/range consistency and conditional strength/infusion calculations. The [executable recipes and contract](../examples/medication/README.md) retain every ingredient and dosage instruction. The published MII Medication 2026.0.1 guide is selected for optional full-resource validation.

Source strength is not catalogue evidence. Clinical dose appropriateness, product/form/route compatibility beyond the explicit lexical check, medication AI literature research and clinician-registry reuse remain future work. ATC alone cannot establish formulation or concentration. Condition and Procedure still need domain adapters.
