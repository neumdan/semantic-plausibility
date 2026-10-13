# Pinned public profile fixture

`ObservationLab-2026.0.3.json` is the published resolved snapshot from MII laboratory package `de.medizininformatikinitiative.kerndatensatz.laborbefund#2026.0.3`, downloaded on 7 October 2026.

Source: https://simplifier.net/packages/de.medizininformatikinitiative.kerndatensatz.laborbefund/2026.0.3
Official project: https://github.com/medizininformatik-initiative/kerndatensatzmodul-labor
SHA-256: `7b312044ef929ce97f4aec2f0ada8cfef1c782f60033dfb7330497ac9e40f25c`

The MII laboratory project publishes its specification under CC BY 4.0. Attribution remains with Medizininformatik-Initiative and the profile authors. This fixture is unchanged. It exercises selected-element import only; complete validation requires the profile package and dependencies in the official validator.

The default examples load this fixture and fall back to international R4 for unspecified selected elements. `examples/lab/mii-lis.toml` demonstrates explicit pinned profile selection. Use the actual deployed release for a real study.

`results-laboratory-observations-uv-ips.json` is the actual HL7 IPS ValueSet edition 1.1.0 retrieved on 10 October 2026 from https://hl7.org/fhir/uv/ips/ValueSet-results-laboratory-observations-uv-ips.json (SHA-256: `7cd3b777f5ced7778dc66b4ef57f454f59569b3b7b798ec3f0ab8fa06210ea15`). It resolves the systems of the snapshot's unversioned preferred binding. It is not claimed to resolve every MII dependency or establish ValueSet membership; deploy the full pinned package/dependencies for formal validation. Attribution: HL7 International / IPS authors; original resource content is unchanged.

The full-validation setup explicitly installs this historical ValueSet in local Blaze. IPS 2.0.0 renamed the laboratory ValueSet canonical while the MII 2026.0.3 snapshot retains the earlier URI. The published 1.1.0 definition is therefore supplied and hashed explicitly; the profile binding and resource content remain unchanged.
