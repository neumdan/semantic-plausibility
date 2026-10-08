# Example inputs

`laboratory.csv` contains 8 illustrative input rows and `laboratory.bundle.json` the equivalent 84 individual FHIR Observations. Measurement values/reference intervals are examples, not clinical recommendations. Neither file contains lookup verdicts.

The default configuration contacts real NLM UCUM and FHIR LOINC services. `lab/` contains source/profile configurations; `profiles/` preserves the attributed pinned MII snapshot. `studies/` contains upstream SQL/CQL selection examples and example export-selection manifests. No code mappings or terminology responses are invented.
