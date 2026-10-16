# Internal regression configurations

These TOMLs are **test fixtures**, selected by automated tests and `tools/run-podman-examples.sh`.
Operator setup uses the single root `pipeline.example.toml` → private `pipeline.toml`.

- `lab/individual.toml`, `aggregated.toml`: arithmetic and cross-source input contracts.
- `lab/fhir.toml`, `fhir-csv.toml`: complete resources and the pinned MII validator.
- `medication/`: retained regression coverage for later medication development.
- `terminology.toml`: opt-in tests against real public/national terminology services.

Inputs are illustrative; terminology responses are obtained from real services. Offline tests check input handling and report structure. Paths resolve relative to each fixture. Keep secrets in `.env`/secret files; fixtures contain variable names only.
