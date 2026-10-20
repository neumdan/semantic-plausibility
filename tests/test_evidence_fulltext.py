"""Parser/arithmetic contracts and opt-in real-service checks; no simulated lookups."""
from decimal import Decimal
import os
import unittest

from semantic_plausibility.study_fulltext import fulltext_passages
from semantic_plausibility.study_sources import screen_record, provider_query
from semantic_plausibility.model_schemas import validate_grounding
from semantic_plausibility.evidence_comparison import compare_numbers
from semantic_plausibility import research_prompts


# Structural parser example, not a terminology response, study or clinical interval.
LAYOUT = """<article><body><table-wrap id="t1"><caption><p>Reference intervals</p></caption>
<table><thead><tr><th>Analyte</th><th>Unit</th><th>Sex</th><th>N</th>
<th>Reference interval</th><th>95% CI lower limit</th><th>Mean (SD)</th></tr></thead>
<tbody><tr><td>Analyte A</td><td>mg/L</td><td>M</td><td>100</td><td>1-2</td><td>0.9-1.1</td><td>1.5 (0.1)</td></tr>
<tr><td>Analyte A</td><td>mg/L</td><td>F</td><td>200</td><td>3-4</td><td>2.9-3.1</td><td>3.5 (0.2)</td></tr>
<tr><td>Analyte B</td><td>mg/L</td><td>M</td><td>100</td><td>5-6</td><td>4.9-5.1</td><td>5.5 (0.3)</td></tr>
</tbody></table></table-wrap></body></article>"""


class FullTextContracts(unittest.TestCase):
    def test_cells_keep_units_strata_and_roles_without_borrowing_ci_or_counts(self):
        parts = fulltext_passages(LAYOUT, ["Analyte A"])["passages"]
        self.assertEqual(len(parts), 4)
        cells = [p["table_cell"] for p in parts]
        self.assertEqual([c["bounds"]["low"] for c in cells if c["bounds"]], ["1", "3"])
        self.assertEqual([c["measurements"] for c in cells if c["measurements"]],
                         [[("mean", "1.5"), ("sd", "0.1")], [("mean", "3.5"), ("sd", "0.2")]])
        self.assertEqual({c["sample_size"] for c in cells}, {"100", "200"})
        self.assertTrue(all(p["location"].startswith("table[t1]/") for p in parts))
        self.assertFalse(any("Analyte B" in p["excerpt"] or "95% CI" in p["excerpt"] for p in parts))

    def test_guard_rejects_swapped_sd_mean_or_interval_strata(self):
        parts = fulltext_passages(LAYOUT, ["Analyte A"])["passages"]
        evidence = {"abstract": "\n".join(p["excerpt"] for p in parts),
                    "analyte_definition": {"aliases": ["Analyte A"]},
                    "table_cells": {p["excerpt"]: p["table_cell"] for p in parts}}
        ref = next(p for p in parts if p["table_cell"]["bounds"])
        ctx = {"unit": "mg/L", "excerpt": ref["excerpt"], "population": "", "specimen": "", "method": ""}
        good = {**ctx, **ref["table_cell"]["bounds"]}
        validate_grounding(research_prompts.STUDY_BOUNDS, good, evidence)
        with self.assertRaises(ValueError):
            validate_grounding(research_prompts.STUDY_BOUNDS, {**good, "high": "4"}, evidence)
        mean = next(p for p in parts if p["table_cell"]["measurements"])
        wrong = {**ctx, "excerpt": mean["excerpt"], "value_kind": "sd", "reported_value": "1.5"}
        with self.assertRaisesRegex(ValueError, "labelled cell"):
            validate_grounding(research_prompts.STUDY_MEASUREMENTS, {"measurements": [wrong]}, evidence)

    def test_ambiguous_layout_and_missing_unit_abstain(self):
        broken = LAYOUT.replace("<td>mg/L</td>", "", 1)
        self.assertEqual(fulltext_passages(broken, ["Analyte A"])["skipped_tables"], ["t1"])
        self.assertFalse(fulltext_passages(LAYOUT.replace("mg/L", ""), ["Analyte A"])["passages"])
        self.assertFalse(fulltext_passages(LAYOUT.replace("Reference intervals", "Method differences"), ["Analyte A"])["passages"])

    def test_jats_alternatives_supported_without_fetching_images(self):
        xml = LAYOUT.replace("<table>", "<alternatives><graphic href='untrusted'/><table>").replace(
            "</table>", "</table></alternatives>")
        self.assertEqual(len(fulltext_passages(xml, ["Analyte A"])["passages"]), 4)

    def test_only_source_defined_row_abbreviations_are_resolved(self):
        xml = LAYOUT.replace("Analyte A", "AA")
        self.assertFalse(fulltext_passages(xml, ["Analyte A"])["passages"])
        xml = xml.replace("</table-wrap>", "<table-wrap-foot><p>AA - Analyte A.</p></table-wrap-foot></table-wrap>")
        self.assertEqual(len(fulltext_passages(xml, ["Analyte A"])["passages"]), 4)
        self.assertTrue(all(p["table_cell"]["alias_definition"] == "AA - Analyte A"
                            for p in fulltext_passages(xml, ["Analyte A"])["passages"]))

    def test_hypothetical_extreme_is_not_observed_evidence(self):
        from semantic_plausibility.model_schemas import supports_evidence_type
        self.assertFalse(supports_evidence_type("documented_extreme",
            "The proposed interval would push the highest analyte A value to 10 mg/L.", ""))

    def test_entities_rejected_and_superscripts_preserved(self):
        with self.assertRaisesRegex(ValueError, "entity"):
            fulltext_passages("<!DOCTYPE a [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><a>&x;</a>", ["A"])
        xml = LAYOUT.replace("mg/L", "10<sup>3</sup>/µL")
        self.assertEqual(fulltext_passages(xml, ["Analyte A"])["passages"][0]["table_cell"]["unit"], "10^3/µL")

    def test_table_admission_does_not_require_abstract_numbers(self):
        parsed = fulltext_passages(LAYOUT, ["Analyte A"])
        record = {"title": "Human reference intervals", "abstract": "", **parsed}
        record["evidence_text"] = "\n".join(p["excerpt"] for p in parsed["passages"])
        self.assertGreater(screen_record(record, ["Analyte A"])[0], 0)
        self.assertEqual(screen_record({**record, "title": "Reference intervals in European mink"},
                                      ["Analyte A"])[1], "explicit_nonhuman_title")
        self.assertNotIn("TITLE_ABS", provider_query('"Analyte A"', "europe_pmc", full_text=True))


