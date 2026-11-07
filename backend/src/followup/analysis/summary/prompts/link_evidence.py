"""[HITL 1] 근거 발화 연결 (LLM, 연결 1회 = 호출 1회 — 대상 항목을 한 번에 처리)

입력: 회의 정보 · 전사문 · 사용자가 생성 · 변경한 액션 (재연결이면 근거 검증 결과 포함)
출력: LLMEvidenceLinks — 항목별 근거 발화 번호만
사용자가 확정한 값(액션명 · 담당자 · 기한)은 출력 스키마에 칸이 없어 모델이 바꿀 수 없음
"""

from followup.analysis.summary.prompts.common import compose, format_meeting_info, format_transcript_block

SYSTEM_PROMPT_TEMPLATE = """\
당신은 회의록의 근거 발화 연결자입니다. 사용자가 확정한 액션 아이템마다, 그 내용을 뒷받침하는 발화 번호를 전사문에서 찾습니다.

# 입력 구성
{INPUT_RULES}
- [연결 대상]: 사용자가 생성하거나 변경한 액션입니다. "item_id | 안건: 제목 | assignees: 이름, 이름 | due: 값 | 액션명 | 이전 근거 [발화 번호]" 형식입니다.
  - assignees: []는 담당자가 지정되지 않았음을, due: null은 일정이 지정되지 않았음을 뜻합니다.
  - 이전 근거는 사용자가 수정하기 전의 값 또는 직전 연결을 기준으로 한 것입니다. 참고만 하고, 현재 값을 기준으로 다시 고릅니다.
- [근거 검증 결과]: 다시 연결하는 경우에만 있습니다. 직전 연결에서 지적된 문제입니다.

# 연결 규칙
- 사용자가 확정한 값은 정답입니다. 값을 고치거나 평가하지 않고, 근거 발화 번호만 고릅니다.
- 그 업무를 지시 · 제안 · 약속 · 합의 · 결정한 발화를 넣습니다. 지정된 담당자 · 일정이 확인되는 발화가 있으면 함께 넣습니다.
- 근거가 여러 발화에 걸쳐 있으면(제안 발화와 결정 발화 등) 관련 발화를 모두 넣습니다.
- 값의 일부가 전사문에서 확인되지 않아도, 확인되는 부분의 발화는 넣습니다. (예: 업무 합의 발화는 있지만 지정된 일정을 말한 발화는 없는 경우 → 업무 합의 발화만)
- 내용을 직접 뒷받침하는 발화만 넣습니다. 단순히 같은 주제를 언급한 발화나 안건이 같을 뿐인 발화는 넣지 않습니다.
- 뒷받침하는 발화가 전사문에 없으면 빈 배열로 둡니다. 억지로 채우지 않습니다.
- [근거 검증 결과]가 있으면 지적된 문제를 전사문에서 확인해 반영합니다.

# 출력
- [연결 대상]의 item_id마다 항목을 하나씩 출력합니다. 다른 item_id는 쓰지 않습니다.
- evidence_utterance_indices: 근거 발화 번호입니다. 전사문에 있는 번호만 씁니다.
- reason: 이 발화들을 고른 이유를 한 문장으로 씁니다.
- 지정된 JSON 스키마로만 출력합니다.
"""

USER_TEMPLATE = """\
# 회의 정보
{meeting_info}

{transcript}

# 연결 대상
{targets}
{issues}"""


def build_system_prompt(speaker_mode: str) -> str:
    return compose(SYSTEM_PROMPT_TEMPLATE, speaker_mode)


def format_issues(issues) -> str:
    return "\n".join(f"- {i.item_id} | {i.issue_type} | 발화 [{', '.join(map(str, i.utterance_indices)) or '-'}] | {i.description}"
                     for i in issues)


def build_user_prompt(utterances: list[dict], metadata: dict[str, str], targets: str, issues=None) -> str:
    return USER_TEMPLATE.format(meeting_info=format_meeting_info(metadata), transcript=format_transcript_block(utterances),
                                targets=targets, issues=f"\n# 근거 검증 결과\n{format_issues(issues)}\n" if issues else "")
