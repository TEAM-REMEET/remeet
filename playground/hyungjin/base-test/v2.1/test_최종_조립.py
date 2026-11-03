import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'v1'))
import 최종_조립 as final


class FinalAssemblyTests(unittest.TestCase):
    def test_action_candidates_and_review_findings_are_displayed_separately(self):
        result = {'actions': [{'content': '회의록을 올린다', 'assignee': None, 'due_date': None,
                               'evidence_utterance_ids': [42]}],
                  'review_needed': [{'description': '담당자 합의 확인', 'evidence_utterance_ids': [43]}]}
        review = {'issues': [{'type': '과잉 추출', 'description': '후속성 불명확'}], 'review_needed': []}
        text = final.render_actions(result, review, [])
        self.assertIn('회의록을 올린다 | 담당: - | 기한: - | 관련 안건: 미연결', text)
        self.assertIn('### 검토 필요 업무\n\n- 담당자 합의 확인', text)
        self.assertIn('[과잉 추출] 후속성 불명확', text)
        self.assertIn('사람이 확인한 최종 업무 목록이 아닙니다', text)
        self.assertNotIn('42', text)

    def test_action_agenda_link_and_invalid_identifier(self):
        actions = {'actions': [
            {'content': '첫 업무', 'agenda_id': 'A003', 'assignee': None, 'due_date': None},
            {'content': '둘째 업무', 'agenda_id': 'A999', 'assignee': None, 'due_date': None}],
            'review_needed': []}
        agenda_details = [{'agenda_id': 'A001'}, {'agenda_id': 'A003'}]
        text = final.render_actions(actions, {'issues': [], 'review_needed': []}, agenda_details)
        self.assertIn('관련 안건: [안건 2](#agenda-a003)', text)
        self.assertIn('관련 안건: 연결 확인 필요', text)

    def test_final_assembly_preserves_action_source_and_inserts_section(self):
        action = {'actions': [{'content': '보고서 공유', 'assignee': '김', 'due_date': '다음 회의', 'agenda_id': 'A001',
                               'evidence_utterance_ids': [8]}], 'review_needed': []}
        source = {'meeting_id': 'M', 'action_result': action, 'action_review': {'issues': [], 'review_needed': []},
                  'summary_reviews': [], 'unassigned_chunks': [], 'scope_limitations': [],
                  'source_paths': {}, 'valid_utterances': [8]}
        assembled = {'meeting_id': 'M', 'agenda_details': [{'agenda_id': 'A001'}], 'review_notes': [], 'scope_limitations': []}
        exp = Mock(output=Path('example/outputs/run'))
        exp.call.return_value = assembled
        saved = {}
        with patch.object(final, 'load_inputs', return_value=source), \
             patch.object(final, 'validate_result') as validate, \
             patch.object(final, 'render', return_value='# 회의록\n\n## 안건별 상세\n'), \
             patch.object(final, 'save', side_effect=lambda path, data: saved.update({path.name: data})), \
             patch.object(Path, 'write_text') as write:
            final.assemble(exp, 'M')
        validate.assert_called_once_with(assembled, source)
        self.assertEqual(saved['13_최종조립_입력.json']['action_result'], action)
        text = write.call_args.args[0]
        self.assertLess(text.index('## 후속 업무'), text.index('## 안건별 상세'))
        self.assertIn('보고서 공유 | 담당: 김 | 기한: 다음 회의 | 관련 안건: [안건 1](#agenda-a001)', text)
        self.assertIn('전체 안건의 관련 쟁점을 대조', exp.call.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
