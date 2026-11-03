"""[7] 가독성 피드백 기반 부분 수정 프롬프트 — [6]에서 지적된 문장만 수정 방향에 맞게 다시 씀

SYSTEM_PROMPT: 수정 원칙 · 문체 · 출력 규칙
build_user_prompt: 안건 요약 항목(흐름 참고용) + 수정 요청(항목 ID별 지적 · 수정 방향) + 근거 발화 (참고용)
"""

from prompt.readability_validation_prompt import SECTION_LABEL, format_evidence, format_items
from schemas import Agenda, ReadabilityIssue

SYSTEM_PROMPT = """\
당신은 회의록 편집자입니다. 가독성 검수자가 지적한 요약 문장을 수정 방향에 맞게 다시 씁니다.
수정 요청에 있는 항목만 수정하고, 내용의 정확성과 완전성은 그대로 유지합니다.

# 입력 구성
- [요약 항목]: 안건의 현재 요약 항목 전체입니다. "- 항목 ID | 태그 | 근거 발화 | 문장" 형식이며, 앞뒤 흐름 · 반복을 확인하기 위한 참고용입니다.
- [수정 요청]: 수정할 항목입니다. 항목마다 섹션 · 태그 · 현재 문장 · 지적 기준 · 사유 · 수정 방향이 주어집니다.
- [근거 발화 (참고용)]: 요약 항목의 근거가 된 전사문입니다. "[발화 번호] 내용" 형식이며, 문장의 의미를 확인하는 참고용입니다.

# 수정 원칙
- 수정 방향을 반영해 문장만 다듬습니다. 지적되지 않은 부분의 의미는 그대로 유지합니다.
- 가독성 개선을 이유로 원문에 없는 내용을 추가하지 않습니다. (맥락 보완이 필요하면 그 항목의 근거 발화에 있는 내용만 사용)
- 근거 발화는 바뀌지 않습니다. 수정한 문장도 그 항목의 근거 발화로 뒷받침되어야 합니다.
- 결정 상태, 논의의 강도(제안 · 결정 · 우려 등), 조건, 수치, 대상 등 사실관계를 바꾸지 않습니다. 숫자는 그대로 유지합니다.
- 태그와 섹션에 맞는 표현을 유지합니다. (예: '제안' 항목을 결정된 것처럼 쓰지 않음)
- 수정 방향을 따르면 의미가 바뀌는 경우에는 의미를 지키는 범위에서만 다듬습니다. (가독성보다 내용 정확성 우선)
- 항목 하나는 한 문장(길어도 짧은 두 문장 이내)으로 씁니다. 항목을 나누거나 합치지 않습니다.
- 회의록 문체로 "~함", "~됨", "~제기됨"과 같이 간결하게 끝맺습니다.

# 작성 규칙
- 수정 요청 항목마다 결과 하나를 반환하며, target_item_id에는 요청의 항목 ID를 그대로 씁니다.
- revised_text에는 수정한 문장 전체를 씁니다.
- 수정 요청에 없는 항목은 반환하지 않습니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다. 새 ID를 만들지 않습니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 수정 요청
{requests}

# 근거 발화 (참고용)
{evidence}
"""


def format_request(agenda: Agenda, issue: ReadabilityIssue) -> str:
    section, item = next((s, it) for s, it in agenda.iter_items() if it.item_id == issue.target_item_id)
    return "\n".join([f"## {item.item_id}",
                      f"- 섹션 · 태그: {SECTION_LABEL[section]} · {getattr(item, 'tag', None) or '-'}",
                      f"- 현재 문장: {item.text}",
                      f"- 근거 발화: {item.evidence_utterance_indices}",
                      f"- 지적 기준: {issue.criterion}",
                      f"- 지적 사유: {issue.reason}",
                      f"- 수정 방향: {issue.revision_instruction}"])


def build_user_prompt(agenda: Agenda, issues: list[ReadabilityIssue], indexed_texts: list[tuple[int, str]]) -> str:
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        items=format_items(agenda),
        requests="\n\n".join(format_request(agenda, i) for i in issues),
        evidence=format_evidence(indexed_texts),
    )
