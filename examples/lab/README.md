# Laboratory source configurations

- `lis.toml`: illustrative CSV with real terminology services.
- `fhir.toml`: equivalent illustrative FHIR R4 Bundle; native fields.
- `mii-lis.toml`: CSV checked against selected rules from the pinned official MII profile.

Paths are relative to each TOML for uv. For Compose, copy a source configuration to repository-root `pipeline.toml` and adjust paths to that root; the container mounts it at `/app/pipeline.toml`. No terminology fixtures are supplied. Configure a genuine catalogue for local codes and your own/authenticated server for studies. See [configuration](../../docs/configuration.md).
