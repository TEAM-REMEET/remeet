"""v2.1 최종 조립과 기존 액션 결과의 표시."""
import json
from pathlib import Path

from experiment import save
from final_assembly import load_inputs, render, validate_result


def render_actions(result, review, agenda_details):
    agenda_numbers = {agenda['agenda_id']: i for i, agenda in enumerate(agenda_details, 1)}
    lines = ['## 후속 업무', '', '> 자동 추출된 업무 후보와 안건 연결입니다. 아래 검증 지적과 함께 확인하세요.', '']
    for action in result['actions']:
        assignee = action.get('assignee') or '-'
        due = action.get('due_date') or '-'
        agenda_id = action.get('agenda_id')
        if agenda_id in agenda_numbers:
            agenda = f"[안건 {agenda_numbers[agenda_id]}](#agenda-{agenda_id.lower()})"
        else:
            agenda = '미연결' if agenda_id is None else '연결 확인 필요'
        lines.append(f"- {action['content']} | 담당: {assignee} | 기한: {due} | 관련 안건: {agenda}")
    if not result['actions']:
        lines.append('- 해당 없음')
    lines += ['', '### 검토 필요 업무', '']
    lines.extend(f"- {item['description']}" for item in result['review_needed'])
    if not result['review_needed']:
        lines.append('- 해당 없음')
    lines += ['', '### 액션 검증에서 남은 지적', '']
    lines.extend(f"- [{item['type']}] {item['description']}" for item in review['issues'])
    lines.extend(f'- {item}' for item in review['review_needed'])
    if not review['issues'] and not review['review_needed']:
        lines.append('- 없음')
    lines.extend(['', '> 이 목록은 모델의 판단이며, 사람이 확인한 최종 업무 목록이 아닙니다.', ''])
    return '\n'.join(lines)


def assemble(exp, meeting):
    source = load_inputs(meeting, exp.output, exp.output)
    save(exp.output / '13_최종조립_입력.json', source)
    prompt = Path(__file__).with_name('최종_요약_프롬프트.md').read_text(encoding='utf-8')
    payload = {k: v for k, v in source.items() if k not in ('source_paths', 'valid_utterances')}
    result = exp.call('final_assembly', prompt, json.dumps(payload, ensure_ascii=False), 'final_assembly')
    validate_result(result, source)
    save(exp.output / '14_최종회의록.json', result)
    minutes = render(result, source)
    marker = '## 안건별 상세'
    if marker not in minutes:
        raise ValueError('회의록의 안건별 상세 위치를 찾을 수 없습니다.')
    action_section = render_actions(source['action_result'], source['action_review'], result['agenda_details'])
    minutes = minutes.replace(marker, action_section + '\n' + marker, 1)
    (exp.output / '최종_회의록.md').write_text(minutes, encoding='utf-8')
    save(exp.output / '15_사람검토자료.json', {
        'summary_reviews': source['summary_reviews'], 'action_review': source['action_review'],
        'unassigned_chunks': source['unassigned_chunks'], 'generated_review_notes': result['review_notes'],
        'scope_limitations': source['scope_limitations'] + result['scope_limitations']})
    exp.record('final_generated_not_verified')
