"""[4] 특정 문장 재수정 프롬프트 — 내용 검증 이슈를 반영해 안건 하나의 지적된 항목만 다시 작성

SYSTEM_PROMPT: 처리 방식별 작성 방법 · 태그 · 소주제 · 문장 · 근거 규칙
build_user_prompt: 안건 요약 항목 + 수정 요청(요청 ID별 이슈) + 원본 발화
(삭제 요청은 코드가 처리하므로 프롬프트에 넣지 않음)
"""

from prompt.content_validation_prompt import SECTION_LABEL, format_item, format_items, format_utterances
from schemas import Agenda, ContentIssue

SYSTEM_PROMPT = """\
당신은 회의록 수정 담당자입니다. 검수자가 지적한 수정 요청을 반영하여 안건 하나의 요약 항목을 다시 작성합니다.
수정 요청에 해당하는 항목만 작성하고, 다른 항목은 다루지 않습니다.

# 입력 구성
- [요약 항목]: 안건의 현재 요약 항목입니다. "- 항목 ID | 태그 | 소주제 | 근거 발화 | 문장" 형식입니다.
- [수정 요청]: 검수자의 지적 사항입니다. 요청 ID마다 처리 방식 · 섹션 · 대상 항목 · 지적 기준 · 사유 · 수정 방향 · 판단 근거 발화가 주어집니다.
- [원본 발화]: 이 안건이 다루어진 구간의 전사문입니다. "[발화 번호] 내용" 형식이며, 생략된 구간은 "..."으로 표시됩니다.
  - 원본 발화에는 다른 안건의 논의도 섞여 있을 수 있습니다. 이 안건과 관련된 내용만 씁니다.
  - 전사문은 음성 인식 결과라 군말 · 오인식이 있을 수 있습니다. 문맥상 분명한 의미로 정리하되, 불분명한 내용을 만들어내지 않습니다.

# 처리 방식별 작성
- 수정: 대상 항목을 수정 방향에 맞게 다시 작성합니다. 지적되지 않은 부분의 의미는 그대로 유지합니다.
- 병합: 대상 항목과 합칠 항목들의 내용을 빠짐없이 담아 한 항목으로 작성합니다. 근거 발화도 합칩니다.
- 추가: 지정된 섹션에 들어갈 새 항목을 수정 방향에 맞게 작성합니다.
- 요청 ID 하나에 결과 하나를 반환하며, target_item_id에는 요청 ID를 그대로 씁니다. 요청에 없는 ID를 만들지 않습니다.

# 태그 (해당 섹션의 태그만 사용하며, 새 태그를 만들지 않음)
- discussion_background(논의 배경): 태그 없음 → null
- major_discussions(주요 논의): 제안(아이디어 · 대안 제시) / 반대·우려(반대, 위험 · 문제 제기) / 질문(답변되지 않았거나 별도 논의로 발전한 질문) / 정보(사실 · 현황 · 수치 공유)
- decisions(결정사항): 결정(명시적 합의, 잠정 결론 포함) / 조건부 결정(조건 충족 시에만 유효) / 보류(의도적 연기 또는 의견 차이로 결론 못 냄) / 공유(의사결정 없는 보고 · 공유)
- unresolved_issues(미해결 이슈): 확인 필요 / 승인 필요 / 세부 미정 / 미논의 (해당 태그가 없으면 null)
- next_meeting_topics(다음 회의 후보 안건): 결과 공유 / 재논의
- 수정 · 병합에서 수정 방향에 태그 변경이 없으면 대상 항목의 현재 태그를 유지합니다.

# 소주제 (subtopic)
- major_discussions만 소주제를 나타내는 10자 이내 명사구를 씁니다. 다른 섹션은 빈 문자열입니다.
- 수정 · 병합에서는 수정 방향에 소주제 변경이 없으면 대상 항목의 현재 소주제를 유지합니다.

# 문장 작성 규칙
- 항목 하나에는 독립된 내용 하나만 씁니다.
- 원문에서 확인되는 내용만 씁니다. 추측, 해석, 과장을 덧붙이지 않고 수치 · 조건 · 대상은 원문 그대로 유지합니다.
- 회의록 문체로 "~함", "~됨", "~제기됨"과 같이 간결하게 끝맺습니다. (예: "원문 로그를 30일간 보존하는 방안이 제안됨.")
- 다른 항목과 같은 내용을 반복하지 않습니다.

# 근거 발화
- evidence_utterance_indices에는 작성한 문장을 직접 뒷받침하는 원본 발화 번호만 넣습니다. 원본 발화에 없는 번호는 쓰지 않습니다.
- 판단 근거 발화를 참고하되, 문장과 관계없는 발화는 넣지 않습니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 수정 요청
{requests}

# 원본 발화
{utterances}
"""


def format_request(agenda: Agenda, request_id: str, issue: ContentIssue) -> str:
    lines = [f"## 요청 ID: {request_id}",
             f"- 처리 방식: {issue.action}",
             f"- 섹션: {SECTION_LABEL[issue.section]} ({issue.section})"]
    if issue.action == "추가":
        lines.append("- 대상 항목: (없음, 새 항목 작성)")
    else:
        lines.append(f"- 대상 항목: {format_item(agenda.find_item(issue.target_item_id))[2:]}")
    if issue.action == "병합":
        lines += [f"- 합칠 항목: {format_item(agenda.find_item(i))[2:]}" for i in issue.related_item_ids]
    lines += [f"- 지적 기준: {issue.criterion}",
              f"- 지적 사유: {issue.reason}",
              f"- 수정 방향: {issue.revision_instruction}",
              f"- 판단 근거 발화: {issue.evidence_utterance_indices}"]
    return "\n".join(lines)


def build_user_prompt(agenda: Agenda, requests: dict[str, ContentIssue], indexed_texts: list[tuple[int, str]]) -> str:
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        items=format_items(agenda),
        requests="\n\n".join(format_request(agenda, rid, issue) for rid, issue in requests.items()),
        utterances=format_utterances(indexed_texts),
    )
