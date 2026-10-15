"""Report contracts on real input parsing; no fabricated terminology responses."""
import csv
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
import unittest

from semantic_plausibility import run_plausibility_check, verify_audit
from semantic_plausibility.export_report import write_html
from semantic_plausibility.laboratory_report_table import blocked_selection, field_table_html, laboratory_fields, set_decisions
from semantic_plausibility.laboratory_suggestions import existing_code_conflicts
from semantic_plausibility.review_feedback import approved_mapping_ready
from test_batch import configuration


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.styles = 0
        self.details = 0
        self.scripts = 0
        self.item_ids = []
        self.item_tables = 0
        self.open_set_details = 0

    def handle_starttag(self, tag, attrs):
        self.styles += tag == "style"
        self.details += tag == "details"
        self.scripts += tag == "script"
        attrs = dict(attrs)
        if tag == "section" and attrs.get("class") == "item":
            self.item_ids.append(attrs.get("data-tuple-id"))
        self.item_tables += tag == "table" and attrs.get("class") == "item-table"
        if tag == "details" and attrs.get("class") == "set-details":
            self.open_set_details += "open" in attrs


class LaboratoryReportTests(unittest.TestCase):
    def test_unmapped_input_has_no_existing_definition_to_conflict_with(self):
        self.assertEqual(existing_code_conflicts(None, {}), [])
        self.assertEqual(existing_code_conflicts({}, None), [])

    def test_offline_report_has_explicit_zero_and_one_table_per_item(self):
        with tempfile.TemporaryDirectory() as name:
            result = run_plausibility_check(configuration(Path(name)))
            html = result.report.read_text()
            self.assertIn("Approved mapping ready: 0 / 7", html)
            self.assertIn("No clinician review registry configured", html)
            self.assertIn("Lookup / deterministic checks", html)
            self.assertIn("AI-supported lookup", html)
            self.assertIn("Study evidence", html)
            self.assertIn("Not run", html)
            self.assertIn("No finite numeric summary", html)
            self.assertNotIn("Retain None", html)
            parser = TableParser()
            parser.feed(html)
            self.assertEqual(parser.styles, 1)
            self.assertEqual(parser.scripts, 0)
            self.assertGreaterEqual(parser.details, 7)
            self.assertEqual(len(set(parser.item_ids)), 7)
            self.assertEqual(parser.open_set_details, 0)
            self.assertLess(html.index('class="item-table"'), html.index('class="run-details"'))
            self.assertNotIn('<th scope="col">AI → proposal</th>', html)
            for label in ("Data field", "Input value", "Method + finding", "Recommendation", "Source", "→ Proposal", "Code system", "Unit system", "Specimen"):
                self.assertIn(label, html)
            with result.csv.open(encoding="utf-8-sig") as stream:
                for row in csv.DictReader(stream):
                    decisions = set_decisions(row, json.loads(result.summary.read_text()))
                    self.assertNotEqual(decisions["Code"][0], "Keep")  # Offline membership stays unresolved.
            self.assertEqual(verify_audit(result.log)["status"], "verified")

    def test_field_table_escapes_input_and_does_not_invent_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            result = run_plausibility_check(configuration(Path(name)))
            with result.csv.open(encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
            data = json.loads(row["source_data_json"])
            data["name"] = '<script>alert("input")</script>'
            row["source_data_json"] = json.dumps(data)
            html = field_table_html(row, json.loads(result.summary.read_text()))
            self.assertIn("&lt;script&gt;", html)
            self.assertNotIn("<script>", html)
            self.assertNotIn("Retain 718-7", html)  # Offline means unverified.
            self.assertIn("No proposal", html)
            for field in laboratory_fields(row, json.loads(result.summary.read_text())):
                if field.name in {"Code", "Code system", "Analyte / name", "Unit code / display", "Unit system"}:
                    self.assertNotEqual(field.source_state, "green")
                    self.assertNotEqual(field.proposal_state, "green")

    def test_approval_is_not_inferred_and_incomplete_rechecks_fail_closed(self):
        # These are review workflow states, not terminology lookup fixtures.
        payload, stats = {"quantity": {}}, {"minimum": 0, "maximum": 1, "invalid": 0}
        for state in ("not_configured", "rejected", "deferred", "revoked", "responses_accepted"):
            self.assertFalse(approved_mapping_ready({"state": state}, payload, stats))
        decision = {"state": "reused_clinician_approval", "review_required": False}
        self.assertTrue(approved_mapping_ready(decision, payload, stats))
        self.assertFalse(approved_mapping_ready({"state": "reused_clinician_approval"}, payload, stats))
        self.assertFalse(approved_mapping_ready(decision, payload, {**stats, "invalid": 1}))
        self.assertFalse(approved_mapping_ready(decision, {"quantity": {"comparator": ">"}}, stats))


@unittest.skipUnless(os.getenv("SQC_REVIEW_REPORT_DIR"), "Provide an actual audited Podman report directory")
class RecordedRunTests(unittest.TestCase):
    def test_real_reports_render_without_changing_audited_artifacts(self):
        root = Path(os.environ["SQC_REVIEW_REPORT_DIR"])
        with tempfile.TemporaryDirectory() as name:
            for folder in sorted(root.glob("0*-lab-*")):
                self.assertEqual(verify_audit(folder / "report.audit.jsonl")["status"], "verified")
                with (folder / "report.csv").open(encoding="utf-8-sig") as stream:
                    rows = list(csv.DictReader(stream))
                summary = json.loads((folder / "report.summary.json").read_text())
                path = Path(name) / (folder.name + ".html")
                write_html(path, rows, summary, "report.csv", "report.audit.jsonl")
                html = path.read_text()
                metric = summary["metrics"]["approved_mapping_ready"]
                self.assertIn(f"Approved mapping ready: {metric['numerator']} / {metric['denominator']}", html)
                for row in rows:
                    self.assertIn(row["tuple_id"], html)
                    if row.get("suggested_code") == "26471-3" and row.get("code") == "6690-2":
                        self.assertIn("existing_code_property", blocked_selection(row))
                        self.assertIn("Conflicting recorded AI proposal blocked", html)
                        fields = laboratory_fields(row, summary)
                        for field in fields:
                            self.assertNotIn("26471-3", field.recommendation)
                            self.assertNotIn("100000000", field.recommendation)
                    decisions = set_decisions(row, summary)
                    fields = {f.name: f for f in laboratory_fields(row, summary)}
                    if row.get("code") == "LIS-HB" and row.get("suggested_code") == "718-7":
                        self.assertEqual(fields["Code"].proposal_state, "yellow")
                        self.assertEqual(fields["Method"].proposal_state, "yellow")
                        self.assertIn("no method-specific requirement", fields["Method"].recommendation)
                    study_records = json.loads(row.get("study_evidence_json") or "[]")
                    if any(r.get("status") == "extraction_unavailable" for r in study_records):
                        for name in ("Value / statistics", "Reference ranges"):
                            self.assertTrue(any("rejected/unavailable" in f for f in fields[name].findings))
                    if row.get("unit_code") == "g/L":
                        self.assertEqual(decisions["Unit code / display"][0], "Convert")
                        for key in ("Unit code / display", "Value / statistics", "Reference ranges"):
                            self.assertEqual(fields[key].proposal_state, "yellow")
                        self.assertIn("0.1", decisions["Unit code / display"][1])
                        self.assertIn("14", decisions["Value / statistics"][1])
                        self.assertIn("12.0", decisions["Reference ranges"][1])
                    if row.get("unit_code") == "10*6":
                        self.assertEqual(decisions["Unit code / display"][0], "Review")
                        self.assertIn("no verified factor", decisions["Unit code / display"][1])
                        self.assertNotEqual(fields["Unit code / display"].proposal_state, "green")
                    if row.get("value_numeric_ready") == "False":
                        self.assertEqual(decisions["Value / statistics"][0], "Correct source")
                        self.assertEqual(fields["Value / statistics"].source_state, "red")
                        self.assertEqual(fields["Value / statistics"].proposal_state, "red")
                parser = TableParser()
                parser.feed(html)
                self.assertEqual(parser.item_ids, [row["tuple_id"] for row in rows])
                self.assertEqual(parser.open_set_details, 0)
                self.assertIn('<th scope="col">→ Proposal</th>', html)
                self.assertEqual(parser.item_tables, len(rows))
                study_counts = summary.get("research", {}).get("evidence_counts", {})
                if summary.get("research", {}).get("scope") == "studies" and study_counts.get("extracted_measurements") == 0 and study_counts.get("extracted_intervals") == 0:
                    self.assertIn("No study-derived value or reference-range recommendation.", html)
                    self.assertLess(html.index("No study-derived value"), html.index('class="item"'))
                if folder.name in {"01-lab-lookup", "03-lab-ai-recommendations", "04-lab-ai-evidence"}:
                    self.assertIn("g/dL; value × 0.1", html)
                    self.assertIn("ucum.nlm.nih.gov", html)
                self.assertEqual(verify_audit(folder / "report.audit.jsonl")["status"], "verified")


if __name__ == "__main__":
    unittest.main()
