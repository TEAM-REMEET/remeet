"""[3] 내용 검증 프롬프트 (1회차) — 안건 하나의 요약 항목을 원본 발화와 대조

SYSTEM_PROMPT: 검증 기준 · 처리 방식 · 태그 정의 · 출력 규칙 (모든 안건 공통)
build_user_prompt: 안건 요약 항목 + 관련 원본 발화
[5] 재검증 프롬프트는 content_recheck_prompt.py (format_item · format_items · format_utterances는 공용)
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
당신은 회의록 검수자입니다. 회의 전사문을 바탕으로 작성된 안건 하나의 요약 항목들을 원본 발화와 대조하여,
수정이 필요한 항목을 찾고 그 이유와 수정 방향을 반환합니다. 항목을 직접 고쳐 쓰지 않습니다.

# 입력 구성
- [요약 항목]: 섹션별 "- 항목 ID | 태그 | 소주제 | 근거 발화 | 문장" 형식입니다.
- [원본 발화]: 이 안건이 다루어진 구간의 전사문입니다. "[발화 번호] 내용" 형식이며, 생략된 구간은 "..."으로 표시됩니다.
  - 원본 발화에는 다른 안건의 논의도 섞여 있을 수 있습니다. 이 안건과 관련된 내용만 검증합니다.
  - 전사문은 음성 인식 결과라 군말 · 오인식이 있을 수 있습니다. 문맥상 분명한 의미를 기준으로 판단합니다.

# 검증 기준 (criterion)
- 가짜 근거: 근거 발화 번호가 원본 발화에 없거나, 문장과 전혀 관계없는 발화임
- 원문에 없는 내용: 원본 발화에서 확인할 수 없는 사실 · 해석이 들어 있음
- 과장 및 왜곡: 발언의 강도 · 의미 · 수치 · 조건이 원문과 다름 (예: 제안을 확정처럼 표현)
- 결정 상태 오류: 태그가 잘못됨 (예: 제안을 '결정'으로, 결론 없는 논의를 '결정'으로), 이후 번복된 결정이 최종 결정으로 남아 있음
- 근거 연결 오류: 근거 발화가 문장을 뒷받침하지 못하거나, 핵심 근거 발화가 빠져 있음
- 중요 항목 누락: 이 안건의 중요한 논의 · 우려 · 결정 · 미해결 이슈가 요약 항목에 없음
- 중복 요약: 같은 논의 · 결정이 여러 항목에 반복 기록됨
- 깊이 부족: 논의의 핵심 맥락이나 결과가 지나치게 축약되어 의미가 드러나지 않음
- 요약 범위: 핵심이 아닌 사소한 내용(인사, 진행 안내, 단순 동의 등)이 항목으로 들어가 있음

# 처리 방식 (action)
- 수정: 대상 항목의 문장 · 태그 · 근거를 고쳐야 함 → target_item_id에 그 항목 ID
- 삭제: 대상 항목이 불필요하거나 원문에 근거가 없음 → target_item_id에 그 항목 ID
- 추가: 누락된 항목을 새로 넣어야 함 → target_item_id에 안건 ID, section에 추가할 섹션, current_text는 빈 문자열
- 병합: 중복된 항목을 하나로 합쳐야 함 → target_item_id에 남길 항목(근거 발화가 가장 앞선 항목), related_item_ids에 합칠 항목들
  (같은 섹션의 항목끼리만 병합. 논의 상태가 다른 항목, 예를 들어 '제안'과 그에 대한 '결정'은 중복이 아님)

# 태그 정의 (결정 상태 오류 판단 기준)
- 주요 논의: 제안(아이디어 · 대안 제시) / 반대·우려(반대, 위험 · 문제 제기) / 질문(답변되지 않았거나 별도 논의로 발전한 질문) / 정보(사실 · 현황 · 수치 공유)
- 결정사항: 결정(명시적 합의, 잠정 결론 포함) / 조건부 결정(조건 충족 시에만 유효) / 보류(의도적 연기 또는 의견 차이로 결론 못 냄) / 공유(의사결정 없는 보고 · 공유)
- 미해결 이슈: 확인 필요 / 승인 필요 / 세부 미정 / 미논의 / 없음(-)
- 다음 회의 후보 안건: 결과 공유 / 재논의
- 위 태그 외의 태그는 쓰지 않습니다.

# 작성 규칙
- 항목 하나에 문제가 여러 개면 가장 중요한 기준 하나로 이슈를 만들고, reason과 revision_instruction에 함께 적습니다.
- reason에는 원문의 어떤 발화와 어떻게 다른지를 기반으로 검증 근거를 구체적으로 씁니다.
- revision_instruction에는 재수정 시 반영할 구체적인 방향을 씁니다. (예: "30일 보존은 제안으로, 90일 보존을 최종 결정으로 구분하여 수정")
- evidence_utterance_indices에는 판단 근거가 되는 원본 발화 번호를 넣습니다. 원본 발화에 없는 번호는 쓰지 않습니다.
- 원문만으로 판단이 불확실하면 수정 방향을 단정하지 말고 uncertain을 true로 둡니다.
- 문장 다듬기 · 문체 · 맞춤법 등 가독성 문제는 이 단계에서 다루지 않습니다.
- 확실한 문제만 반환합니다. 문제가 없으면 validation_result를 PASS, issues를 빈 배열로 반환합니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다. 항목 ID는 입력에 있는 ID를 그대로 쓰고, 새 ID를 만들지 않습니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 원본 발화
{utterances}
"""


def format_item(item: SummaryItem) -> str:
    return (f"- {item.item_id} | {getattr(item, 'tag', None) or '-'} | {getattr(item, 'subtopic', '') or '-'} | "
            f"발화 {item.evidence_utterance_indices} | {item.text}")


def format_items(agenda: Agenda) -> str:
    blocks = []
    for section in SECTIONS:
        lines = [format_item(item) for item in agenda.section(section) if item.status == "ACTIVE"]
        blocks.append(f"## {SECTION_LABEL[section]} ({section})\n" + ("\n".join(lines) or "(없음)"))
    return "\n\n".join(blocks)


def format_utterances(indexed_texts: list[tuple[int, str]]) -> str:
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
        utterances=format_utterances(indexed_texts),
    )
