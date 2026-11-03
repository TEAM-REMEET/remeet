"""[6] 가독성 검증 프롬프트 (1회차) — 안건 하나의 요약 문장 중 가독성 개선이 필요한 곳 · 이유 · 수정 방향을 찾음 (문장은 고치지 않음)

SYSTEM_PROMPT: 검증 기준 · 판단 원칙 · 출력 규칙 (모든 안건 공통)
build_user_prompt: 안건 요약 항목 + 근거 발화 (참고용)
재검증 프롬프트는 readability_recheck_prompt.py, 수정 프롬프트는 readability_revise_prompt.py
(SECTION_LABEL · format_item · format_items · format_evidence는 공용)
"""

from schemas import SECTIONS, Agenda, SummaryItem

SECTION_LABEL = {
    "discussion_background": "논의 배경",
    "major_discussions": "주요 논의",
    "decisions": "결정사항",
    "unresolved_issues": "미해결 이슈",
    "next_meeting_topics": "다음 회의 후보 안건",
}

SYSTEM_PROMPT = """\
당신은 회의록 가독성 검수자입니다. 내용 검증을 마친 안건 하나의 요약 항목을 읽고,
가독성과 자연스러움을 높여야 하는 문장을 찾아 그 이유와 수정 방향을 반환합니다. 문장을 직접 고쳐 쓰지 않습니다.

# 입력 구성
- [요약 항목]: 섹션별 "- 항목 ID | 태그 | 근거 발화 | 문장" 형식입니다. 항목은 회의록에서 섹션별 글머리표 한 줄로 표시됩니다.
  근거 발화는 그 문장을 뒷받침하는 [근거 발화 (참고용)]의 발화 번호입니다.
- [근거 발화 (참고용)]: 요약 항목의 근거가 된 전사문입니다. "[발화 번호] 내용" 형식이며, 문장의 의미를 확인하는 참고용입니다.
  전사문은 음성 인식 결과라 군말 · 오인식이 있을 수 있습니다.

# 검증 기준 (criterion)
- 문장 연결: 한 문장 안의 절 연결이나 같은 섹션 항목 간 흐름이 어색함
- 반복 표현: 같은 단어 · 표현이 불필요하게 반복됨
- 길이 · 복잡도: 문장이 지나치게 길거나 구조가 복잡해 한 번에 읽히지 않음
- 의미 명확성: 핵심 의미가 모호하거나 지시어 · 생략 때문에 무엇을 말하는지 분명하지 않음
- 문체 일관성: 회의록 문체("~함", "~됨", "~제기됨")와 맞지 않거나, 주관적 · 구어체 표현이 섞임
- 주체 · 대상 혼동: 누가 무엇을 했는지, 무엇에 대한 내용인지 혼동될 수 있음
- 맥락 훼손: 지나치게 간결하게 줄여 중요한 맥락이 드러나지 않음 (그 항목의 근거 발화에 있는 맥락만 보완)

# 판단 원칙
- 가독성만 검증합니다. 사실관계 · 근거 · 태그의 정확성은 이전 단계에서 검증했으므로 다루지 않습니다.
- 수정 방향은 원문에 없는 내용 추가, 결정 상태 · 논의의 강도(제안 · 결정 · 우려 등) · 조건 · 수치 · 대상 변경이 필요 없는 범위에서만 제시합니다.
  그런 변경 없이는 개선할 수 없으면 이슈를 만들지 않습니다. (가독성보다 내용 정확성 우선)
- 수정 후에도 문장이 그 항목의 근거 발화로 뒷받침되어야 합니다. 근거 발화에 없는 내용이 필요한 수정 방향은 제시하지 않습니다.
- 수정이 필요한 문장만 반환합니다. 이미 읽기 좋은 문장이나, 단순히 표현 취향이 다른 정도는 반환하지 않습니다.
- 항목 하나에 이슈는 최대 하나입니다. 문제가 여러 개면 가장 중요한 기준으로 만들고, reason과 revision_instruction에 함께 적습니다.
- 항목을 나누거나 합치는 방향은 제시하지 않습니다. (항목 하나는 한 문장, 길어도 짧은 두 문장 이내)

# 작성 규칙
- target_item_id는 입력에 있는 항목 ID를 그대로 씁니다.
- current_text에는 대상 항목의 현재 문장을 그대로 씁니다.
- reason에는 현재 문장의 어떤 부분이 왜 읽기 어려운지를 구체적으로 씁니다.
- revision_instruction에는 수정할 때 반영할 구체적인 방향을 씁니다. (예: "'~하고, ~하며'로 이어진 세 절을 두 절로 줄이고 주어를 앞에 둠")
- 문제가 없으면 validation_result를 PASS, issues를 빈 배열로 반환합니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다. 새 ID를 만들지 않습니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 근거 발화 (참고용)
{evidence}
"""


def format_item(item: SummaryItem) -> str:
    return f"- {item.item_id} | {getattr(item, 'tag', None) or '-'} | 발화 {item.evidence_utterance_indices} | {item.text}"


def format_items(agenda: Agenda) -> str:
    # 삭제된 항목만 제외 (REVIEW_REQUIRED 항목도 회의록에 표시되므로 포함)
    blocks = []
    for section in SECTIONS:
        lines = [format_item(item) for item in agenda.section(section) if item.status != "DELETED"]
        blocks.append(f"## {SECTION_LABEL[section]}\n" + ("\n".join(lines) or "(없음)"))
    return "\n\n".join(blocks)


def format_evidence(indexed_texts: list[tuple[int, str]]) -> str:
    # 번호가 이어지지 않는 곳에 "..." 표시
    lines, prev = [], None
    for index, text in indexed_texts:
        if prev is not None and index != prev + 1:
            lines.append("...")
        lines.append(f"[{index}] {text}")
        prev = index
    return "\n".join(lines) or "(없음)"


def build_user_prompt(agenda: Agenda, indexed_texts: list[tuple[int, str]]) -> str:
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        items=format_items(agenda),
        evidence=format_evidence(indexed_texts),
    )
