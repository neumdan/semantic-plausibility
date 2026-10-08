# Examples

All clinical-looking data and reference intervals here are **synthetic**.

- `studies/`: distinct LIS CSV, equivalent FHIR Bundle, reference pack, local codes/ConceptMap and illustrative SQL/CQL extraction.
- `lab/`: current TOML configurations for LIS CSV, FHIR files, mapped PostgreSQL and the pinned MII laboratory snapshot.
- `profiles/`: attributed published MII Laboratory 2026.0.3 snapshot and its provenance.

Start with `pipeline.example.toml` in the project root. Regenerate the synthetic comparison with `python tools/run_study_demo.py --out output/study-demo`. Replace synthetic reference evidence before reviewing real exports.
