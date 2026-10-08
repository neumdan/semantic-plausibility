# Active laboratory configurations

From the project root:

```sh
plausibility batch --config examples/lab/lis.toml
plausibility batch --config examples/lab/fhir.toml
plausibility compare --baseline output/lis.review.csv --target output/fhir.review.csv --out output/lis-fhir-comparison
```

Both inputs and terminology responses are synthetic. The FHIR demo explicitly supplies the fixture-wide `Bld` context; replace that only with documented facts for a real selection. Paths are relative to each TOML. Outputs are protected from overwrite. For your own PostgreSQL view use the documented sections in [the configuration guide](../../docs/configuration.md); for the complete three-source database fixture run `tools/run_study_demo.py`.

`mii-lis.toml` adds the included pinned public MII Laboratory 2026.0.3 snapshot to the same synthetic data. It demonstrates selected policy import, not full IG validation.
