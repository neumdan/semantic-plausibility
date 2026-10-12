# Where laboratory semantic review fits in MII/FDPG quality checks

Reviewed against the official projects on **7 October 2026**. This describes compatible interfaces and complementary scopes; it does not claim certification or a live integration test with every listed deployment.

## Three complementary checks

| Layer | Tool / output | Question answered |
| --- | --- | --- |
| Formal FHIR conformance | MII FHIR Validator → OperationOutcome | Is this individual resource conformant to the selected FHIR/IG constraints and terminology bindings? |
| Availability and completeness | FHIR Data Evaluator (FDE) → MeasureReport + DocumentReference | Which criteria/elements are present in the node's data? |
| Laboratory semantic sets | This library → local CSV/HTML/JSON | Is the declared code–unit–value–reference set supported, consistent and reviewable? What transformation/mapping needs approval? |

The [MII FHIR Validator](https://github.com/medizininformatik-initiative/mii-fhir-validator) is locally deployable and returns OperationOutcome from `/validateResource`. Its documented offline deployment uses installed IG packages/dependencies and a local terminology service such as Blaze. Prepare the required terminology content locally. Public/shared terminology servers are not automatically suitable for production ETL; follow their access policy.

The [FDE documentation](https://medizininformatik-initiative.github.io/dataportal/data-node/fhir-data-evaluator.html) describes `cdsCodingAvailability` and `DseElementAvailability` metadata reports for the FDPG/data node. **Our summary is not an FDE MeasureReport** and is not submitted to its server/DSF. Keep the measures side by side with their own denominators. A high availability ratio does not prove appropriate laboratory units or reference context.

## Placement in a hospital workflow

1. **LIS/DB:** export the selected measurements or distinct value-frequency sets. Preserve local code identity, full references and qualifiers. Review semantic mapping proposals locally.
2. **FHIR conversion:** apply only physician-approved mappings in the institution's ETL; transform value and every quantitative reference bound with its independently verified factor. Run official FHIR/IG validation on generated individual resources.
3. **FHIR server/export:** use the site's TORCH/CRTDL or supported CQL selection. Review exported laboratory sets with this same library. Compare with LIS only when cohort/time/status/deduplication and semantic selection agree.
4. **CDS Hub/research:** review a mapped laboratory export/view and compare its sets/frequencies with earlier stages. FDE remains the availability/completeness layer; this report records potential semantic loss or improvement.

[TORCH](https://medizininformatik-initiative.github.io/torch/implementation/implementation-overview.html) uses CRTDL cohort/extraction definitions and produces NDJSON. It can select cohorts using FHIR Search or CQL. Preserve the laboratory elements needed here in that extraction. This library consumes the files; it does not submit CRTDL jobs or replace TORCH consent/reference handling.

[Blaze CQL](https://samply.github.io/blaze/implementation/cql.html) documents server operations including `$cql` and `$evaluate-measure`. Check the actual server's capabilities. The generic `Library/$evaluate` operation is not a universal export protocol. A count-only MeasureReport cannot reconstruct measurement distributions or references.

[CDS Toolchain / INTERPOLAR](https://github.com/medizininformatik-initiative/INTERPOLAR) remains the upstream data processing environment. This runner follows familiar `DB_*` TOML connection names and can reuse its explicitly selected connection file. A site-approved scalar view defines the data contract; the runner has no assumed CDS Hub schema.

## FHIR, MII and NUM profiles

International fallback: [FHIR R4 Observation 4.0.1](https://hl7.org/fhir/R4/observation.html). The declared code system is checked against its configured source; it is not inferred from a familiar-looking code. A FHIR CodeableConcept may have alternative codings. A required concept binding can be satisfied by one member coding; explicit coding-slice constraints still apply.

For MII, use the **deployed package release**, not moving GitHub master. The [official laboratory module](https://github.com/medizininformatik-initiative/kerndatensatzmodul-labor) lists stable and ballot releases separately. For NUM, use the exact installed [NUM/GECCO profiles](https://github.com/hl7germany/forschungsnetz-covid19) relevant to the site's dataset; there is no universal NUM laboratory rule applied automatically.

```toml
[specification]
FHIR_VERSION = "4.0.1"
PROFILE = "fhir-r4-lab"       # fallback for unspecified selected elements
STRUCTURE_DEFINITION = "local/ObservationLab.snapshot.json"
IG_PACKAGE = "de.medizininformatikinitiative.kerndatensatz.laborbefund"
IG_VERSION = "YOUR_DEPLOYED_RELEASE"
IG_CANONICAL = "https://www.medizininformatik-initiative.de/fhir/core/modul-labor/StructureDefinition/ObservationLab"

[lookups.installed_artifacts]
enabled = true
type = "fhir_artifacts"
path = "local/terminology"
systems = ["http://loinc.org", "http://snomed.info/sct"]
```

The unchanged published **MII Laboratory 2026.0.3** resolved snapshot is included under `examples/profiles/`, with attribution and SHA. Run `run-plausibility-check --config examples/lab/mii-lis.toml` to exercise it on the illustrative measurement input with real terminology services. For your institution, copy the active, nonexperimental Observation **resolved snapshot** from its pinned installed package. The importer projects selected code bindings, fixed/pattern quantity systems, required code/system fields and selected explicit prohibitions. It records canonical/version/SHA and unsupported FHIRPath constraints in `summary.imported_profile`. Use `PROFILE_FILE` instead for a reviewed selected-element policy; do not configure both.

**Import scope is deliberately partial:** whole-resource cardinalities, complete slicing, invariants, reference target conformance, demographic applicability and other IG rules require the full validator. A package name or `meta.profile` declaration is provenance, not proof. Unspecified selected rules use the implemented R4 fallback; supported explicit prohibitions are retained.

Install exact CodeSystem content/expanded ValueSets with versions. A partial expansion or an unavailable edition yields unknown membership, not a fabricated invalid result. This adapter does not perform arbitrary ValueSet compose expansion or package dependency resolution. LOINC term axes/example units and UCUM syntax/factors still need the appropriate local pack or explicit dedicated lookups.

## The ratio: a clearly labelled proxy

`mii_numeric_set_ready` requires:

- Explicit verified LOINC code and UCUM unit system/code.
- Documented unchanged fit or independently verified linear conversion to a LOINC example unit.
- Finite, ordered observed numeric values/bounds; no invalid point in the group.
- At least one supplied numerically valid reference interval, with valid numeric bounds for all supplied intervals.
- No deterministic error, ambiguous LOINC identity/version or uninterpreted modifier.

Report `numerator / all selected output groups` and, when every source frequency is known, `occurrences in entirely ready groups / all represented source occurrences`. In items mode the denominator is selected measurement items. A missing value inside a mixed group is counted separately; this proxy describes group metadata/numeric availability, not validation of each patient event.

“Ready” does **not** establish that a supplied reference interval is correct for the specimen, assay, age, sex or population. Specimen/method compatibility is assessed only for supplied resolved context; native Specimen codes are retained without automatically equating SNOMED to a LOINC axis. `clinical_*_evidence` stays separate. Out-of-reference values are descriptive; pathological results may be perfectly correct.

An approved unit conversion still needs application/revalidation in ETL. The report does not manufacture patient Observations from aggregate means. `fhir_mapping_proposal_json` describes a plan and missing resource context, not a conformant patient resource.

## Evidence and reviewed mappings

The default examples now start with the pinned MII Laboratory 2026.0.3 snapshot. Local LIS code/unit membership is assessed independently; target coding is proposed according to the selected bindings and quantitative unit constraints. LOINC is preferred in this snapshot's code binding; UCUM is fixed for the value Quantity. Versioned supplied ValueSets support system routing, not automatic filter expansion or complete IG conformance.

`research-evidence` consumes the audited export, retrieves bounded reported-practice evidence and writes its prompts/search trace. Clinician decisions enter a scoped registry; future checks reverify compatible approvals. [Requirements](requirements.md) · [Review workflow](research.md#clinician-feedback). FDPG study access remains a future authorised provider/export integration.
