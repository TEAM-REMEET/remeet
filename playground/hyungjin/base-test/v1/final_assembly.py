"""Build a final meeting record from existing first-pass results."""
import html
import json
from pathlib import Path

from experiment import Experiment, HERE, REPO, save
PROMPT = HERE / '최종_요약_프롬프트.md'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def load_inputs(meeting_id, summary, action):
    summary, action = Path(summary), Path(action)
    source = read(summary / '00_입력.json')
    action_input = action / '00_입력.json'
    if not action_input.exists() and action.name == '03_액션':
        action_input = action.parent / '00_입력.json'
    action_source = read(action_input)
    if source['meeting_id'] != meeting_id or action_source['meeting_id'] != meeting_id or action_source['chunks'] != source['chunks']:
        raise ValueError('요약과 액션의 회의 입력이 일치하지 않습니다.')
    agendas = read(summary / '02_안건목록.json')
    merged = [read(summary / '06_수정후' / f"{a['안건ID']}.json") for a in agendas]
    if any(x.get('meeting_id') != meeting_id or x.get('agenda_id') != a['안건ID'] for a, x in zip(agendas, merged)):
        raise ValueError('요약 결과의 회의 또는 안건 ID가 일치하지 않습니다.')
    reviews = []
    for a in agendas:
        aid = a['안건ID']
        path = summary / '07_재검증' / f'{aid}.json'
        if not path.exists():
            path = summary / '05_검증' / f'{aid}.json'
        review = read(path)
        if review.get('meeting_id') != meeting_id or review.get('agenda_id') != aid:
            raise ValueError('요약 검증 결과의 회의 또는 안건 ID가 일치하지 않습니다.')
        reviews.append(review)
    empty_chunks = [c['청크ID'] for c in source['chunks'] if not read(summary / '01_청크요약' / f"{c['청크ID']:03d}.json")['agendas']]
    action_review_path = action / '12_액션재검증.json'
    if not action_review_path.exists():
        action_review_path = action / '10_액션검증.json'
    action_result = read(action / '11_액션수정.json')
    action_review = read(action_review_path)
    if action_result.get('meeting_id') != meeting_id or action_review.get('meeting_id') != meeting_id:
        raise ValueError('액션 결과의 회의 ID가 일치하지 않습니다.')
    def source_path(path):
        try:
            return path.resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return f'외부 폴더/{path.name}'
    return {'meeting_id': meeting_id, 'metadata': source['metadata'], 'agenda_index': agendas,
            'merged_agendas': merged, 'summary_reviews': reviews,
            'action_result': action_result, 'action_review': action_review,
            'unassigned_chunks': empty_chunks,
            'scope_limitations': [f'안건 미식별 청크: {empty_chunks}. 회의 전체 누락 검증은 아직 수행하지 않음.',
                                  '액션은 전체 원문으로 검증했으며 안건별 요약을 별도로 입력해 재검증하지 않음.'],
            'source_paths': {'summary': source_path(summary), 'action': source_path(action)},
            'valid_utterances': [u['번호'] for c in source['chunks'] for u in c['발화']]}


def validate_result(result, source):
    if result.get('meeting_id') != source['meeting_id']:
        raise ValueError('회의 ID 불일치')
    expected = [a['안건ID'] for a in source['agenda_index']]
    actual = [a['agenda_id'] for a in result['agenda_details']]
    if actual != expected:
        raise ValueError('안건 ID 또는 순서가 입력과 다릅니다.')
    valid = set(source['valid_utterances'])
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'evidence_utterance_ids':
                    if not isinstance(item, list) or not item or any(type(i) is not int or i not in valid for i in item):
                        raise ValueError('근거 발화가 없거나 해당 회의 범위 밖입니다.')
                elif key == 'agenda_ids':
                    if not item or any(i not in expected for i in item):
                        raise ValueError('회의 결과의 안건 ID 오류')
                else:
                    check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(result)
    if any(x['agenda_id'] is not None and x['agenda_id'] not in expected for x in result['review_notes']):
        raise ValueError('검토 메모의 안건 ID 오류')


