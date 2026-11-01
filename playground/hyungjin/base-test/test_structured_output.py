import io
import hashlib
import json
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from experiment import Experiment, SCHEMAS


class StructuredOutputTest(unittest.TestCase):
    def setUp(self):
        self.schemas = json.loads(SCHEMAS.read_text(encoding='utf-8'))
        self.exp = Experiment.__new__(Experiment)
        self.exp.schemas = self.schemas
        self.exp.calls = []
        self.exp.record = lambda status, error=None: setattr(self.exp, 'status', status)

    def test_all_schemas_use_strict_object_rules(self):
        def check(node):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertFalse(node.get('additionalProperties'))
                    self.assertEqual(set(node['required']), set(node['properties']))
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        for schema in self.schemas.values():
            Draft202012Validator.check_schema(schema)
            check(schema)

    def test_request_uses_selected_schema(self):
        data = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"actions":[]}'}}], 'model': 'gpt-6-luna', 'usage': {}}
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}), patch('experiment.urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as request:
            result = self.exp.call('stage', 'system', 'user', 'legacy_action')
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['response_format']['type'], 'json_schema')
        config = body['response_format']['json_schema']
        self.assertTrue(config['strict'])
        self.assertEqual(config['name'], 'legacy_action')
        self.assertEqual(config['schema'], self.schemas['legacy_action'])
        self.assertEqual(self.exp.calls[0]['schema'], 'legacy_action')
        self.assertEqual(self.exp.calls[0]['system_prompt_sha256'], hashlib.sha256(b'system').hexdigest())
        self.assertEqual(result, {'actions': []})

    def test_refusal_and_incomplete_are_recorded_as_failure(self):
        for finish, message in [('stop', {'refusal': '거절'}), ('length', {'content': '{}'}), ('stop', {'content': None})]:
            with self.subTest(finish=finish, message=message), patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}):
                response = {'choices': [{'finish_reason': finish, 'message': message}]}
                with patch('experiment.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
                    with self.assertRaises(ValueError):
                        self.exp.call('stage', 'system', 'user', 'action_result')
                self.assertEqual(self.exp.status, 'failed')


if __name__ == '__main__':
    unittest.main()
