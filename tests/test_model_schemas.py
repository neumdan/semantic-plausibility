"""Pure schema/security guards; no model or terminology reply is simulated."""
import unittest
from semantic_plausibility.model_schemas import explicit_token, response_schema, validate_response, validate_grounding, evidence_schema, model_evidence
from semantic_plausibility import research_prompts
from semantic_plausibility.fhir_quality_pipeline import number

class SchemaTests(unittest.TestCase):
    def test_bounded_token_matching(self):
        self.assertTrue(explicit_token('g/dL', 'g/dL.'))
        self.assertFalse(explicit_token('g/dL', 'mg/dL'))
        self.assertFalse(explicit_token('14', '140'))

    def test_nonfinite_numbers_are_not_measurements(self):
        for value in ['NaN', 'Infinity', '-Infinity', 'not-a-number']:
            self.assertIsNone(number(value))
        self.assertEqual(str(number('0')), '0')

    def test_unknown_extraction_task_is_rejected(self):
        with self.assertRaises(ValueError):
            response_schema('unknown task')

    def test_json_types_and_required_fields_are_enforced(self):
        schema = response_schema(research_prompts.LOINC_UNITS)
        for invalid in [None, [], {}, {'units': 'g/dL'}, {'units': [], 'additional': True}]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_response(invalid, schema)
        validate_response({'units': []}, schema)

    def test_abstention_and_untrusted_search_phrases(self):
        validate_grounding(research_prompts.LOINC_REVIEW,
            {'selected_code': None, 'selected_name': None, 'reason': ''}, {'candidates': []})
        for query in ['https://example.org', '718-7', '<instructions>', 'name\ncode']:
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_grounding(research_prompts.NAME_QUERIES, {'queries': [query]}, {})

    def test_study_schema_uses_source_ids_without_repeating_source_text(self):
        import json
        evidence = {"abstract": "A source sentence.", "excerpt_candidates": ["A source sentence."]}
        schema = evidence_schema(research_prompts.STUDY_EXTRACT, evidence)
        self.assertEqual(schema["properties"]["bounds"]["properties"]["excerpt"]["enum"], ["", "s1"])
        self.assertNotIn("A source sentence.", json.dumps(schema))
        payload = model_evidence(research_prompts.STUDY_EXTRACT, evidence)
        self.assertEqual(payload, {"sentences": {"s1": "A source sentence."}})
        self.assertEqual(schema["properties"]["measurements"]["maxItems"], 4)
        self.assertEqual(schema["properties"]["measurements"]["items"]["properties"]["excerpt"]["enum"], ["s1"])
        empty = evidence_schema(research_prompts.STUDY_EXTRACT, {"excerpt_candidates": []})
        self.assertEqual(empty["properties"]["measurements"]["maxItems"], 0)

    def test_no_verified_candidate_only_allows_abstention_in_schema(self):
        schema = evidence_schema(research_prompts.LOINC_REVIEW, {"candidates": []})
        self.assertEqual(schema["properties"]["selected_code"]["enum"], [None])
        self.assertEqual(schema["properties"]["selected_name"]["enum"], [None])

    def test_reference_abbreviations_require_a_definition_in_the_actual_source(self):
        from semantic_plausibility.model_schemas import source_defined_reference_abbreviation, supports_evidence_type
        self.assertTrue(source_defined_reference_abbreviation("The RIs are reported.", "reference intervals (RIs)"))
        self.assertFalse(source_defined_reference_abbreviation("The RIs are reported.", "No abbreviation definition."))
        self.assertFalse(source_defined_reference_abbreviation("A risk is reported.", "reference intervals (RI)"))
        self.assertTrue(supports_evidence_type("reference_interval", "The RIs are reported.", "reference intervals (RIs)"))
        self.assertFalse(supports_evidence_type("reference_interval", "The RIs are reported.", "Undefined abbreviation."))

    def test_study_context_does_not_treat_cell_names_as_specimen_evidence(self):
        evidence = {"excerpt_candidates": ["Red blood cells were reported.",
            "Blood samples were collected from healthy adults."]}
        schema = evidence_schema(research_prompts.STUDY_EXTRACT, evidence)
        fields = schema["properties"]["bounds"]["properties"]
        self.assertEqual(fields["specimen"]["enum"], ["", "s2"])
        self.assertEqual(fields["population"]["enum"], ["", "s2"])
        self.assertEqual(fields["method"]["enum"], [""])
