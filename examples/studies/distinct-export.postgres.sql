-- Run INSIDE the hospital on your own mapped, read-only laboratory view.
-- The installation supplies semantic_review.lab; this is not a guessed CDS Hub schema.
-- First apply the SAME cohort, time/status selection at LIS/FHIR/CDS Hub.
-- The JSON field must already preserve every original reference range and qualifier.
SELECT code, code_system, code_version, value,
       unit, unit_code, unit_system,
       reference_ranges_json::text AS reference_ranges_json,
       specimen, source_test, method, status,
       COUNT(*) AS count
FROM semantic_review.lab
GROUP BY code, code_system, code_version, value,
         unit, unit_code, unit_system, reference_ranges_json::text,
         specimen, source_test, method, status;

-- A smaller min/max-only export is also supported by mapping value_min/value_max.
-- It cannot recover mean, SE, median or IQR. Do not label its rows as individuals.