def render(result, source):
    meta = source['metadata']
    detail = result['agenda_details']
    number = {a['agenda_id']: i for i, a in enumerate(detail, 1)}
    source_document = meta.get('원본 문서') or f"playground/hyungjin/test/input/{source['meeting_id']}.txt"
    lines = [f"# {meta.get('회의 대주제') or source['meeting_id']}", '', '## 회의 정보', '',
             f"- 제목: {meta.get('회의 대주제') or '-'}", f"- 일시: {meta.get('일시') or '-'}",
             f"- 참석자: {meta.get('참여자') or '-'}",
             f"- 원본 문서: {source_document}", '', '## 안건 목차', '']
    for i, a in enumerate(detail, 1):
        lines.append(f"{i}. [{a['title']}](#agenda-{a['agenda_id'].lower()}) [결정 {len(a['decisions'])} · 미해결 {len(a['unresolved_issues'])}]")
    lines += ['', '## 회의 결과', '']
    for x in result['meeting_results']:
        refs = ' '.join(f'[안건{number[aid]}]' for aid in x['agenda_ids'])
        lines.append(f"- [{x['tag']}] {x['text']} {refs}")
    if not result['meeting_results']:
        lines.append('-')
    lines += ['', '## 안건별 상세', '']
    def section(title, items, tagged=False):
        lines.extend([f'**{title}**', ''])
        lines.extend([f"- {('[' + x['tag'] + '] ') if tagged else ''}{x['text']}" for x in items] or ['-'])
        lines.append('')
    for i, a in enumerate(detail, 1):
        title = html.escape(a['title'])
        lines += [f"<a id=\"agenda-{a['agenda_id'].lower()}\"></a>", f"<details>\n<summary>{i}. {title} [결정 {len(a['decisions'])} · 미해결 {len(a['unresolved_issues'])}]</summary>", '']
        section('논의 배경', a['background'])
        lines += ['**주요 논의**', '']
        for j, topic in enumerate(a['discussions'], 1):
            lines += [f"{i}-{j}. {topic['subtopic']}", '']
            lines.extend(f"- [{x['tag']}] {x['text']}" for x in topic['items'])
            lines.append('')
        if not a['discussions']:
            lines += ['-', '']
        section('결정사항', a['decisions'], True)
        section('미해결 이슈', a['unresolved_issues'])
        section('다음 회의 후보 안건', a['next_meeting_topics'])
        lines += ['</details>', '']
    return '\n'.join(lines)


def run(meeting_id, summary, action, exp=None):
    source = load_inputs(meeting_id, summary, action)
    exp = exp or Experiment(meeting_id)
    save(exp.output / '13_최종조립_입력.json', source)
    prompt = PROMPT.read_text(encoding='utf-8')
    payload = {k: v for k, v in source.items() if k not in ('source_paths', 'valid_utterances')}
    try:
        result = exp.call('final_assembly', prompt, json.dumps(payload, ensure_ascii=False), 'final_assembly')
        validate_result(result, source)
        save(exp.output / '14_최종회의록.json', result)
        (exp.output / '최종_회의록.md').write_text(render(result, source), encoding='utf-8')
        save(exp.output / '15_사람검토자료.json', {'summary_reviews': source['summary_reviews'], 'action_review': source['action_review'], 'unassigned_chunks': source['unassigned_chunks'], 'generated_review_notes': result['review_notes'], 'scope_limitations': source['scope_limitations'] + result['scope_limitations']})
        exp.record('final_generated_not_verified')
        print(f"완료: {meeting_id} / 안건 {len(result['agenda_details'])}개 / {exp.output}", flush=True)
        return exp.output
    except Exception as exc:
        exp.record('failed', f'final assembly: {type(exc).__name__}')
        raise
