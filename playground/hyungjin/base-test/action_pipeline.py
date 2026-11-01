"""Independent action extraction and meeting-wide consolidation/review."""
import json
from pathlib import Path

from experiment import HERE, save


PROMPT = HERE / '액션_프롬프트.md'
POLICIES = {
    'three_with_review': '①②③을 모두 충족하면 actions, 두 개만 충족하고 단순 의견·제안이 아닌 경우 review_needed로 분리한다.',
}


def _text(value):
    return json.dumps(value, ensure_ascii=False)


def _evidence(items, valid_ids):
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('액션 항목이 객체가 아닙니다.')
        ids = item.get('evidence_utterance_ids')
        if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in valid_ids for i in ids):
            raise ValueError('근거 발화가 없거나 입력 범위에 없습니다.')


def _validate(result, meeting_id, valid_ids, action_ids=None):
    if result.get('meeting_id') != meeting_id:
        raise ValueError('액션 결과의 회의 ID가 일치하지 않습니다.')
    for key in ('actions', 'review_needed', 'change_log'):
        if not isinstance(result.get(key), list):
            raise ValueError(f'{key}는 배열이어야 합니다.')
        _evidence(result[key], valid_ids)
    seen = set()
    for item in result['actions']:
        aid = item.get('action_id')
        if not isinstance(aid, str) or not aid or aid in seen or not isinstance(item.get('content'), str) or not item['content'].strip():
            raise ValueError('액션 ID 또는 내용이 올바르지 않습니다.')
        if item.get('type') not in ('업무 수행', '조사·검토', '수정·개선', '확인·보고', '의사결정 준비'):
            raise ValueError('액션 유형이 올바르지 않습니다.')
        criteria = item.get('criteria')
        if not isinstance(criteria, dict) or any(criteria.get(k) is not True for k in ('actionable', 'future', 'intent')):
            raise ValueError('액션은 세 판단 기준을 모두 충족해야 합니다.')
        for key in ('assignee', 'due_date', 'agenda_id'):
            if key not in item or (item[key] is not None and not isinstance(item[key], str)):
                raise ValueError(f'{key}는 문자열 또는 null이어야 합니다.')
        seen.add(aid)
    if action_ids is not None and any(a['agenda_id'] is not None and a['agenda_id'] not in action_ids for a in result['actions']):
        raise ValueError('존재하지 않는 안건 ID입니다.')


def extract_actions(exp, chunk, agendas, policy):
    if policy not in POLICIES:
        raise ValueError('액션 판정 정책을 선택해야 합니다.')
    rules = exp.action_prompt_text
    source = {'회의ID': exp.data['meeting_id'], '안건목록': agendas, '청크': chunk}
    system = rules + '\n정책: ' + POLICIES[policy] + '\n현재 청크만 판단하고 결과 스키마의 JSON 객체 하나만 반환한다.'
    result = exp.call(f"chunk_{chunk['청크ID']}_actions_v2", system, _text(source), 'action_result')
    valid = {u['번호'] for u in chunk['발화']}
    _validate(result, exp.data['meeting_id'], valid, {a['안건ID'] for a in agendas})
    return result


def run_action_review(exp, policy):
    """Consolidate all candidates against all chunks, then review and repair once."""
    if policy not in POLICIES:
        raise ValueError('액션 판정 정책을 선택해야 합니다.')
    if [a.get('chunk_id') for a in exp.actions] != [c['청크ID'] for c in exp.chunks]:
        raise ValueError('모든 청크의 액션 후보가 필요합니다.')
    rules = exp.action_prompt_text
    valid = {u['번호'] for c in exp.chunks for u in c['발화']}
    agenda_ids = {a['안건ID'] for a in exp.agendas}
    source = {'meeting_id': exp.data['meeting_id'], 'agendas': exp.agendas, 'candidates': exp.actions, 'chunks': exp.chunks}
    policy_text = '\n정책: ' + POLICIES[policy]
    def check_review(review):
        if review.get('meeting_id') != exp.data['meeting_id']:
            raise ValueError('검증 회의 ID 불일치')
        for key in ('issues', 'review_needed', 'scope_limitations'):
            if not isinstance(review.get(key), list):
                raise ValueError('액션 검증 결과 형식 오류')
        _evidence(review['issues'], valid)

    merged = exp.call('actions_merge', rules + policy_text + '\n전체 원문과 후보를 함께 읽어 병합하고 결과 스키마 JSON만 반환한다.', _text(source), 'action_result')
    _validate(merged, exp.data['meeting_id'], valid, agenda_ids)
    save(exp.output / '09_액션병합.json', merged)

    def review(result, stage):
        user = {'meeting_id': exp.data['meeting_id'], 'result': result, 'chunks': exp.chunks}
        output = exp.call(stage, rules + policy_text + '\n원문 전체와 결과를 비교하고 검증 결과 스키마 JSON만 반환한다.', _text(user), 'action_review')
        check_review(output)
        return output

    initial_review = review(merged, 'actions_verify')
    save(exp.output / '10_액션검증.json', initial_review)
    final = merged
    if initial_review['issues']:
        user = {'meeting_id': exp.data['meeting_id'], 'result': merged, 'issues': initial_review['issues'], 'chunks': exp.chunks, 'agendas': exp.agendas}
        final = exp.call('actions_repair', rules + policy_text + '\n검증 지적을 원문에 대조해 수정하고 결과 스키마 JSON만 반환한다.', _text(user), 'action_result')
        _validate(final, exp.data['meeting_id'], valid, agenda_ids)
        save(exp.output / '11_액션수정.json', final)
        final_review = review(final, 'actions_reverify')
        save(exp.output / '12_액션재검증.json', final_review)
    else:
        save(exp.output / '11_액션수정.json', final)
    exp.record('completed_review_required')
    return final
