"""[2-1] 안건 재구성 프롬프트 (agenda_split 변형) — 큰 범주 안건 하나의 항목을 흐름 · 핵심 안건 단위의 안건으로 다시 묶음

SYSTEM_PROMPT: 안건 단위 기준 · 항목 배정 규칙 · 안건 제목 규칙 (항목 문장은 고치지 않음)
build_user_prompt: 회의 정보 + 현재 안건 + 목표 안건 수 + 요약 항목(논의 순서)
"""

from prompt.content_validation_prompt import SECTION_LABEL
from schemas import Agenda, SummaryItem

SYSTEM_PROMPT = """\
당신은 회의록 편집자입니다. 회의 요약의 안건 하나에 너무 많은 세부 논의가 묶여 있어,
안건 목록만 보고는 회의에서 무엇을 어떤 흐름으로 논의했는지 파악하기 어렵습니다.
입력된 요약 항목을 '안건 목록을 통해 핵심 논의 사항과 흐름을 이해할 수 있는' 단위의 안건으로 다시 묶습니다.
항목의 문장 · 태그 · 근거는 고치지 않고, 어느 안건에 속하는지만 정합니다.

# 입력 구성
- [회의 정보]: 회의 제목 등 메타데이터입니다.
- [현재 안건]: 지금 하나로 묶여 있는 큰 범주의 안건입니다.
- [요약 항목]: "- 항목 ID | 섹션 | 태그 | 소주제 | 근거 발화 | 문장" 형식이며, 근거 발화 순서(= 논의 순서)로 정렬되어 있습니다.

# 안건 단위 기준
- 안건 하나는 하나의 논의 주제와 그에 대한 배경 · 의견 · 결론(결정사항 · 미해결 이슈)을 함께 담는 단위입니다.
  (예: '목표 고객과 시장 조사 결과', '버튼 구성 및 배치', '전원 공급 방식', '외관 소재와 색상')
- 안건 목록만 읽어도 회의의 핵심 논의와 진행 흐름이 보여야 합니다.
- 소주제 하나하나를 안건으로 만들지 않습니다. 같은 대상 · 같은 목적을 다루는 소주제는 한 안건으로 묶습니다.
- 항목이 한두 개뿐인 안건은 만들지 않습니다. 단, 중요한 결정이 따로 내려진 독립 주제는 예외입니다.
- 회의 진행 · 발표 순서 안내처럼 내용이 없는 항목만으로 안건을 만들지 않고, 관련된(또는 가장 가까운) 안건에 넣습니다.

# 항목 배정 규칙
- 모든 항목 ID를 정확히 한 안건에 배정합니다. 입력 ID를 그대로 쓰며, 누락 · 중복 · 새 ID가 없어야 합니다.
- 결정사항 · 미해결 이슈 · 다음 회의 후보 안건 항목은 그 결론이 나온 논의와 같은 안건에 넣습니다.
- 논의 배경 항목은 그 배경이 설명하는 논의의 안건에 넣습니다.
- 같은 주제가 회의 후반에 다시 논의되면 처음 다룬 안건에 함께 넣습니다.
- 안건은 처음 논의된 순서대로 나열합니다.

# 안건 제목
- 20자 이내 명사구로, 그 안건에서 다룬 핵심 대상이 드러나게 씁니다.
- 회의 대주제나 현재 안건 제목(예: '콘셉트 설계')을 그대로 쓰지 않습니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다.
"""

USER_TEMPLATE = """\
# 회의 정보
{meeting_info}

# 현재 안건
{agenda_id} | {agenda_title}

# 목표 안건 수
{target_min}~{target_max}개 (논의 내용에 따라 조정 가능, 최대 {max_agendas}개)

# 요약 항목 ({item_count}개)
{items}
"""


def format_item(section: str, item: SummaryItem) -> str:
    return (f"- {item.item_id} | {SECTION_LABEL[section]} | {getattr(item, 'tag', None) or '-'} | "
            f"{getattr(item, 'subtopic', '') or '-'} | 발화 {item.evidence_utterance_indices} | {item.text}")


def ordered_items(agenda: Agenda) -> list[tuple[str, SummaryItem]]:
    # 삭제되지 않은 항목을 첫 근거 발화 순서(= 논의 순서)로 정렬
    return sorted(agenda.iter_items(), key=lambda x: (x[1].evidence_utterance_indices[:1] or [-1], x[1].item_id))


def build_user_prompt(agenda: Agenda, metadata: dict[str, str], target: tuple[int, int], max_agendas: int) -> str:
    items = ordered_items(agenda)
    return USER_TEMPLATE.format(
        meeting_info="\n".join(f"- {k}: {v}" for k, v in metadata.items()) or "(없음)",
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        target_min=target[0],
        target_max=target[1],
        max_agendas=max_agendas,
        item_count=len(items),
        items="\n".join(format_item(section, item) for section, item in items),
    )
