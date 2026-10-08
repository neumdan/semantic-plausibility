"""Local unit catalogue and catalogue-before-research scenarios."""

from copy import deepcopy
import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from semantic_plausibility.audit import Checker
from semantic_plausibility.model_schemas import response_schema, validate_grounding, validate_response
from semantic_plausibility.observation_quality import LoincTerm, UnitEvidence
from semantic_plausibility import research_prompts as prompts
from semantic_plausibility.snapshot_services import SnapshotLoinc
from semantic_plausibility.unit_catalog import (
    UCUM,
    FiniteUnitCodeSource,
    UnitSpelling,
    codesystem_document,
    sync_units,
    ucum_document,
)
import test_audit as fixtures
from test_audit import record

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "tests/fixtures"
ESSENCE = b"""<root xmlns="http://unitsofmeasure.org/ucum-essence" version="test">
<prefix Code="m"><name>milli</name></prefix><prefix Code="u"><name>micro</name></prefix>
<base-unit Code="g"><name>gram</name></base-unit><unit Code="L" isMetric="yes"><name>litre</name></unit>
<unit Code="mol" isMetric="yes"><name>mole</name></unit></root>"""


def catalog(path, common=None):
    document = ucum_document(ESSENCE)
    document["common_units"] = common or ["mmol/L", "ug/dL"]
    path.write_text(json.dumps({"format_version": 1, "systems": [document]}))
    return path


class Units:
    source = "synthetic_validator"

    def __init__(self, events=None):
        self.events = events if events is not None else []

    def is_valid(self, unit):
        self.events.append("exact:" + unit)
        return unit in {"mmol/L", "ug/dL", "g/dL", "mg", "mL", "10^6", "newValidExpression"}

    def factor(self, source, target):
        return Decimal(1) if source == target else None


class Model:
    def __init__(self, selected="mmol/L", events=None):
        self.selected, self.prompts = selected, []
        self.events = events if events is not None else []

    def json(self, instruction, evidence):
        self.events.append("llm_spelling")
        self.prompts.append(deepcopy(evidence))
        return {"selected_unit": self.selected, "reason": "Closest documented candidate; requires review"}


