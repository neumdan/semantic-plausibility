"""Synthetic input contracts and real-service medication checks; no lookup doubles."""

from contextlib import closing
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from semantic_plausibility.batch import BatchConfiguration, run_plausibility_check
from semantic_plausibility.deployment_checks import check_configuration
from semantic_plausibility.evidence_research import research_plan
from semantic_plausibility.fhir_validation import target_profile, validation_copy
from semantic_plausibility.medication_checks import MedicationChecker
from semantic_plausibility.medication_input import from_row, normalize
from semantic_plausibility.study_compare import verify_audit

ROOT = Path(__file__).resolve().parents[1]


def configuration(folder, stem="request", *, native=False, live=False, validate=False):
    source = ROOT / "examples/medication" / (stem + ("-fhir" if native else "") + ".toml")
    text = source.read_text()
    input_path = (
        source.parent / (stem + "-bundle.json")
        if native
        else ROOT / "examples" / ("medication-" + stem + ".csv")
    )
    text = text.replace(
        'path = "' + (stem + "-bundle.json" if native else "../medication-" + stem + ".csv") + '"',
        'path = "' + str(input_path) + '"',
    )
    text = text.replace(
        'path = "../../output/medication-' + stem + ("-fhir" if native else "") + '.csv"',
        'path = "' + str(folder / "result.csv") + '"',
    )
    if not live:
        text = text.replace('mode = "lookups"', 'mode = "offline"')
    if native and not validate:
        text = text.replace("enabled = true # Complete synthetic", "enabled = false # Complete synthetic")
    path = folder / "pipeline.toml"
    path.write_text(text)
    return path


def input_rows(stem):
    with (ROOT / "examples" / ("medication-" + stem + ".csv")).open() as f:
        return list(csv.DictReader(f))


def details(result):
    return [json.loads(line) for line in result.csv.with_suffix(".evidence.jsonl").read_text().splitlines()]


