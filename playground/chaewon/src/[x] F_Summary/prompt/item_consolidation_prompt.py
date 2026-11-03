"""[2-2] 항목 정돈 프롬프트 (agenda_split 변형) — 발화 단위로 잘게 나뉜 안건 하나의 항목을 핵심 중심으로 병합 · 이동 · 제거

SYSTEM_PROMPT: 정돈 방법(병합 · 이동 · 제거 · 소주제) · 작성 규칙 · 태그
build_user_prompt: 안건 + 요약 항목 + 근거 발화 (참고용)
"""

from prompt.content_validation_prompt import SECTION_LABEL, format_utterances
from prompt.agenda_restructure_prompt import ordered_items
from schemas import Agenda, SummaryItem

SYSTEM_PROMPT = """\
당신은 회의록 편집자입니다. 안건 하나의 요약 항목이 발화 단위로 잘게 나뉘어 있어, 항목 수가 많고 비슷한 내용이 흩어져 있습니다.
항목을 정돈하여, 읽는 사람이 이 안건의 배경 · 핵심 논의 · 결론을 빠르게 파악할 수 있는 요약 항목 목록으로 다시 구성합니다.

# 입력 구성
- [요약 항목]: "- 항목 ID | 섹션 | 태그 | 소주제 | 근거 발화 | 문장" 형식이며, 논의 순서로 정렬되어 있습니다.
- [근거 발화 (참고용)]: 요약 항목의 근거가 된 전사문입니다. 문장의 의미를 확인하는 참고용입니다.

# 정돈 방법
1. 병합: 같은 섹션에서 같은 대상 · 같은 논의 흐름을 다루는 항목은 하나로 합칩니다.
   - [정보] 항목은 사실을 하나씩 나열하지 않고, 이 안건의 제안 · 결정을 이해하는 데 필요한 현재 상황 · 조사 결과 · 조건을 중심으로 묶어 정리합니다.
   - 같은 제안이 여러 발화에서 반복 · 구체화되었으면, 구체화된 내용을 담아 하나로 합칩니다.
   - 의견의 방향이 다른 항목(제안과 반대·우려)이나 논의 상태가 다른 항목(제안과 결정)은 합치지 않습니다.
   - 서로 다른 결정은 합치지 않습니다. 같은 결정이 반복 기록된 경우만 합칩니다.
2. 이동: 주요 논의의 [정보] 항목 중 이 안건이 논의된 배경 · 전제(현황, 조사 결과, 이전 회의 내용, 제약 조건)에 해당하는 것은
   논의 배경(discussion_background)으로 옮깁니다. 기존 논의 배경 항목과 합칠 수 있습니다. 이 외의 섹션 이동은 하지 않습니다.
3. 제거: 안건 이해에 필요 없는 사소한 항목(진행 안내, 발표 순서, 단순 확인)은 removed에 넣습니다.
   결정사항 · 미해결 이슈 · 다음 회의 후보 안건 항목은 제거하지 않습니다.
4. 소주제: 주요 논의 항목의 소주제는 안건 안에서 2~4개의 큰 묶음이 되도록 다시 정합니다. (10자 이내 명사구)
5. 정돈할 필요가 없는 항목은 source_item_ids에 그 항목 하나만 넣고 문장 · 태그를 그대로 씁니다.

# 작성 규칙
- 모든 입력 항목 ID는 items의 source_item_ids 또는 removed 중 정확히 한 곳에만 나옵니다. 새 ID를 만들지 않습니다.
- 원문에 없는 내용을 추가하지 않습니다. 합친 문장은 원래 항목들의 내용만 담되, 중요한 내용을 빠뜨리지 않습니다.
- 수치 · 조건 · 대상 · 결정 상태(제안 · 결정 · 우려 등)는 원래 항목 그대로 유지합니다.
- evidence_utterance_indices에는 원래 항목들의 근거 발화 중 결과 문장을 뒷받침하는 번호를 넣습니다.
- 회의록 문체로 "~함", "~됨", "~제기됨"과 같이 끝맺습니다. 합친 항목도 두 문장 이내로 씁니다.
- items는 섹션별로 논의 순서대로 나열합니다.

# 태그 (해당 섹션의 태그만 사용)
- discussion_background(논의 배경): null
- major_discussions(주요 논의): 제안 / 반대·우려 / 질문 / 정보
- decisions(결정사항): 결정 / 조건부 결정 / 보류 / 공유
- unresolved_issues(미해결 이슈): 확인 필요 / 승인 필요 / 세부 미정 / 미논의 / null
- next_meeting_topics(다음 회의 후보 안건): 결과 공유 / 재논의
- 합친 항목은 원래 항목들의 태그를 유지합니다. ([정보]를 논의 배경으로 옮기면 null)

# 출력
- 지정된 JSON 스키마로만 출력합니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목 ({item_count}개)
{items}

# 근거 발화 (참고용)
{utterances}
"""


def format_item(section: str, item: SummaryItem) -> str:
    return (f"- {item.item_id} | {SECTION_LABEL[section]}({section}) | {getattr(item, 'tag', None) or '-'} | "
            f"{getattr(item, 'subtopic', '') or '-'} | 발화 {item.evidence_utterance_indices} | {item.text}")


def build_user_prompt(agenda: Agenda, indexed_texts: list[tuple[int, str]]) -> str:
    items = ordered_items(agenda)
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        item_count=len(items),
        items="\n".join(format_item(section, item) for section, item in items),
        utterances=format_utterances(indexed_texts),
    )
