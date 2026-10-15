# Validate FHIR resources with the selected guide

Use the local **Matchbox service, which embeds the official HL7 FHIR validator**, or an existing MII FHIR Validator. Validate actual FHIR JSON Bundles/resources before selecting elements and grouping, including complete JSON documents supplied in CSV cells. Scalar CSV/database tuples receive semantic checks; aggregate means are not manufactured into patient resources.

## Configuration

The starter uses FHIR R4 and MII Laboratory **2026.0.3**. `specification.STRUCTURE_DEFINITION` supplies the selected profile; `IG_PACKAGE` and `IG_VERSION` pin the complete guide and its dependencies. `PROFILE_VERSION` can override the profile's business version when no snapshot is supplied. Profile and package versions are separate concepts.

```toml
[validation]
enabled = true
backend = "matchbox"
endpoint = "http://127.0.0.1:8081/matchboxv3/fhir"
container_endpoint = "http://matchbox:8080/matchboxv3/fhir"
terminology_server = "http://blaze:8080/fhir"
terminology_endpoint = "http://127.0.0.1:8082/fhir"
timeout_seconds = 180
fail_on_error = true
```

`enabled` controls automatic validation when `input.type="fhir_file"` or CSV maps only `input.columns.resource_json`. The explicit `validate-fhir` command always requests validation. Container runners use `container_endpoint`; native Python uses `endpoint`. Only local/LAN destinations are accepted. An optional `token_env` or `token_file` authenticates a protected local validator.

`validator-init` writes server configuration from these same settings. Matchbox installs the pinned guide and transitive dependencies during its connected image build. Its runtime network is internal; full resources stay local. Changing the guide or terminology server requires regenerating configuration with `--overwrite` and rebuilding.

## Docker or Podman setup

Run from the repository directory after preparing `.env`, `pipeline.toml`, `local/`, `output/` and building the runner once as described in [the setup guide](shipped-stack.md). The commands below prepare the validator for the first run or a changed guide:

```sh
podman-compose --profile validation run --rm validator-init
podman-compose --profile validation up -d blaze
podman-compose --profile validation run --rm terminology-init
podman-compose --profile validation up -d --build matchbox
podman-compose --profile validation ps
```

For Docker replace `podman-compose` with `docker compose`. Matchbox depends on local Blaze for terminology checks. Allow the first build/start to finish; inspect `podman-compose --profile validation logs --tail=30 matchbox` if it is not healthy. Access the local Matchbox interface at `http://127.0.0.1:8081/matchboxv3/`.

