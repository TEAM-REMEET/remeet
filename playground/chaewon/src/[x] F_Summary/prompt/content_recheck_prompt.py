"""[5] 내용 재검증 프롬프트 — [4] 재수정에서 고친 항목만 수정 전후 · 이전 지적과 함께 원본 발화와 대조

SYSTEM_PROMPT: 재검증 방법 · 검증 기준 · 처리 방식 · 태그 정의 · 출력 규칙
build_user_prompt: 안건 요약 항목(수정 반영) + 재검증 대상(수정 전 · 수정 후 · 이전 지적) + 원본 발화
"""

from prompt.content_validation_prompt import SECTION_LABEL, format_item, format_items, format_utterances
from schemas import Agenda, ContentIssue, SummaryItem

SYSTEM_PROMPT = """\
당신은 회의록 검수자입니다. 이전 검증에서 지적되어 수정된 요약 항목을 원본 발화와 대조하여,
지적 사항이 제대로 반영되었는지와 수정 과정에서 새로운 문제가 생기지 않았는지 재검증합니다. 항목을 직접 고쳐 쓰지 않습니다.

# 입력 구성
- [요약 항목]: 수정 결과가 반영된 안건의 현재 요약 항목 전체입니다. "- 항목 ID | 태그 | 소주제 | 근거 발화 | 문장" 형식입니다.
  재검증 대상과 다른 항목의 중복 여부 등을 판단하기 위한 참고용입니다.
- [재검증 대상]: 재검증할 항목입니다. 항목마다 수정 전 · 수정 후 내용과 이전 지적(기준 · 처리 방식 · 사유 · 수정 방향)이 주어집니다.
- [원본 발화]: 이 안건이 다루어진 구간의 전사문입니다. "[발화 번호] 내용" 형식이며, 생략된 구간은 "..."으로 표시됩니다.
  - 원본 발화에는 다른 안건의 논의도 섞여 있을 수 있습니다. 이 안건과 관련된 내용만 검증합니다.
  - 전사문은 음성 인식 결과라 군말 · 오인식이 있을 수 있습니다. 문맥상 분명한 의미를 기준으로 판단합니다.

# 재검증 방법
1. 지적 반영: 수정 후 내용이 이전 지적의 사유를 해소하고 수정 방향을 따랐는지 확인합니다. 해소되지 않았으면 같은 기준으로 이슈를 만듭니다.
2. 새로운 문제: 수정 후 내용에 아래 검증 기준의 문제가 새로 생겼는지 확인합니다.
3. 재검증 대상이 아닌 항목에 대한 이슈는 반환하지 않습니다.

# 검증 기준 (criterion)
- 가짜 근거: 근거 발화 번호가 원본 발화에 없거나, 문장과 전혀 관계없는 발화임
- 원문에 없는 내용: 원본 발화에서 확인할 수 없는 사실 · 해석이 들어 있음
- 과장 및 왜곡: 발언의 강도 · 의미 · 수치 · 조건이 원문과 다름 (예: 제안을 확정처럼 표현)
- 결정 상태 오류: 태그가 잘못됨 (예: 제안을 '결정'으로, 결론 없는 논의를 '결정'으로), 이후 번복된 결정이 최종 결정으로 남아 있음
- 근거 연결 오류: 근거 발화가 문장을 뒷받침하지 못하거나, 핵심 근거 발화가 빠져 있음
- 중요 항목 누락: 수정 과정에서 원래 담겨 있던 중요한 내용(병합된 항목의 내용 포함)이 빠짐
- 중복 요약: 수정 후 내용이 다른 항목과 같은 논의 · 결정을 반복함
- 깊이 부족: 논의의 핵심 맥락이나 결과가 지나치게 축약되어 의미가 드러나지 않음
- 요약 범위: 핵심이 아닌 사소한 내용(인사, 진행 안내, 단순 동의 등)이 항목으로 들어가 있음

# 처리 방식 (action) — 재검증에서는 추가를 쓰지 않음
- 수정: 대상 항목의 문장 · 태그 · 근거를 다시 고쳐야 함 → target_item_id에 재검증 대상 항목 ID
- 삭제: 대상 항목이 불필요하거나 원문에 근거가 없음 → target_item_id에 재검증 대상 항목 ID
- 병합: 대상 항목이 같은 섹션의 다른 항목과 중복됨 → target_item_id에 재검증 대상 항목 ID, related_item_ids에 중복 항목들

# 태그 정의 (결정 상태 오류 판단 기준)
- 주요 논의: 제안(아이디어 · 대안 제시) / 반대·우려(반대, 위험 · 문제 제기) / 질문(답변되지 않았거나 별도 논의로 발전한 질문) / 정보(사실 · 현황 · 수치 공유)
- 결정사항: 결정(명시적 합의, 잠정 결론 포함) / 조건부 결정(조건 충족 시에만 유효) / 보류(의도적 연기 또는 의견 차이로 결론 못 냄) / 공유(의사결정 없는 보고 · 공유)
- 미해결 이슈: 확인 필요 / 승인 필요 / 세부 미정 / 미논의 / 없음(-)
- 다음 회의 후보 안건: 결과 공유 / 재논의
- 위 태그 외의 태그는 쓰지 않습니다.

# 작성 규칙
- 재검증 대상 항목 하나에 이슈는 최대 하나입니다. 문제가 여러 개면 가장 중요한 기준으로 만들고, reason과 revision_instruction에 함께 적습니다.
- reason에는 수정 후 내용이 원문의 어떤 발화와 어떻게 다른지, 또는 이전 지적의 어떤 부분이 반영되지 않았는지를 구체적으로 씁니다.
- revision_instruction에는 다시 수정할 때 반영할 구체적인 방향을 씁니다.
- evidence_utterance_indices에는 판단 근거가 되는 원본 발화 번호를 넣습니다. 원본 발화에 없는 번호는 쓰지 않습니다.
- 원문만으로 판단이 불확실하면 수정 방향을 단정하지 말고 uncertain을 true로 둡니다.
- 문장 다듬기 · 문체 · 맞춤법 등 가독성 문제는 이 단계에서 다루지 않습니다.
- 확실한 문제만 반환합니다. 모든 재검증 대상에 문제가 없으면 validation_result를 PASS, issues를 빈 배열로 반환합니다.

# 출력
- 지정된 JSON 스키마로만 출력합니다. 항목 ID는 입력에 있는 ID를 그대로 쓰고, 새 ID를 만들지 않습니다.
"""

