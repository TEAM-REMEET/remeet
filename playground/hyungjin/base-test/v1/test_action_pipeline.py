import json
import unittest
from pathlib import Path
from unittest.mock import patch

from action_pipeline import PROMPT, _validate, extract_actions, run_action_review


# 테스트에서 공통으로 사용할 정상 액션 예시다.
# 세 판단 기준을 모두 충족하고, 근거는 테스트 원문의 0번 발화다.
def action():
    return {'action_id': 'ACT001', 'content': '비용을 조사한다', 'type': '조사·검토',
            'assignee': None, 'due_date': None, 'agenda_id': None,
            'criteria': {'actionable': True, 'future': True, 'intent': True},
            'evidence_utterance_ids': [0]}


# 실제 회의 실험을 대신하는 가짜 객체다. 모델 API를 호출하지 않는다.
# 각 호출에서 미리 준비한 responses를 하나씩 반환하고, 호출 순서만 기록한다.
class FakeExperiment:
    def __init__(self, output, responses):
        self.output = Path(output)
        self.data = {'meeting_id': 'TS3003c'}
        self.chunks = [{'청크ID': 1, '발화': [{'번호': 0, '텍스트': '비용을 조사해 주세요.'}]}]
        self.agendas = []
        self.actions = [{'chunk_id': 1, 'actions': [action()]}]
        self.action_prompt_text = PROMPT.read_text(encoding='utf-8')
        self.responses = iter(responses)
        self.stages = []
        self.schemas = []
        self.status = None

    def call(self, stage, system, user, schema_name):
        # 실행한 단계와 사용한 출력 스키마를 나중에 확인할 수 있도록 기록한다.
        self.stages.append(stage)
        self.schemas.append(schema_name)
        return next(self.responses)

    def record(self, status, error=None):
        self.status = status


class ActionPipelineTest(unittest.TestCase):
    # 각 테스트가 시작되기 전에 공통으로 사용할 가짜 모델 응답을 준비한다.
    def setUp(self):
        # result: 액션 추출·병합 결과, issue: 검증 중 오류 발견, ok: 검증 지적 없음
        self.result = {'meeting_id': 'TS3003c', 'actions': [action()], 'review_needed': [], 'change_log': []}
        self.issue = {'meeting_id': 'TS3003c', 'issues': [{'type': '누락', 'description': '확인', 'evidence_utterance_ids': [0], 'suggested_fix': '수정'}], 'review_needed': [], 'scope_limitations': []}
        self.ok = {'meeting_id': 'TS3003c', 'issues': [], 'review_needed': [], 'scope_limitations': []}

    def test_invalid_evidence_and_criteria_are_rejected(self):
        # 원문에 없는 9번 발화를 근거로 제시하면 오류가 발생해야 한다.
        # JSON 변환은 원본 result를 바꾸지 않도록 별도 복사본을 만드는 데 사용한다.
        bad = json.loads(json.dumps(self.result))
        bad['actions'][0]['evidence_utterance_ids'] = [9]
        with self.assertRaises(ValueError):
            _validate(bad, 'TS3003c', {0}, set())
        # 근거를 복구해도 실행 의도가 없다면 확정 액션으로 허용되지 않아야 한다.
        bad['actions'][0]['evidence_utterance_ids'] = [0]
        bad['actions'][0]['criteria']['intent'] = False
        with self.assertRaises(ValueError):
            _validate(bad, 'TS3003c', {0}, set())

    def test_chunk_extract_uses_selected_policy_and_input_evidence(self):
        # 가짜 모델 응답이 청크의 액션 결과로 반환되고, 올바른 단계가 호출되는지 확인한다.
        exp = FakeExperiment('.', [self.result])
        self.assertEqual(extract_actions(exp, exp.chunks[0], [], 'three_with_review'), self.result)
        self.assertEqual(exp.stages, ['chunk_1_actions_v2'])

    def test_merge_review_repair_and_recheck_are_saved(self):
        # 모델이 병합 결과 → 오류 발견 → 수정 결과 → 재검증 통과 순서로 응답한다고 가정한다.
        exp = FakeExperiment('.', [self.result, self.issue, self.result, self.ok])
        # patch는 파일을 실제로 쓰지 않으면서 어떤 저장 요청이 있었는지 확인하게 해 준다.
        with patch('action_pipeline.save') as saved, patch.object(Path, 'write_text'):
            run_action_review(exp, 'three_with_review')
            # 각 단계와 스키마가 정해진 순서로 실행됐는지 확인한다.
            self.assertEqual(exp.stages, ['actions_merge', 'actions_verify', 'actions_repair', 'actions_reverify'])
            self.assertEqual(exp.schemas, ['action_result', 'action_review', 'action_result', 'action_review'])
            self.assertEqual(exp.status, 'completed_review_required')
            # 병합·검증·수정·재검증 결과가 각각 별도 파일로 저장 요청됐는지 확인한다.
            self.assertEqual([x.args[0].name for x in saved.call_args_list], ['09_액션병합.json', '10_액션검증.json', '11_액션수정.json', '12_액션재검증.json'])

    def test_missing_chunk_prevents_review(self):
        # 필요한 청크의 액션 후보가 빠졌다면 검증을 시작하지 않고 오류를 내야 한다.
        exp = FakeExperiment('.', [])
        exp.actions = []
        with self.assertRaises(ValueError):
            run_action_review(exp, 'three_with_review')
        self.assertEqual(exp.stages, [])


if __name__ == '__main__':
    # 이 파일을 직접 실행하면 test_로 시작하는 테스트들을 실행한다.
    unittest.main()
