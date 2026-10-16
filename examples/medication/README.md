# Medication development fixtures

The operator workflow currently focuses on laboratory Observations. Configure `pipeline.toml` using the [laboratory manual](../lab/README.md).

The existing Medication, MedicationRequest, MedicationAdministration and MedicationStatement adapters and illustrative CSV/Bundle inputs remain covered by regression tests. Their internal configurations are under `tests/configurations/medication/`. Run their real-service checks explicitly with `bash tools/run-podman-examples.sh --include-medication --lookup-only` from a source checkout.

Medication onboarding and evidence research are the next workstream. Existing A1 checks cover terminology, structured quantities, form/route/timing context and conditional dose arithmetic; physician review establishes clinical appropriateness.
