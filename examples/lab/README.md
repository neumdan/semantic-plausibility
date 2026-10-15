# Laboratory source configurations

- `lis.toml`: illustrative CSV with real terminology services.
- `fhir.toml`: equivalent illustrative FHIR R4 Bundle; native fields.
- `mii-lis.toml`: CSV checked against selected rules from the pinned official MII profile.

Paths are relative to each TOML for uv. Run recipes in place. Compose selects them with `--config /app/examples/lab/lis.toml` (or another recipe); Python uses `--config examples/lab/lis.toml`. Root `pipeline.toml` is a separate starter. If copying a nested recipe elsewhere, adjust its relative paths. No terminology fixtures are supplied. Configure a genuine catalogue for local codes and your own/authenticated server for studies. See [configuration](../../docs/configuration.md).

`fhir-csv.toml` reads the complete Bundle inside `../laboratory-fhir.csv`, validates it with the configured local MII/Matchbox service and writes `../../output/laboratory-fhir-review.csv`. After validator setup, run it in place:

```sh
uv run --no-sync --env-file .env run-plausibility-check --config examples/lab/fhir-csv.toml
# Docker alternative; use podman-compose for Podman:
docker compose run --rm run-plausibility-check check --config /app/examples/lab/fhir-csv.toml
```

Relative paths in this recipe work from its actual `examples/lab/` directory, including the Compose mount. If copying it elsewhere, adjust those paths. The CSV recipe has one mapping, `resource_json`; counts/scalar overrides are rejected.
