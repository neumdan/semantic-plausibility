# Laboratory examples

Use **one `pipeline.toml`**. The [input manual](lab/README.md) gives the exact input/column changes; all other settings stay in that file.

| Input | Intended check | Expected grouping |
| --- | --- | --- |
| `laboratory.csv` | Starter frequency export; real UCUM/LOINC, spelling candidate, local-code review, reversed bounds | 8 rows → 7 groups; 84 occurrences |
| `lab/individual.csv` | Individual Hb values; calculate and assess statistics together | 3 values → 1 group: mean 14, sample SD 2 g/dL |
| `lab/aggregated.csv` | Pool compatible disjoint Hb summaries; retain incompatible contexts | 6 rows → 5 groups (includes one deliberate unit typo) |
| `fhir/laboratory-valid.bundle.json` | Complete Observation with Patient/Organization context and pinned MII guide | 1 laboratory Observation |
| `fhir/laboratory-invalid.bundle.json` | Same numerical set, deliberately missing required MII identifier | Formal validation finding |
| `laboratory-fhir.csv` | Complete Bundle in a CSV cell | Same Observation as the valid Bundle |
| `lab/observation.json`, `lab/observations.ndjson` | Single-resource and line-delimited FHIR input | Group by complete laboratory context |
| `laboratory.bundle.json` | FHIR export counterpart of the starter | 84 measurements → 7 groups |
| SQLite / PostgreSQL | Same individual/aggregate columns read from a table | Same statistics as the CSV counterpart |

Inputs are illustrative. Lookup outcomes depend on the configured real services and are reported with source/version. Reference limits in the examples exercise the pipeline and are not clinical guidance.

## Laboratory workflows

```sh
bash tools/run-podman-examples.sh --dry-run
bash tools/run-podman-examples.sh
```

| Report folder | Input | Checks |
| --- | --- | --- |
| `lookup` | `lab/aggregated.csv`: 6 rows → 5 groups | lookup terminology and set consistency |
| `fhir-validation` | `laboratory-fhir.csv`: one complete Observation in a Bundle | MII/HL7 validation, then lookup |
| `ai-lookup` | Audited `lookup` report and sidecars | ai-lookup sourced terminology proposals |
| `ai-evidence` | The same audited `lookup` report and sidecars | ai-evidence lookup proposals plus study evidence |

The script writes a separate folder for each workflow. It prints the report directory and creates `index.html`. `--lookup-only` runs just `lookup`. Model/terminology volumes and image layers persist.

Your own data uses `pipeline.toml` and `run-plausibility-check check`; the example script deliberately selects its own illustrative inputs. Medication development checks are retained as the starting point for version 2 under `examples/medication/` and `tests/configurations/medication/`.