class MedicationTests(unittest.TestCase):
    def test_all_recipes_run_offline_unknown_is_not_success(self):
        for stem, expected in [("request", 3), ("administration", 2), ("statement", 2), ("product", 2)]:
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as name:
                config = configuration(Path(name), stem)
                self.assertTrue(check_configuration(config)["passed"])
                result = run_plausibility_check(config)
                self.assertEqual(result.input_records, expected)
                self.assertEqual(result.statuses, {"needs_review": expected})
                self.assertTrue(all(x["clinical_dose_safety"] == "not_assessed" for x in details(result)))
                self.assertEqual(verify_audit(result.log)["status"], "verified")
                self.assertNotIn("source_id", result.csv.read_text().splitlines()[0])
                self.assertIn("Source:", result.report.read_text())
                self.assertNotIn("strength_conversion", result.csv.read_text())
                with self.assertRaisesRegex(ValueError, "Output already exists"):
                    run_plausibility_check(config)

    def test_malformed_negative_and_string_quantities_and_all_instructions(self):
        with tempfile.TemporaryDirectory() as name:
            c = BatchConfiguration(configuration(Path(name)))
            row = input_rows("request")[0]
            original = json.loads(row["dosage_instructions_json"])
            bad = deepcopy(original[0])
            bad["doseAndRate"][0]["doseQuantity"]["value"] = -4
            bad["timing"]["repeat"].update(frequency=2, frequencyMax=1, period=0)
            other = deepcopy(original[0])
            other["doseAndRate"][0]["doseQuantity"]["value"] = "5"
            row["dosage_instructions_json"] = json.dumps(original + [bad, other])
            data, _, _ = from_row(row, c)
            with c.deployment.prepare() as prepared:
                checker = MedicationChecker(c, prepared)
                try:
                    result = checker.assess(data)
                    self.assertEqual(result["status"], "error")
                    errors = [f for f in result["findings"] if f["state"] == "error"]
                    self.assertTrue(any("dosage[1]" in f["path"] for f in errors))
                    self.assertTrue(any("dosage[2]" in f["path"] for f in errors))
                finally:
                    checker.close()

    def test_liquid_tablet_and_nonpositive_strength_are_flagged(self):
        with tempfile.TemporaryDirectory() as name:
            c = BatchConfiguration(configuration(Path(name)))
            row = input_rows("request")[2]
            row["strength_denominator_value"] = "0"
            data, _, _ = from_row(row, c)
            with c.deployment.prepare() as prepared:
                checker = MedicationChecker(c, prepared)
                try:
                    result = checker.assess(data)
                    self.assertEqual(result["status"], "error")
                    self.assertTrue(any("liquid" in f["message"] for f in result["findings"]))
                    self.assertTrue(
                        any("denominator" in f["path"] and f["state"] == "error" for f in result["findings"])
                    )
                finally:
                    checker.close()

    def test_grouping_keeps_timing_route_prn_and_unknown_frequency(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder)
            rows = input_rows("request")
            a = rows[0]
            b = deepcopy(a)
            b["count"] = ""
            change = deepcopy(a)
            dose = json.loads(change["dosage_instructions_json"])
            dose[0]["asNeededBoolean"] = True
            dose[0]["route"]["text"] = "Other route"
            change["dosage_instructions_json"] = json.dumps(dose)
            file = folder / "input.csv"
            with file.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(a))
                w.writeheader()
                w.writerows([a, b, change])
            path.write_text(
                path.read_text().replace(str(ROOT / "examples/medication-request.csv"), str(file))
            )
            result = run_plausibility_check(path)
            self.assertEqual(result.input_records, 3)
            self.assertEqual(result.distinct_tuples, 2)
            with result.csv.open(encoding="utf-8-sig") as f:
                out = list(csv.DictReader(f))
            self.assertEqual(sorted(r["source_rows"] for r in out), ["1", "2"])
            self.assertEqual(next(r for r in out if r["source_rows"] == "2")["occurrence_count"], "")

    def test_number_and_numeric_string_do_not_collapse_into_one_group(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder)
            good = input_rows("request")[0]
            bad = deepcopy(good)
            dose = json.loads(bad["dosage_instructions_json"])
            dose[0]["doseAndRate"][0]["doseQuantity"]["value"] = "5"
            bad["dosage_instructions_json"] = json.dumps(dose)
            source = folder / "numbers.csv"
            with source.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(good))
                writer.writeheader()
                writer.writerows([good, bad])
            path.write_text(
                path.read_text().replace(str(ROOT / "examples/medication-request.csv"), str(source))
            )
            result = run_plausibility_check(path)
            self.assertEqual(result.distinct_tuples, 2)
            self.assertEqual(result.statuses, {"error": 1, "needs_review": 1})

    def test_native_reference_resolution_item_ids_and_no_patient_identifiers(self):
        for stem in ("request", "administration", "statement", "product"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as name:
                path = configuration(Path(name), stem, native=True)
                path.write_text(path.read_text().replace('mode = "distinct"', 'mode = "items"'))
                result = run_plausibility_check(path)
                data = details(result)[0]["source_set"]
                self.assertEqual(data["medication"]["code"]["coding"][0]["code"], "N02BE01")
                self.assertFalse(data["input_issues"])
                self.assertIn("example-" + stem, result.csv.read_text())
                self.assertNotIn("example-patient", result.csv.read_text())
                if stem == "administration":
                    self.assertEqual(data["duration"]["value"], 1800)
                if stem == "statement":
                    self.assertTrue(data['dosages'])
                    self.assertFalse(data['duration'])

    def test_statement_not_taken_and_optional_product_status_dose(self):
        with tempfile.TemporaryDirectory() as name:
            path = configuration(Path(name), 'statement')
            result = run_plausibility_check(path)
            entry = next(d for d in details(result) if d['source_set']['status'] == 'not-taken')
            self.assertTrue(any('not-taken' in f['message'] for f in entry['findings']))
            self.assertFalse(any(p['type'] == 'strength_conversion' for p in entry['recommendations']))
            c = BatchConfiguration(path)
            with self.assertRaisesRegex(ValueError, 'array'):
                normalize({'resourceType': 'MedicationStatement', 'dosage': {}}, {}, c)
            path = configuration(Path(name), 'product')
            c = BatchConfiguration(path)
            row = input_rows('product')[0]
            row['status'] = ''
            data, _, _ = from_row(row, c)
            with c.deployment.prepare() as prepared:
                checker = MedicationChecker(c, prepared)
                try:
                    assessed = checker.assess(data)
                    self.assertFalse(any(f['path'] in {'status', 'dosage'} for f in assessed['findings']))
                finally:
                    checker.close()

    def test_native_duplicate_ids_and_unknown_references_fail_or_flag(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder, native=True)
            bundle = json.loads((ROOT / "examples/medication/request-bundle.json").read_text())
            item = bundle["entry"][-1]["resource"]
            item["medicationReference"]["reference"] = "Medication/missing"
            file = folder / "bundle.json"
            file.write_text(json.dumps(bundle))
            path.write_text(
                path.read_text().replace(str(ROOT / "examples/medication/request-bundle.json"), str(file))
            )
            result = run_plausibility_check(path)
            self.assertTrue(details(result)[0]["source_set"]["input_issues"])
            bundle["entry"].append({"resource": deepcopy(item)})
            file.write_text(json.dumps(bundle))
            with self.assertRaisesRegex(ValueError, "standardization"):
                run_plausibility_check(path, output="different.csv")

    def test_csv_database_and_resource_json_equivalence(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder)
            csv_result = run_plausibility_check(path)
            rows = input_rows("request")
            database = folder / "input.sqlite"
            with closing(sqlite3.connect(database)) as db:
                columns = list(rows[0])
                db.execute("CREATE TABLE medication (" + ",".join('"' + x + '" TEXT' for x in columns) + ")")
                db.executemany(
                    "INSERT INTO medication VALUES (" + ",".join("?" for _ in columns) + ")",
                    [[r[x] for x in columns] for r in rows],
                )
                db.commit()
            text = (
                path.read_text()
                .replace('type = "csv"', 'type = "sqlite"', 1)
                .replace(str(ROOT / "examples/medication-request.csv"), str(database))
            )
            path.write_text(text + '\n[database]\nDB_TABLE = "medication"\n')
            result = run_plausibility_check(path, output="db.csv")
            self.assertEqual(
                [d["source_set"] for d in details(result)], [d["source_set"] for d in details(csv_result)]
            )
            native = configuration(folder, native=True)
            first = run_plausibility_check(native, output="native.csv")
            jsoncsv = folder / "resources.csv"
            with jsoncsv.open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["resource_json"])
                w.writerow([(ROOT / "examples/medication/request-bundle.json").read_text()])
            native.write_text(
                native.read_text()
                .replace('type = "fhir_file"', 'type = "csv"')
                .replace(str(ROOT / "examples/medication/request-bundle.json"), str(jsoncsv))
                + '\n[input.columns]\nresource_json = "resource_json"\n'
            )
            second = run_plausibility_check(native, output="jsoncsv.csv")
            self.assertEqual(
                [d["source_set"] for d in details(first)], [d["source_set"] for d in details(second)]
            )
            self.assertEqual(first.statuses, second.statuses)

    def test_guard_output_configuration_and_unsupported_research(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            config = configuration(folder)
            with self.assertRaisesRegex(ValueError, "AI recommendations"):
                research_plan(config)
            c = BatchConfiguration(config)
            before = config.read_bytes()
            c.output.update(path=str(config), overwrite=True)
            from semantic_plausibility.export_review import review_export

            with self.assertRaises(ValueError):
                review_export(c)
            self.assertEqual(config.read_bytes(), before)

    def test_validation_targets_selected_medication_resource_only(self):
        with tempfile.TemporaryDirectory() as name:
            c = BatchConfiguration(configuration(Path(name), native=True))
            bundle = json.loads((ROOT / "examples/medication/request-bundle.json").read_text())
            old = deepcopy(bundle)
            new = validation_copy(bundle, target_profile(c), c.kind)
            self.assertEqual(bundle, old)
            self.assertEqual(new["entry"][-1]["resource"]["meta"]["profile"], [target_profile(c)])
            self.assertNotIn("meta", new["entry"][1]["resource"])
            c.data["specification"] = {}
            self.assertEqual(
                target_profile(c), "http://hl7.org/fhir/StructureDefinition/MedicationRequest|4.0.1"
            )

    def test_missing_validator_is_visible_and_audited(self):
        with tempfile.TemporaryDirectory() as name:
            path = configuration(Path(name), native=True, validate=True)
            path.write_text(
                path.read_text().replace("http://127.0.0.1:8081/matchboxv3/fhir", "http://127.0.0.1:9/fhir")
            )
            result = run_plausibility_check(path)
            self.assertEqual(result.fhir_validation_state, "unavailable")
            self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_validator_setup_honours_configured_directory(self):
        from semantic_plausibility.fhir_validation import initialise_validator

        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            path = configuration(folder, native=True)
            destination = (folder / "generated").resolve()
            path.write_text(
                path.read_text().replace(
                    'setup_directory = "../../local/validation"', 'setup_directory = "generated"'
                )
            )
            result = initialise_validator(path)
            self.assertTrue(all(Path(p).is_relative_to(destination) for p in result["files"]))
            self.assertIn(
                "de.medizininformatikinitiative.kerndatensatz.medikation#2026.0.1", result["packages"]
            )
            self.assertTrue((destination / "matchbox/Dockerfile").exists())

    def test_period_and_count_contracts(self):
        with tempfile.TemporaryDirectory() as name:
            c = BatchConfiguration(configuration(Path(name), "administration"))
            data = normalize(
                {
                    "resourceType": "MedicationAdministration",
                    "effectivePeriod": {"start": "2026-01-01T11:00:00+01:00", "end": "2026-01-01T10:30:00Z"},
                },
                {},
                c,
            )
            self.assertEqual(data["duration"]["value"], 1800)
            for bad in ("0", "1.5", "-4", "NaN", "999999999999999999999999"):
                row = input_rows("administration")[0]
                row["count"] = bad
                with self.assertRaisesRegex(ValueError, "count"):
                    from_row(row, c)
            row = input_rows("administration")[0]
            row["constant_rate"] = "yes"
            with self.assertRaisesRegex(ValueError, "true or false"):
                from_row(row, c)

    @unittest.skipUnless(
        os.getenv("SQC_LIVE_MEDICATION_TESTS") == "1", "opt in to actual medication terminology/UCUM calls"
    )
    def test_real_services_strength_conversion_and_infusions(self):
        for stem in ("request", "administration"):
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as name:
                path = configuration(Path(name), stem, live=True)
                result = run_plausibility_check(path)
                rows = details(result)
                # Fail, rather than fake a response, when an explicitly requested live service is unavailable.
                proposals = [p for r in rows for p in r["recommendations"]]
                self.assertTrue(
                    any(
                        p["type"] == "strength_conversion" and p["proposed_value"] == "100" for p in proposals
                    )
                    if stem == "request"
                    else any(p["type"] == "infusion_comparison" for p in proposals)
                )
                if stem == "administration":
                    self.assertEqual(
                        sorted(p["matches"] for p in proposals if p["type"] == "infusion_comparison"),
                        [False, True],
                    )
                    self.assertIn("error", result.statuses)
                self.assertTrue(all(p["source"] and p["review_required"] for p in proposals))
                self.assertTrue(all("clinical_dose_safety" in r for r in rows))