class CatalogueTests(unittest.TestCase):
    def test_ucum_inventory_preserves_all_definitions_and_prefixes_without_claiming_all_expressions(self):
        document = ucum_document(ESSENCE)
        self.assertEqual(len(document["units"]), 3)
        self.assertEqual(len(document["prefixes"]), 2)
        self.assertFalse(document["expressions_complete"])
        with self.assertRaises(ValueError):
            ucum_document(b"<!DOCTYPE x><root/>")

    def test_valid_expression_absent_from_finite_inventory_never_becomes_a_typo(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            result = UnitSpelling(catalog(Path(folder) / "catalog.json"), Units(), model).check(
                UCUM, "newValidExpression"
            )
            self.assertEqual(result["state"], "valid_spelling")
            self.assertEqual(model.prompts, [])

    def test_spelling_candidate_is_exact_validated_bounded_and_has_no_inferred_factor(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            result = UnitSpelling(catalog(Path(folder) / "catalog.json"), Units(), model).check(
                UCUM, "mmo/L", ["mmol/L"]
            )
            self.assertEqual(result["selected_unit"], "mmol/L")
            self.assertIsNone(result["factor"])
            self.assertTrue(result["requires_expert_review"])
            self.assertLessEqual(len(result["candidates"]), 8)
            self.assertTrue(all(row["validation"] == "valid" for row in result["candidates"]))
            self.assertEqual(
                set(model.prompts[0]),
                {
                    "source_unit",
                    "system",
                    "catalogue_version",
                    "candidates",
                    "documented_code_units",
                    "source_code_valid",
                    "context_conflict",
                },
            )

    def test_valid_but_incompatible_unit_can_have_a_review_candidate_without_becoming_invalid(self):
        class ValidMho(Units):
            def is_valid(self, unit):
                return unit == "mmo/L" or super().is_valid(unit)

        with tempfile.TemporaryDirectory() as folder:
            spelling = UnitSpelling(catalog(Path(folder) / "catalog.json"), ValidMho(), Model())
            self.assertEqual(spelling.check(UCUM, "mmo/L")["state"], "valid_spelling")
            candidate = spelling.check(UCUM, "mmo/L", ["mmol/L"], context_conflict=True)
            self.assertTrue(candidate["source_code_valid"])
            self.assertEqual(candidate["selected_unit"], "mmol/L")
            self.assertIsNone(candidate["factor"])

    def test_model_cannot_invent_a_unit_or_a_factor_and_can_abstain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = catalog(Path(folder) / "catalog.json")
            result = UnitSpelling(path, Units(), Model("invented-unit")).check(UCUM, "mmo/L")
            self.assertIsNone(result["selected_unit"])
            self.assertTrue(result["errors"])
            abstention = UnitSpelling(path, Units(), Model(None)).check(UCUM, "mmo/L")
            self.assertEqual(abstention["state"], "llm_abstained")
            with self.assertRaises(ValueError):
                validate_response(
                    {"selected_unit": "mmol/L", "reason": "x", "factor": 1000},
                    response_schema(prompts.UNIT_SPELLING),
                )
            with self.assertRaises(ValueError):
                validate_grounding(
                    prompts.UNIT_SPELLING,
                    {"selected_unit": "mg/mL", "reason": "x"},
                    {"candidates": [{"unit": "mmol/L", "validation": "valid"}]},
                )

    def test_bare_multiplier_and_a_different_system_do_not_trigger_typo_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            spelling = UnitSpelling(catalog(Path(folder) / "catalog.json"), Units(), model)
            self.assertIsNone(spelling.check(UCUM, "10^6")["selected_unit"])
            self.assertEqual(spelling.check("urn:other-units", "mmo/L")["state"], "not_configured")
            self.assertEqual(model.prompts, [])

    def test_finite_complete_codesystem_has_versioned_membership_and_rejects_fragments(self):
        resource = {
            "resourceType": "CodeSystem",
            "content": "complete",
            "url": "urn:finite-units",
            "version": "1",
            "concept": [{"code": "drop", "concept": [{"code": "tablet"}]}],
        }
        source = FiniteUnitCodeSource(codesystem_document(resource, source="fixture.json"))
        self.assertEqual(source.check("urn:finite-units", "tablet", "1")["state"], "valid")
        self.assertEqual(source.check("urn:finite-units", "unknown", "1")["state"], "invalid")
        self.assertEqual(source.check("urn:finite-units", "tablet", "2")["state"], "unknown")
        for changes in ({"content": "fragment"}, {"version": ""}, {"url": UCUM}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                codesystem_document({**resource, **changes}, source="fixture")

    def test_local_inventory_import_is_offline_and_writes_a_readable_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / "essence.xml", Path(folder) / "units.json"
            source.write_bytes(ESSENCE)
            with patch("semantic_plausibility.unit_catalog._download") as download:
                result = sync_units(destination, source_file=source)
            download.assert_not_called()
            self.assertEqual(result["definitions"], 3)
            with destination.with_suffix(".csv").open(encoding="utf-8-sig") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 5)

    def test_catalogue_metadata_is_required_and_ucum_cannot_claim_expression_completeness(self):
        with tempfile.TemporaryDirectory() as folder:
            path = catalog(Path(folder) / "catalog.json")
            data = json.loads(path.read_text())
            for field in ("source", "units", "expressions_complete"):
                bad = deepcopy(data)
                bad["systems"][0].pop(field)
                path.write_text(json.dumps(bad))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    UnitSpelling(path, Units())
            data["systems"][0]["expressions_complete"] = True
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                UnitSpelling(path, Units())


class ResearchOrderTests(unittest.TestCase):
    def test_catalogue_llm_precedes_website_and_pubmed_without_patient_data_in_prompt(self):
        events = []

        class Evidence:
            llm = Model(events=events)

            def website_units(self, code, term):
                events.append("website")
                return [UnitEvidence("mmol/L", "website_example", "https://example.org/source")]

            def suggest_units(self, unit, term, found):
                events.append("website_llm")
                return []

            def website_ranges(self, code, term):
                events.append("website_ranges")
                return []

            def search_studies(self, term):
                events.append("pubmed")
                return []

        with tempfile.TemporaryDirectory() as folder:
            checker = Checker(
                ucum=Units(events),
                loinc=SnapshotLoinc(
                    {
                        "synthetic": LoincTerm(
                            "synthetic",
                            "Synthetic lab",
                            system="Bld",
                            scale="Qn",
                            example_ucum_units=["mmol/L"],
                        )
                    }
                ),
                evidence=Evidence(),
                research=True,
                unit_catalog=catalog(Path(folder) / "catalog.json"),
            )
            source = record(123456.789, "mmo/L", identifier="PRIVATE_IDENTIFIER")
            source.resource["code"]["coding"][0]["code"] = "synthetic"
            for bound in ("low", "high"):
                source.resource["referenceRange"][0][bound]["code"] = "mmol/L"
            _, rows, evidence, _ = fixtures.AuditTests().check([source], checker=checker)
            self.assertLess(events.index("llm_spelling"), events.index("website"))
            self.assertLess(events.index("website"), events.index("pubmed"))
            prompt = json.dumps(Evidence.llm.prompts)
            self.assertNotIn("PRIVATE_IDENTIFIER", prompt)
            self.assertNotIn("123456", prompt)
            self.assertEqual(rows[0]["vorschlag_einheit"], "mmol/L")
            self.assertEqual(rows[0]["vorschlag_faktor"], "")
            self.assertEqual(rows[0]["vorschlag_grundlage"], "catalogue_llm_spelling_candidate")
            self.assertIsNone(evidence[0]["recommendation"]["transformation"]["factor"])

    def test_reference_unit_typo_is_reported_per_bound_without_rewriting_its_value_or_factor(self):
        with tempfile.TemporaryDirectory() as folder:
            source = record()
            source.resource["referenceRange"][0]["low"]["code"] = "g/dll"
            term = LoincTerm("718-7", "Synthetic Hb", system="Bld", scale="Qn", example_ucum_units=["g/dL"])
            checker = Checker(
                ucum=Units(),
                loinc=SnapshotLoinc({"718-7": term}),
                evidence=SimpleNamespace(
                    llm=Model("g/dL"), search_studies=lambda term: [], website_ranges=lambda code, term: []
                ),
                research=True,
                unit_catalog=catalog(Path(folder) / "catalog.json", ["g/dL"]),
            )
            _, rows, evidence, _ = fixtures.AuditTests().check([source], checker=checker)
            self.assertIn("referenceRange[0].low: g/dL", rows[0]["schreibweise_kandidaten"])
            candidate = evidence[0]["recommendation"]["reference_unit_candidates"][0]
            self.assertIsNone(candidate["factor"])
            self.assertEqual(source.resource["referenceRange"][0]["low"]["value"], 12)
            plan = evidence[0]["recommendation"]["transformation"]
            self.assertNotEqual(plan["state"], "verified_linear")


class ReferenceCatalogueTests(unittest.TestCase):
    def test_resource_mode_preserves_reference_indices_and_tuple_mode_can_still_group_reordered_ranges(self):
        first = record()
        first.resource["referenceRange"] = [
            {"low": {"value": 20}, "high": {"value": 30}},
            {"low": {"value": 10}, "high": {"value": 15}},
        ]
        second = deepcopy(first)
        second.resource["referenceRange"].reverse()
        _, _, resource_evidence, _ = fixtures.AuditTests().check([first], mode="resources")
        self.assertEqual(resource_evidence[0]["metadata"]["reference_ranges"][0]["low"]["value"], "20")
        summary, _, _, _ = fixtures.AuditTests().check([first, second], mode="tuples")
        self.assertEqual(summary["assessed_items"], 1)

    def test_proven_invalid_reference_unit_is_an_error_independently_of_value_unit_validity(self):
        source = record()
        source.resource["referenceRange"][0]["low"]["code"] = "fake"
        _, rows, evidence, _ = fixtures.AuditTests().check([source])
        self.assertEqual(rows[0]["einheit_pruefung"], "gültig")
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(evidence[0]["reference_metadata"][0]["unit_checks"]["low"]["state"], "invalid")


if __name__ == "__main__":
    unittest.main()
