"""[1] 구조화된 요약문 생성 및 액션 아이템 추출 (LLM 1회, ID 없음)

SYSTEM_PROMPT_TEMPLATE: 역할 + 공통 블록(prompt/common.py) + 출력 규칙
    {INPUT_RULES} {GOALS} {AGENDA_RULES} {SECTION_RULES} {TAG_RULES} {ACTION_RULES}
    {WRITING_CONTENT_RULES} {READABILITY_RULES} {EVIDENCE_RULES}
    화자 모드(speaker / none)에 따라 바뀌는 부분은 common.SPEAKER_MODE_PARTS
build_system_prompt(speaker_mode) · build_user_prompt(utterances, metadata)

태그 구조
- 주요 논의 항목 = [논의 종류 태그][결정사항 태그] 요약문 [근거 발화 인덱스]
  - 논의 종류: 제안 / 반대·우려 / 질문 / 정보 / 액션
  - 결정사항: 결정 / 보류 / 공유 / None (결정과 연관 없는 논의)
  - 액션은 결정사항 태그 '결정' 고정 (최종 결정되어 실제로 진행할 작업)
- 결정사항을 별도 섹션으로 다시 추출하지 않음 → 회의 결과 · 안건 목차의 결정 수는 코드로 취합
  - 결정 수: decision_tag == '결정'인 항목만 셈 (보류 · 공유 제외, [액션][결정] 제외 — 액션은 앞선 결정에 따라 부여되는 후속 작업)
- 액션 아이템도 별도 추출하지 않음 → 논의 종류 태그가 '액션'인 항목을 코드로 취합
  - 액션 항목만 assignees(담당자, 여러 명 가능) · due(기한)를 채움 (확인되지 않으면 빈 배열 · null), 다른 태그는 항상 빈 배열 · null
  - 판단 · 분리 · 병합 기준: 'Action Item 추출 및 검증 기준' 문서 (액션은 [결정] 고정이므로 실행 의도(합의 · 지시 · 약속)는 필수로 조정)

분할 이력
- 기존 '# 자체 점검'은 검증 프롬프트(Verify_summary.py)의 검증 기준으로 이동
- 가독성 규칙은 생성 · 재수정 프롬프트에만 두고 별도 검증은 하지 않음
- item_id · agenda_id는 LLM이 만들지 않음 (후처리 코드에서 부여)
"""

from prompt.common import compose, format_meeting_info, format_transcript_block

SYSTEM_PROMPT_TEMPLATE = """\
당신은 회의 전사문 전체를 읽고, 안건별 구조화 요약을 작성하는 회의록 작성자입니다.
요약 과정에서 각 논의가 어떤 결정으로 이어졌는지와, 결정에 따라 실제로 진행할 작업(액션)을 함께 표시합니다.
사람이 직접 작성한 것처럼 자연스럽고, 원문에 충실하며, 핵심만 담긴 회의록을 만듭니다.

# 입력 구성
{INPUT_RULES}

{GOALS}

{AGENDA_RULES}

{SECTION_RULES}

{TAG_RULES}

{ACTION_RULES}

{WRITING_CONTENT_RULES}
{READABILITY_RULES}

{EVIDENCE_RULES}

# 출력
- 지정된 JSON 스키마로만 출력합니다.
- 항목 ID(item_id) · 안건 ID(agenda_id)는 만들지 않습니다. (시스템이 출력 후 부여)
"""

USER_TEMPLATE = """\
# 회의 정보
{meeting_info}

{transcript}
"""


def build_system_prompt(speaker_mode: str) -> str:
    return compose(SYSTEM_PROMPT_TEMPLATE, speaker_mode)


def build_user_prompt(utterances: list[dict], metadata: dict[str, str]) -> str:
    return USER_TEMPLATE.format(meeting_info=format_meeting_info(metadata),
                                transcript=format_transcript_block(utterances))
