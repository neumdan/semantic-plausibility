# Changelog

## Unreleased

- Add bounded Europe PMC open-access XML/table extraction with exact cell locations, role guards and visible exclusions.
- Compare input statistics and reference bounds locally with published evidence; preserve missing-metadata conditions and source values.

- Document laboratory objectives, limits and a lean clinician validation protocol.
- Add `study-template` and `study-metrics`: audited report → clinician CSV → sensitivity, recommendation/evidence quality and review-effort tables, with explicit denominators.

## 0.15.0

- Separate missing source metadata from demonstrated semantic errors.
- Retrieve exact LOINC/UCUM candidates in lookup-only runs when source systems are absent; retain original fields and require confirmation.
- Show conditional code/name/unit matches, ordered corroboration and a short review reason.
- Publish five assessment scopes with independent completeness, terminology compatibility and physiological evidence results in CSV, HTML and audited JSONL.
- Keep analytical performance evidence separate from clinical reference intervals; retain source quotation and unit checks.
- Include the pending research, input, documentation and optional experimental Laya updates. Local models remain optional; clinician approval remains explicit.

See [tests and implementation](docs/implementation.md) for offline contracts and real-service integration checks.