For later runs with the same guide, use `podman-compose --profile validation up -d blaze matchbox` and then the check command. This reuses the image and Blaze volume. The [example launcher](../README.md#architecture-and-decision-flow) handles these reuse/build decisions automatically.

Allocate **16 GiB** to the Docker/Podman VM when running the 3B Ollama model and validation together; Matchbox is limited to 4 GiB and Blaze to 2 GiB. On macOS, a stopped Podman machine can be resized:

```sh
podman machine stop
podman machine set --memory 16384
podman machine start
```

Stop active workloads before resizing. Native Linux uses host memory and does not need a VM.

## Two concrete FHIR examples

The [complete illustrative Bundle](../examples/fhir/laboratory-valid.bundle.json) contains a Patient, Organization and hemoglobin Observation. The Observation has the MII-required OBI identifier and assigner, both laboratory category codings, subject, effective time and UCUM Quantity. The reference bounds illustrate conversion only.

The [invalid variant](../examples/fhir/laboratory-invalid.bundle.json) removes that required identifier. The analyte/unit/value set is otherwise identical: semantic numeric readiness cannot detect the missing identifier, whereas full MII profile validation can.

```sh
podman-compose --profile validation run --rm validate-fhir validate-fhir --config /app/pipeline.toml --input /app/examples/fhir/laboratory-valid.bundle.json --out /app/output/fhir-valid.jsonl
podman-compose --profile validation run --rm validate-fhir validate-fhir --config /app/pipeline.toml --input /app/examples/fhir/laboratory-invalid.bundle.json --out /app/output/fhir-invalid.jsonl
```

Python can call the same running container through its published local port:

```sh
uv run --no-sync --env-file .env run-plausibility-check validate-fhir --config pipeline.toml --input examples/fhir/laboratory-valid.bundle.json --out output/fhir-valid-python.jsonl
```

The JSONL retains full OperationOutcomes, requested profile/packages, source/submitted resource hashes, retrieval time and response hash. A summary gives issue counts. Exit code 1 means invalid, incomplete or unavailable validation. Choose a fresh `--out` for each run.

## CSV to CSV with full validation

After the service setup above, use the supplied recipe. It reads the complete Bundle in `examples/laboratory-fhir.csv` and writes `output/laboratory-fhir-review.csv` and HTML:

```sh
uv run --no-sync --env-file .env run-plausibility-check --config examples/lab/fhir-csv.toml
# Docker alternative
docker compose run --rm run-plausibility-check check --config /app/examples/lab/fhir-csv.toml
# Podman alternative
podman-compose run --rm run-plausibility-check check --config /app/examples/lab/fhir-csv.toml
```

To try the invalid CSV, copy the recipe to ignored `local/fhir-csv.toml`, adjust its relative paths to that folder and select `examples/laboratory-fhir-invalid.csv` through the correct relative path. Use a fresh output prefix. The difference is a missing MII identifier; it is not a unit error. For a simpler negative test, use the existing explicit invalid-Bundle command above.

A scalar CSV such as `laboratory.csv` cannot prove resource conformance. Enabling validation for scalar input yields `not_applicable`; it never manufactures patient Observations from aggregated values.

## Validate and review your FHIR export together

Copy `pipeline.fhir.example.toml` to `pipeline.fhir.toml`, then select it using `SQC_CONFIG_FILE=pipeline.fhir.toml` in `.env` or a selector file. Configure the actual Bundle/NDJSON path. Remove CSV column mappings; native Observation fields are used.

```sh
uv run --no-sync --env-file .env run-plausibility-check --config pipeline.fhir.toml
```

For the supplied example, Docker/Podman can select the file without changing `.env`:

```text
# local/fhir.env
SQC_CONFIG_FILE=pipeline.fhir.toml
```

```sh
podman-compose --env-file local/fhir.env --profile validation run --rm run-plausibility-check
```

The HTML shows formal validation separately from semantic findings. `*.fhir-validation.jsonl` contains the validator's outcomes and is included in the artifact hashes. CSV fields `fhir_validation_state` and `fhir_validation_scope` describe **input-document validation**, not a separate conformance decision for each aggregated group. With `fail_on_error=true`, the CLI returns 1 for invalid/incomplete/unavailable validation and retains the review report.

Whole Bundles retain their reference context. NDJSON documents are validated independently; this does not prove reference completeness across files. Existing `meta.profile` declarations are retained. The selected Observation profile is added to a temporary validation copy, so undeclared source Observations are also checked against the target guide. Source files are unchanged.

## Terminology coverage and MII alternative

Blaze is configured for LOINC and its supported local terminology capabilities. `terminology-init` installs the explicitly supplied real ValueSets and complete CodeSystems and records source hashes. The MII Laboratory snapshot retains the historical IPS laboratory ValueSet canonical, which IPS 2.0.0 renamed; the supplied published 1.1.0 definition resolves that binding explicitly. No definition or binding is invented. Other required CodeSystems/ValueSets, including licensed SNOMED CT content when used, must be available in the institution's terminology service. BfArM translation supplements do not replace complete terminology content. Missing bindings or unresolved profiles remain visible in the OperationOutcome; unresolved warnings are labelled `incomplete`.

To use an existing MII service, set `backend="mii"`, `endpoint="http://127.0.0.1:8083"` and its actual local `container_endpoint`. Requests use `/validateResource`, with the target profile on the temporary resource copy. The service must load the pinned guide.

For the included optional MII container, first run `validator-init`, then:

```sh
podman-compose -f compose.yaml -f compose.mii.yaml --profile mii-validation up -d mii-validator
```

Use `container_endpoint="http://mii-validator:8080"`. The overlay is separate so ordinary setup does not require its generated environment file. Use one selected validation backend per run; Matchbox already runs the HL7 validator.

Sources: [Matchbox validation API](https://ahdis.github.io/matchbox/validation/), [container deployment](https://ahdis.github.io/matchbox/docker/), [MII validator](https://github.com/medizininformatik-initiative/mii-fhir-validator), [MII Laboratory 2026.0.3](https://www.medizininformatik-initiative.de/Kerndatensatz/KDS_Laborbefund_2026/MIIIGModulLaborbefund.html).

## Medication profiles

`examples/medication/request-fhir.toml` and `administration-fhir.toml` select MII Medication 2026.0.1. Generate and rebuild the validator using the chosen medication configuration before running it. The requested canonical profile is attached to copies of the selected resource type only; linked Medication and Patient resources are preserved for full Bundle validation. Original input is unchanged. The small supplied Bundles are synthetic validation inputs, not claimed MII-conformant examples. See the [medication commands and limits](../examples/medication/README.md#full-validation-and-real-catalogues). Without an explicit guide, the fallback is the corresponding international R4 resource profile. Scalar medication sets and direct database JSON receive semantic checks only.
