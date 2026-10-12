"""Pure schema/security guards; no model or terminology reply is simulated."""
import unittest
from semantic_plausibility.model_schemas import explicit_token, response_schema, validate_response, validate_grounding
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
