import copy
import unittest

from final_assembly import render, validate_result


class FinalAssemblyTest(unittest.TestCase):
    def setUp(self):
        self.source = {'meeting_id': 'TS3003a', 'metadata': {}, 'agenda_index': [{'안건ID': 'A001'}], 'valid_utterances': [3]}
        self.result = {'meeting_id': 'TS3003a', 'meeting_results': [{'tag': '결정', 'text': '결정', 'agenda_ids': ['A001'], 'evidence_utterance_ids': [3]}], 'agenda_details': [{'agenda_id': 'A001', 'title': '안건', 'background': [], 'discussions': [], 'decisions': [{'tag': '조건부 결정', 'text': '조건', 'evidence_utterance_ids': [3]}], 'unresolved_issues': [], 'next_meeting_topics': []}], 'review_notes': [], 'scope_limitations': []}

    def test_render_counts_and_hides_evidence(self):
        validate_result(self.result, self.source)
        output = render(self.result, self.source)
        self.assertIn('[결정 1 · 미해결 0]', output)
        self.assertIn('[조건부 결정] 조건', output)
        self.assertNotIn('evidence_utterance_ids', output)
        self.assertNotIn('[S3]', output)

    def test_reject_missing_agenda_or_invalid_evidence(self):
        bad = copy.deepcopy(self.result)
        bad['agenda_details'] = []
        with self.assertRaises(ValueError):
            validate_result(bad, self.source)
        bad = copy.deepcopy(self.result)
        bad['meeting_results'][0]['evidence_utterance_ids'] = [99]
        with self.assertRaises(ValueError):
            validate_result(bad, self.source)


if __name__ == '__main__':
    unittest.main()