class ComparisonContracts(unittest.TestCase):
    def test_compare_like_statistics_and_scale_sd_without_inventing_intervals(self):
        source = {"mean": "5", "sd": "2"}
        found = compare_numbers(source, [], {"evidence_type": "reported_measurement",
            "value_kind": "mean", "reported_value": "40"}, Decimal("0.1"))
        self.assertEqual(found[0]["difference"], "1.0")
        self.assertEqual(found[0]["statistic"], "mean")
        sd = compare_numbers(source, [], {"evidence_type": "reported_measurement",
            "value_kind": "sd", "reported_value": "10"}, Decimal("0.1"))
        self.assertEqual(sd[0]["published_value"], "1.0")
        self.assertNotIn("published_low", found[0])

    def test_range_discrepancy_is_a_descriptive_relation_not_an_error(self):
        found = compare_numbers({"minimum": "0", "maximum": "10"},
            [{"index": 1, "low": Decimal("3"), "high": Decimal("4")}],
            {"evidence_type": "reference_interval", "low": "1", "high": "2"}, Decimal(1))
        self.assertEqual([r["relation"] for r in found], ["below_reference", "above_reference", "disjoint_bounds"])
        self.assertFalse(any("error" in r for r in found))
        self.assertEqual(compare_numbers({}, [], {"evidence_type": "analytical_interval",
            "low": "1", "high": "100"}, Decimal(1)), [])


@unittest.skipUnless(os.getenv("SQC_LIVE_RESEARCH_TESTS") == "1", "Enable real Europe PMC retrieval")
class RealFullTextTests(unittest.TestCase):
    def test_real_potassium_table_retains_published_sex_strata_and_source(self):
        from semantic_plausibility.observation_services import PublicEvidenceClient
        client = PublicEvidenceClient(research_sources=("europe_pmc",), europe_pmc_retmax=1)
        record = client._europe_pmc_records("DOI:10.1371/journal.pone.0342530")[0]
        client._add_full_text(record, ["Potassium"])
        cells = [p for p in record["passages"] if p.get("table_cell", {}).get("bounds")]
        pairs = {(p["table_cell"]["bounds"]["low"], p["table_cell"]["bounds"]["high"]) for p in cells}
        self.assertTrue({("3.80", "4.98"), ("3.82", "4.99")} <= pairs)
        self.assertNotIn(("3.79", "3.82"), pairs)  # CI of lower limit, not an interval
        self.assertEqual(len(record["full_text_sha256"]), 64)
        self.assertTrue(record["source_license"])
        self.assertTrue(all(p["location"].startswith("table[") for p in cells))
