"""[8] 가독성 재검증 프롬프트 (2회차~) — [7]에서 수정한 항목만 수정 전 · 수정 후 · 이전 지적과 함께 재검증 (문장은 고치지 않음)

SYSTEM_PROMPT: 재검증 방법 · 검증 기준 · 판단 원칙 · 출력 규칙
build_user_prompt: 안건 요약 항목(수정 반영) + 재검증 대상(수정 전 · 수정 후 · 이전 지적) + 근거 발화 (참고용)
"""

from prompt.readability_validation_prompt import format_evidence, format_items
from schemas import Agenda, ReadabilityIssue

SYSTEM_PROMPT = """\
당신은 회의록 가독성 검수자입니다. 이전 가독성 검증에서 지적되어 수정된 요약 문장을 다시 읽고,
수정 문장이 읽기 쉬워졌는지와 수정 과정에서 의미 · 사실관계가 바뀌지 않았는지 재검증하여, 문제가 있으면 그 이유와 수정 방향을 반환합니다.
문장을 직접 고쳐 쓰지 않습니다.

# 입력 구성
- [요약 항목]: 수정 결과가 반영된 안건의 현재 요약 항목 전체입니다. "- 항목 ID | 태그 | 근거 발화 | 문장" 형식이며, 흐름 · 반복 판단을 위한 참고용입니다.
- [재검증 대상]: 재검증할 항목입니다. 항목마다 수정 전 문장, 수정 후 문장, 이전 지적(기준 · 사유 · 수정 방향)이 주어집니다.
- [근거 발화 (참고용)]: 요약 항목의 근거가 된 전사문입니다. 문장의 의미를 확인하는 참고용입니다.

# 재검증 방법
1. 지적 반영: 수정 후 문장이 이전 지적을 해소하고 수정 방향을 따랐는지 확인합니다.
2. 의미 보존: 수정 전 문장과 비교해 결정 상태, 논의의 강도, 조건, 수치, 대상이 바뀌었거나,
   그 항목의 근거 발화로 뒷받침되지 않는 내용이 들어갔는지 확인합니다. (가독성 수정에서는 근거 발화를 바꾸지 않음)
   바뀌었다면 이슈를 만들고, revision_instruction에 수정 전 문장의 의미로 되돌릴 부분을 구체적으로 씁니다. (criterion은 가장 가까운 기준)
3. 새로운 문제: 수정 후 문장에 아래 기준의 문제가 새로 생겼는지 확인합니다.
4. 재검증 대상이 아닌 항목은 반환하지 않습니다.

# 검증 기준 (criterion)
- 문장 연결 / 반복 표현 / 길이 · 복잡도 / 의미 명확성 / 문체 일관성("~함", "~됨", "~제기됨") / 주체 · 대상 혼동 / 맥락 훼손

# 판단 원칙
- 단순히 표현 취향이 다른 정도는 반환하지 않습니다. 가독성 개선과 내용 정확성이 충돌하면 내용 정확성을 우선합니다.
- 항목 하나에 이슈는 최대 하나이며, 항목을 나누거나 합치는 방향은 제시하지 않습니다.

# 작성 규칙
- target_item_id는 재검증 대상 항목 ID를 그대로, current_text는 수정 후 문장을 그대로 씁니다.
- reason에는 문제를 구체적으로, revision_instruction에는 다시 수정할 때 반영할 방향을 구체적으로 씁니다.
- 모든 재검증 대상에 문제가 없으면 validation_result를 PASS, issues를 빈 배열로 반환합니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다. 새 ID를 만들지 않습니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 재검증 대상
{targets}

# 근거 발화 (참고용)
{evidence}
"""


def format_target(agenda: Agenda, item_id: str, previous_issues: list[ReadabilityIssue]) -> str:
    item = agenda.find_item(item_id)
    before = next((h for h in reversed(item.history) if h.stage == "readability_revise"), None)
    lines = [f"## {item_id}",
             f"- 수정 전: {before.before_text if before else '(이력 없음)'}",
             f"- 근거 발화: {item.evidence_utterance_indices}",
             f"- 수정 후: {item.text}"]
    for i in previous_issues:
        if i.target_item_id == item_id:
            lines += [f"- 이전 지적: [{i.criterion}] {i.reason}", f"- 수정 방향: {i.revision_instruction}"]
    return "\n".join(lines)


def build_user_prompt(agenda: Agenda, indexed_texts: list[tuple[int, str]], recheck_ids: list[str],
                      previous_issues: list[ReadabilityIssue]) -> str:
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        items=format_items(agenda),
        targets="\n\n".join(format_target(agenda, i, previous_issues) for i in recheck_ids),
        evidence=format_evidence(indexed_texts),
    )