USER_TEMPLATE = """\
# 안건
{agenda_id} | {agenda_title}

# 요약 항목
{items}

# 재검증 대상
{targets}

# 원본 발화
{utterances}
"""


def last_revision(item: SummaryItem):
    # [4] 재수정에서 남긴 마지막 수정 이력 (새로 추가된 항목이면 None)
    return next((h for h in reversed(item.history) if h.stage == "content_revise"), None)


def format_target(agenda: Agenda, item_id: str, previous_issues: list[ContentIssue]) -> str:
    item = agenda.find_item(item_id)
    section = next(s for s, it in agenda.iter_items(include_deleted=True) if it.item_id == item_id)
    lines = [f"## {item_id} ({SECTION_LABEL[section]})"]
    issues = [i for i in previous_issues if i.revised_item_id == item_id]
    before = last_revision(item)
    if before is None:
        lines.append("- 수정 전: (이전 검증에서 누락으로 지적되어 새로 추가된 항목)")
    else:
        lines.append(f"- 수정 전: {before.before_tag or '-'} | {before.before_subtopic or '-'} | "
                     f"발화 {before.before_evidence} | {before.before_text}")
    for issue in issues:
        for related_id in issue.related_item_ids if issue.action == "병합" else []:
            related = agenda.find_item(related_id)
            if related is not None:
                lines.append(f"- 병합된 항목: {related_id} | 발화 {related.evidence_utterance_indices} | {related.text}")
    lines.append(f"- 수정 후: {format_item(item)[2:]}")
    for issue in issues:
        lines.append(f"- 이전 지적: [{issue.criterion} / {issue.action}] {issue.reason}")
        lines.append(f"- 수정 방향: {issue.revision_instruction}")
    return "\n".join(lines)


def build_user_prompt(agenda: Agenda, indexed_texts: list[tuple[int, str]], recheck_ids: list[str],
                      previous_issues: list[ContentIssue]) -> str:
    return USER_TEMPLATE.format(
        agenda_id=agenda.agenda_id,
        agenda_title=agenda.agenda_title,
        items=format_items(agenda),
        targets="\n\n".join(format_target(agenda, i, previous_issues) for i in recheck_ids),
        utterances=format_utterances(indexed_texts),
    )
