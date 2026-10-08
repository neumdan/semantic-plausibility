// Site-side illustration, NOT executed by this Python reviewer.
// Deploy with your pinned CQL/FHIR tooling and validate it there before use.
// TORCH/CRTDL is preferred for exporting the selected resources + context.
library LaboratorySelection version '1.0.0'
using FHIR version '4.0.1'

context Patient

define "LaboratoryObservations":
  [Observation] O
    where exists (
      O.category C
        where exists (
          C.coding S
            where S.system.value = 'http://terminology.hl7.org/CodeSystem/observation-category'
              and S.code.value = 'laboratory'
        )
    )

// Retain the resources rather than reducing them to CodeSystem/count metadata.
// Add approved cohort/date/status selection in your site-side extraction definition.
