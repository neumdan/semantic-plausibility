# Example inputs

Measurements, bounds and dosage records are synthetic examples, not clinical recommendations. Terminology lookups use real configured services; these files contain no lookup verdicts.

| File | Shape | Current use |
| --- | --- | --- |
| [laboratory.csv](laboratory.csv) | 8 distinct scalar rows with real-in-example counts | Default runnable input: 7 guarded groups / 84 represented source occurrences |
| [laboratory.bundle.json](laboratory.bundle.json) | Equivalent 84 individual Observations in a Bundle | Semantic grouping comparison; not a complete MII conformance example |
| [laboratory-fhir.csv](laboratory-fhir.csv) | One CSV `resource_json` cell containing a complete synthetic Bundle | Runnable full validation + semantic check with [lab/fhir-csv.toml](lab/fhir-csv.toml) |
| [laboratory-fhir-invalid.csv](laboratory-fhir-invalid.csv) | Same Bundle with its required MII identifier removed | Negative full-validation example; numeric set unchanged |
| [laboratory-research.csv](laboratory-research.csv) | 6 local/name/unit cases | Map `analyte_name="analyte_name"`, then check before AI research |
| [medication-request.csv](medication-request.csv) | 3 distinct product/strength/dose/timing sets | Executable terminology/dose check; see [recipes](medication/README.md) |
| [medication-statement.csv](medication-statement.csv) | 2 distinct reported-use sets, including not-taken | German ATC catalogue required for membership; not an administration |
| [medication-product.csv](medication-product.csv) | 2 product/strength sets | No dosage requirement; optional published ATC/DDD context |
| [medication-administration.csv](medication-administration.csv) | 2 distinct dose/rate/duration sets | Executable terminology/dose check; see [recipes](medication/README.md) |

The scalar laboratory cases show: documented same-unit fit; an out-of-reference value; `140 g/L → 14 g/dL` if a factor of 0.1 is verified; leukocyte `10*3/uL`; ambiguous bare `10*6`; a non-number; absent reference bounds; and a local LIS code needing source catalogue verification and a target coding proposal. The supplied reference intervals illustrate behavior only. AI findings depend on actual retrieved evidence and model responses.

Use CSV `count` only for known frequencies. `input.granularity="individual"` permits `output.mode="items"` with source IDs; distinct mode produces guarded sets and eligible value distributions. JSON-in-CSV uses individual granularity and only a `resource_json` mapping: each cell may hold a complete resource, Bundle or resource array. Scalar rows cannot establish full resource conformance.

`lab/` contains executable source/profile recipes. `profiles/` preserves the attributed pinned MII snapshot and referenced ValueSet. `fhir/` contains the complete/invalid resource documents used by the CSV examples. `studies/` contains upstream SQL/CQL selection examples and export-selection manifests. The SQL names describe a **site-provided view**, not a universal CDS Hub schema; adapt selected tables/columns at your site. [Configuration](../docs/configuration.md) · [Medication field contract](medication/README.md).
