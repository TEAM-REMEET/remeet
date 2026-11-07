"""[HITL 2] 근거 연결 검증 (LLM, 검증 1회 = 호출 1회)

입력: 회의 정보 · 전사문 · 근거가 연결된 액션
출력: LLMEvidenceVerification — 문제 목록 (빈 배열 = 통과)
검증 대상은 근거 연결뿐 — 사용자가 확정한 값이 맞는지는 판단하지 않음
"""

from followup.analysis.summary.prompts.common import compose, format_meeting_info, format_transcript_block

SYSTEM_PROMPT_TEMPLATE = """\
당신은 회의록의 근거 발화 검증자입니다. 사용자가 확정한 액션 아이템에 연결된 근거 발화가 적절한지 전사문과 비교하여 확인합니다.
근거를 직접 고치지 않고, 문제가 있는 항목과 그 이유만 보고합니다. (수정은 다음 단계에서 진행)

# 입력 구성
{INPUT_RULES}
- [검증 대상]: 근거가 연결된 액션입니다. "item_id | 안건: 제목 | assignees: 이름, 이름 | due: 값 | 액션명 | 근거 [발화 번호]" 형식입니다.
  - assignees: []는 담당자가 지정되지 않았음을, due: null은 일정이 지정되지 않았음을 뜻합니다.

# 검증 기준 (issue_type)
- 관련 없는 근거: 연결된 발화가 액션을 뒷받침하지 않는가? (같은 주제를 언급했을 뿐이거나 다른 업무에 관한 발화)
- 핵심 근거 누락: 그 업무를 지시 · 제안 · 약속 · 합의 · 결정한 발화, 또는 지정된 담당자 · 일정이 확인되는 발화가 전사문에 있는데 빠졌는가?

# 보고 규칙
- 사용자가 확정한 값은 정답입니다. 액션명 · 담당자 · 일정이 전사문과 다르거나 전사문에 없다는 것은 문제로 보고하지 않습니다.
- assignees가 비었거나 due가 null인 것은 문제가 아닙니다.
- 근거가 빈 배열이어도, 뒷받침하는 발화가 전사문에 실제로 없으면 문제가 아닙니다. 빠진 발화가 있을 때만 보고합니다.
- 전사문으로 확인되는 명확한 문제만 보고합니다. 있어도 없어도 되는 발화는 보고하지 않습니다.
- 문제 하나당 issue 하나로 씁니다.
- item_id: [검증 대상]에 있는 ID만 씁니다.
- utterance_indices: 관련 없는 근거는 해당 발화 번호, 핵심 근거 누락은 추가해야 할 발화 번호입니다.
- description: 무엇이 왜 문제인지 한두 문장으로 씁니다.
- 문제가 없으면 issues를 빈 배열로 출력합니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다.
"""

USER_TEMPLATE = """\
# 회의 정보
{meeting_info}

{transcript}

# 검증 대상
{targets}
"""


def build_system_prompt(speaker_mode: str) -> str:
    return compose(SYSTEM_PROMPT_TEMPLATE, speaker_mode)


def build_user_prompt(utterances: list[dict], metadata: dict[str, str], targets: str) -> str:
    return USER_TEMPLATE.format(meeting_info=format_meeting_info(metadata), transcript=format_transcript_block(utterances),
                                targets=targets)
