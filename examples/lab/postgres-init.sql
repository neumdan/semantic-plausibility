-- Synthetic input only. Real terminology checks use configured APIs.
CREATE TABLE laboratory_summary (
  "code" text,
  "name" text,
  "code_system" text,
  "unit" text,
  "unit_code" text,
  "unit_system" text,
  "count" text,
  "mean" text,
  "sd" text,
  "min" text,
  "max" text,
  "mean_unit" text,
  "sd_unit" text,
  "min_unit" text,
  "max_unit" text,
  "reference_low" text,
  "reference_high" text,
  "specimen" text,
  "status" text
);
COPY laboratory_summary FROM '/seed/aggregated.csv' WITH (FORMAT csv, HEADER true);
