"""[3] 검증 결과에 따른 재수정 (LLM, 재수정 1회 = 호출 1회)

입력: 회의 정보 · 전사문 · 현재 요약(item_id 포함) · 검증 문제 목록(issue_id 포함)
출력: LLMRevision — 항목 단위 수정 명령 (요약 전체를 다시 쓰지 않음 → item_id · 수정 이력 유지)
    *_updates      기존 항목을 수정 후 값으로 다시 씀 (바꾸지 않는 필드도 현재 값 그대로)
    *_additions    안건(agenda_id) · 섹션에 새 항목 추가 (ID는 코드가 부여)
    deletions      항목 삭제 (DELETED로 표시, 이력 보존)
    agenda_title_updates  안건 제목 수정
적용 · 검사: revision.apply_revision (근거 발화 검사 · 액션 태그 규칙은 생성 후처리와 같은 코드)
추가 지침은 이후 '# 수정 원칙'에 반영
"""

from prompt.common import compose, format_issues, format_meeting_info, format_summary, format_transcript_block

SYSTEM_PROMPT_TEMPLATE = """\
당신은 회의록 수정자입니다. 검증 단계에서 보고된 문제를 해결하도록 회의록의 해당 항목만 고칩니다.

# 입력 구성
{INPUT_RULES}
- [요약]: 현재 회의록입니다. 항목마다 item_id가 붙어 있습니다. ("[null]"은 태그 없음)
- [검증 결과]: 검증 단계에서 보고된 문제 목록입니다. "issue_id | 문제 유형 | 대상 항목 · 안건 | 근거" 다음 줄에 문제와 수정 제안이 있습니다.

# 수정 원칙
- 보고된 문제를 해결하는 데 필요한 항목만 고칩니다. 문제와 관계없는 항목은 바꾸지 않습니다.
- 고치기 전에 전사문을 다시 확인합니다. 검증 결과가 전사문과 맞지 않아 실제 문제가 아니라고 판단되면 고치지 않습니다.
- 수정 제안은 참고 사항입니다. 아래 작성 기준에 맞게 문제를 해결하는 방법을 직접 판단합니다.
- 수정 방법
  - 항목 수정: *_updates에 item_id와 함께 수정 후 값을 모든 필드에 씁니다. 바꾸지 않는 필드도 [요약]의 현재 값을 그대로 씁니다.
  - 항목 추가: *_additions에 들어갈 안건의 agenda_id와 함께 씁니다. (누락된 내용 · 항목 분리)
  - 항목 삭제: deletions에 item_id를 씁니다. (근거 없는 내용 · 중복 · 사소한 내용)
  - 항목 합치기: 남길 항목 하나를 수정하고, 나머지 항목은 삭제합니다.
  - 항목 나누기: 원래 항목을 수정하고, 나머지 내용은 새 항목으로 추가합니다.
  - 다른 안건으로 옮기기: 원래 항목을 삭제하고, 옮길 안건에 새 항목으로 추가합니다.
  - 안건 제목: agenda_title_updates에 씁니다. 새 안건 만들기 · 안건 합치기는 하지 않습니다. ('기타' 안건은 예외)
  - '기타' 안건으로 옮기기: 원래 항목을 삭제하고, *_additions의 agenda_id에 [요약]에 있는 '기타' 안건의 ID를 씁니다. '기타' 안건이 아직 없으면 agenda_id에 "기타"라고 쓰면 시스템이 만듭니다.
    항목을 모두 옮겨 비게 된 안건은 시스템이 목록에서 뺍니다.
- item_id · agenda_id는 [요약]에 있는 값만 씁니다. 새 ID를 만들지 않습니다. (위 "기타"만 예외)
- 모든 수정에는 reason(무엇을 왜 고쳤는지 한 문장)과 resolves_issue_ids(해결한 issue_id 목록)를 씁니다.
- 고칠 문제가 없으면 모든 배열을 빈 배열로 출력합니다.

# 작성 기준 (수정한 항목도 아래 기준을 따름)
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
"""

USER_TEMPLATE = """\
# 회의 정보
{meeting_info}

{transcript}

# 요약 (현재 회의록)
{summary}

# 검증 결과
{issues}
"""


def build_system_prompt(speaker_mode: str) -> str:
    return compose(SYSTEM_PROMPT_TEMPLATE, speaker_mode)


def build_user_prompt(utterances: list[dict], metadata: dict[str, str], summary, issues) -> str:
    return USER_TEMPLATE.format(meeting_info=format_meeting_info(metadata),
                                transcript=format_transcript_block(utterances),
                                summary=format_summary(summary), issues=format_issues(issues))
