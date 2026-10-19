# Medication development checks — version 2 foundation

The documented operator workflow is **laboratory Observation**. The following medication code already exists and is retained with regression tests:

| Resource | Existing technical checks |
| --- | --- |
| `Medication` | Product/ingredient/form terminology and source-declared strength quantities |
| `MedicationRequest` / `MedicationStatement` | Every dosage instruction, dose/unit, route and timing; conditional strength arithmetic |
| `MedicationAdministration` | Dose/rate/duration; consistency when the source explicitly declares constant delivery |

CSV, mapped database columns and native FHIR inputs feed this adapter. Complete FHIR file/CSV documents can use the shared validator with the selected profile; internal examples pin the MII medication guide. CSV/HTML retain supplied data, actual terminology evidence and review findings. Clinical dose applicability requires physician review.

Run the existing technical examples with:

```sh
bash tools/run-podman-examples.sh --include-medication --lookup-only
```

This runs laboratory A1 and five medication regression examples, with real terminology services and separate reports. Their developer configurations are under `tests/configurations/medication/`. The normal four-mode launcher runs laboratory examples only.

## Planned for version 2

Build on these adapters with a unified product–strength–dose–route–timing assessment, medication-specific conditional matches and scope results, AI/evidence research, profile/terminology onboarding and clinician feedback. A2/A3 and the five laboratory assessment scopes currently require `Observation`.
