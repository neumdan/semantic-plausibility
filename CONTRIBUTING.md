# Contributing

Use illustrative measurement data for examples and bug reports, never patient data. Terminology tests use real services/artifacts; do not add fabricated lookup responses or mock terminology servers. Keep the input contract, configuration, check definitions and output columns consistent. Add a regression test when changing a semantic rule or source contract.

Run the checks in [implementation.md](docs/implementation.md). Preserve the distinction between a deterministic finding, a review proposal and clinical approval; lookup/research changes must retain source evidence and abstain when it is missing.
