"""검증 결과 정리 · 재수정 적용 · 검토 필요 표시 (LLM 호출 없음, 코드만)

to_issues       LLMVerification → VerificationIssue (issue_id 부여, 요약에 없는 item_id · agenda_id 제외)
apply_revision  LLMRevision → MeetingSummary에 항목 단위로 적용
    - 바뀐 필드마다 ItemRevision(stage='revise_{회차}') 기록, 삭제는 status=DELETED (이력 보존)
    - 수정 · 추가 항목도 생성 후처리와 같은 검사 (근거 발화 검사 · 액션 태그 규칙 · 담당자 대조 — postprocess.py)
    - 추가 항목 ID: 섹션 안 최대 번호 + 1 (삭제된 항목 번호도 다시 쓰지 않음)
    - 소주제는 항목의 subtopic으로 다시 묶음 (기존 subtopic_id 유지, 새 소주제만 새 ID)
    - agenda_id가 '기타'이면 기타 안건을 찾거나 새로 만듦 (config.MISC_AGENDA_TITLE, 다른 새 안건은 만들지 않음)
    - 적용 후 안건 순서 · 번호 · 근거 범위 · 목차 · 회의 결과 다시 계산, 빈 안건은 숨김 (postprocess.refresh_derived)
mark_review_required  마지막 검증에서 남은 문제의 항목 → status=REVIEW_REQUIRED + review_reasons
"""

import re

import config as cfg
from postprocess import ASSIGNEE_REVIEW_PREFIX, assignee_fields, check_evidence, normalize_discussion, refresh_derived
from schemas import (SECTION_PREFIX, Agenda, BackgroundItem, BaseItem, DiscussionItem, ItemRevision, LLMRevision, LLMVerification,
                     MeetingSummary, NextTopicItem, PostprocessLog, RevisionChange, Subtopic, UnresolvedItem,
                     VerificationIssue, make_agenda_id, make_item_id, make_subtopic_id)

REVIEW_PREFIX = "검증 미통과"


def stage_name(round_no: int) -> str:
    return f"revise_{round_no}"


# ── 검증 결과 정리 ─────────────────────────────────────
def to_issues(llm: LLMVerification, summary: MeetingSummary, round_no: int) -> list[VerificationIssue]:
    alive = {it.item_id for a in summary.agendas for it in a.iter_items()}
    agendas = {a.agenda_id for a in summary.agendas}
    issues = []
    for n, it in enumerate(llm.issues, 1):
        ids = list(dict.fromkeys(i.strip() for i in it.item_ids))
        unknown = [i for i in ids if i not in alive]
        agenda_id = it.agenda_id if it.agenda_id in agendas else None
        if agenda_id is None and ids and not unknown:       # 항목에서 안건을 알 수 있으면 채움
            agenda_id = summary.agenda_of(ids[0]).agenda_id
        notes = []
        if unknown:
            notes.append(f"요약에 없는 item_id 제외: {unknown}")
        if it.agenda_id and it.agenda_id not in agendas:
            notes.append(f"요약에 없는 agenda_id 제외: {it.agenda_id!r}")
        issues.append(VerificationIssue(
            issue_id=f"v{round_no}_i{n}", round=round_no, issue_type=it.issue_type,
            item_ids=[i for i in ids if i in alive], agenda_id=agenda_id, description=it.description.strip(),
            evidence_utterance_indices=sorted(set(it.evidence_utterance_indices)), suggestion=it.suggestion.strip(),
            note="; ".join(notes) or None))
    return issues


# ── 재수정 적용 ─────────────────────────────────────────
def _next_number(agenda: Agenda, section: str) -> int:
    pattern = re.compile(re.escape(f"{agenda.agenda_id}_{SECTION_PREFIX[section]}") + r"(\d+)$")
    nums = [int(m.group(1)) for it in agenda.section(section) if (m := pattern.match(it.item_id))]
    return max(nums, default=0) + 1


def _set(item: BaseItem, field: str, value, history: list[ItemRevision], stage: str, reason: str) -> bool:
    before = getattr(item, field)
    if before == value:
        return False
    history.append(ItemRevision(stage=stage, field=field, before=before, after=value, reason=reason))
    setattr(item, field, value)
    return True


def _apply_evidence(item: BaseItem, indices: list[int], valid: set[int], stage: str, reason: str,
                    log: list[PostprocessLog]) -> list[str]:
    # 근거 발화 검사 후 반영 — 근거 문제로 붙었던 검토 사유는 다시 판정
    cleaned, history, reasons = check_evidence(indices, valid, item.item_id, log, stage)
    changed = ["evidence_utterance_indices"] if _set(item, "evidence_utterance_indices", cleaned,
                                                     item.history, stage, reason) else []
    item.history += history
    item.review_reasons = [r for r in item.review_reasons if r != "유효한 근거 발화 없음"] + reasons
    if item.status != "DELETED":
        item.status = "REVIEW_REQUIRED" if item.review_reasons else "ACTIVE"
    return changed


def _update_fields(item: BaseItem, values: dict, edit, valid: set[int], stage: str,
                   log: list[PostprocessLog]) -> list[str]:
    changed = [f for f, v in values.items() if _set(item, f, v, item.history, stage, edit.reason)]
    return changed + _apply_evidence(item, edit.evidence_utterance_indices, valid, stage, edit.reason, log)


def _discussion_values(edit, item_id: str, history: list[ItemRevision], log: list[PostprocessLog],
                       stage: str) -> dict:
    decision, assignees, due = normalize_discussion(edit.discussion_tag, edit.decision_tag, edit.assignees, edit.due,
                                                    item_id, history, log, stage)
    return {"subtopic": edit.subtopic.strip() or cfg.DEFAULT_SUBTOPIC, "discussion_tag": edit.discussion_tag,
            "decision_tag": decision, "text": edit.text.strip(), "assignees": assignees, "due": due}


def rebuild_subtopics(agenda: Agenda) -> None:
    # 항목의 subtopic 기준으로 소주제 묶음을 다시 만듦 (기존 순서 · ID 유지, 새 소주제는 뒤에 새 ID)
    alive = {it.item_id: it for it in agenda.major_discussions if it.status != "DELETED"}
    by_title = {st.title: st for st in agenda.subtopics}
    lists = {st.title: [i for i in st.item_ids if i in alive and alive[i].subtopic == st.title]
             for st in agenda.subtopics}
    placed = {i for ids in lists.values() for i in ids}
    numbers = [int(m.group(1)) for st in agenda.subtopics if (m := re.search(r"_st(\d+)$", st.subtopic_id))]
    for item_id, it in alive.items():
        if item_id in placed:
            continue
        if it.subtopic not in by_title:
            number = max(numbers, default=0) + 1
            numbers.append(number)
            by_title[it.subtopic] = Subtopic(subtopic_id=make_subtopic_id(agenda.agenda_id, number),
                                             agenda_id=agenda.agenda_id, order=len(by_title) + 1,
                                             title=it.subtopic, item_ids=[])
            lists[it.subtopic] = []
        lists[it.subtopic].append(item_id)
    for title, st in by_title.items():
        st.item_ids = lists[title]          # 빈 소주제도 ID 재사용 방지를 위해 남김 (렌더링에서는 제외)
        for item_id in st.item_ids:
            alive[item_id].subtopic_id = st.subtopic_id
    agenda.subtopics = list(by_title.values())


def misc_agenda(summary: MeetingSummary, agendas: dict[str, Agenda]) -> Agenda:
    # '기타' 안건을 찾고, 없으면 새 agenda_id로 만듦 (순서 · 번호는 refresh_derived에서 정리)
    found = next((a for a in summary.agendas if a.agenda_title == cfg.MISC_AGENDA_TITLE), None)
    if found:
        return found
    numbers = [int(m.group(1)) for a in summary.agendas if (m := re.search(r"_a(\d+)$", a.agenda_id))]
    agenda = Agenda(agenda_id=make_agenda_id(summary.meeting_id, max(numbers, default=0) + 1),
                    agenda_number=len(summary.agendas) + 1, agenda_title=cfg.MISC_AGENDA_TITLE, evidence_range=[],
                    subtopics=[], discussion_background=[], major_discussions=[], unresolved_issues=[],
                    next_meeting_topics=[])
    summary.agendas.append(agenda)
    agendas[agenda.agenda_id] = agenda
    return agenda


def apply_revision(summary: MeetingSummary, rev: LLMRevision, round_no: int) -> list[RevisionChange]:
    stage = stage_name(round_no)
    valid = {u.utterance_index for u in summary.transcript}
    agendas = {a.agenda_id: a for a in summary.agendas}
    log = summary.postprocess_log
    participants = summary.meeting_info.participants
    changes: list[RevisionChange] = []

    def skipped(op: str, target: str, edit, note: str) -> None:
        changes.append(RevisionChange(op=op, target_id=target, reason=edit.reason,
                                      resolves_issue_ids=edit.resolves_issue_ids, applied=False, note=note))

    def target(item_id: str, section: str, edit) -> BaseItem | None:
        item = summary.find_item(item_id)
        if item is None or item.status == "DELETED":
            skipped("update", item_id, edit, "요약에 없거나 삭제된 item_id")
            return None
        if item.section != section:
            skipped("update", item_id, edit, f"섹션 불일치 (항목은 {item.section})")
            return None
        return item

    # 수정
    for e in rev.background_updates:
        if item := target(e.item_id, "discussion_background", e):
            fields = _update_fields(item, {"text": e.text.strip()}, e, valid, stage, log)
            changes.append(RevisionChange(op="update", target_id=e.item_id, changed_fields=fields, reason=e.reason,
                                          resolves_issue_ids=e.resolves_issue_ids))
    for e in rev.discussion_updates:
        if item := target(e.item_id, "major_discussions", e):
            values = _discussion_values(e, e.item_id, item.history, log, stage)
            person, reasons = assignee_fields(values["assignees"], participants, e.item_id, log)
            values.update(person)
            item.review_reasons = [r for r in item.review_reasons if not r.startswith(ASSIGNEE_REVIEW_PREFIX)] + reasons
            fields = _update_fields(item, values, e, valid, stage, log)
            changes.append(RevisionChange(op="update", target_id=e.item_id, changed_fields=fields, reason=e.reason,
                                          resolves_issue_ids=e.resolves_issue_ids))
    for e in rev.unresolved_updates:
        if item := target(e.item_id, "unresolved_issues", e):
            fields = _update_fields(item, {"text": e.text.strip(), "tag": e.tag}, e, valid, stage, log)
            changes.append(RevisionChange(op="update", target_id=e.item_id, changed_fields=fields, reason=e.reason,
                                          resolves_issue_ids=e.resolves_issue_ids))
    for e in rev.next_topic_updates:
        if item := target(e.item_id, "next_meeting_topics", e):
            fields = _update_fields(item, {"text": e.text.strip(), "tag": e.tag}, e, valid, stage, log)
            changes.append(RevisionChange(op="update", target_id=e.item_id, changed_fields=fields, reason=e.reason,
                                          resolves_issue_ids=e.resolves_issue_ids))

    # 추가
    def add(section: str, e, build) -> None:
        if not e.text.strip():
            skipped("add", e.agenda_id, e, "문장이 빈 항목")
            return
        # agenda_id가 '기타'이면 기타 안건을 찾거나 새로 만듦
        agenda = misc_agenda(summary, agendas) if e.agenda_id.strip() == cfg.MISC_AGENDA_TITLE else agendas.get(e.agenda_id)
        if agenda is None:
            skipped("add", e.agenda_id, e, "요약에 없는 agenda_id")
            return
        number = _next_number(agenda, section)
        item_id = make_item_id(agenda.agenda_id, section, number)
        cleaned, history, reasons = check_evidence(e.evidence_utterance_indices, valid, item_id, log, stage)
        history.insert(0, ItemRevision(stage=stage, field="item", before=None, after=item_id, reason=e.reason))
        base = {"item_id": item_id, "agenda_id": agenda.agenda_id, "order": number, "text": e.text.strip(),
                "evidence_utterance_indices": cleaned, "history": history, "review_reasons": reasons,
                "status": "REVIEW_REQUIRED" if reasons else "ACTIVE"}
        agenda.section(section).append(build(base, item_id, history))
        changes.append(RevisionChange(op="add", target_id=item_id, reason=e.reason,
                                      resolves_issue_ids=e.resolves_issue_ids))

    for e in rev.background_additions:
        add("discussion_background", e, lambda b, i, h: BackgroundItem(**b))
    def discussion(base: dict, item_id: str, history: list[ItemRevision], e) -> DiscussionItem:
        values = _discussion_values(e, item_id, history, log, stage)
        values.pop("text")
        person, reasons = assignee_fields(values["assignees"], participants, item_id, log)
        values.update(person)
        if reasons:
            base.update(review_reasons=base["review_reasons"] + reasons, status="REVIEW_REQUIRED")
        return DiscussionItem(**base, subtopic_id="", **values)     # subtopic_id는 rebuild_subtopics에서 채움

    for e in rev.discussion_additions:
        add("major_discussions", e, lambda b, i, h, e=e: discussion(b, i, h, e))
    for e in rev.unresolved_additions:
        add("unresolved_issues", e, lambda b, i, h, e=e: UnresolvedItem(**b, tag=e.tag))
    for e in rev.next_topic_additions:
        add("next_meeting_topics", e, lambda b, i, h, e=e: NextTopicItem(**b, tag=e.tag))

    # 삭제
    for e in rev.deletions:
        item = summary.find_item(e.item_id)
        if item is None or item.status == "DELETED":
            skipped("delete", e.item_id, e, "요약에 없거나 이미 삭제된 item_id")
            continue
        _set(item, "status", "DELETED", item.history, stage, e.reason)
        changes.append(RevisionChange(op="delete", target_id=e.item_id, changed_fields=["status"], reason=e.reason,
                                      resolves_issue_ids=e.resolves_issue_ids))

    # 안건 제목
    for e in rev.agenda_title_updates:
        agenda = agendas.get(e.agenda_id)
        title = e.agenda_title.strip()
        if agenda is None or not title:
            skipped("agenda_title", e.agenda_id, e, "요약에 없는 agenda_id 또는 빈 제목")
            continue
        if title == cfg.MISC_AGENDA_TITLE and any(a.agenda_title == title and a is not agenda and not a.is_empty
                                                  for a in summary.agendas):
            skipped("agenda_title", e.agenda_id, e, f"'{title}' 안건이 이미 있음 (항목을 옮겨야 함)")
            continue
        changed = agenda.agenda_title != title
        agenda.agenda_title = title
        changes.append(RevisionChange(op="agenda_title", target_id=e.agenda_id,
                                      changed_fields=["agenda_title"] if changed else [], reason=e.reason,
                                      resolves_issue_ids=e.resolves_issue_ids))

    for agenda in summary.agendas:
        rebuild_subtopics(agenda)
    refresh_derived(summary)
    return changes


# ── 검토 필요 표시 ─────────────────────────────────────
def mark_review_required(summary: MeetingSummary, issues: list[VerificationIssue]) -> int:
    # 남은 문제의 대상 항목 → REVIEW_REQUIRED (사유: '검증 미통과(유형): 설명') / 표시한 항목 수 반환
    marked = set()
    for issue in issues:
        for item_id in issue.item_ids:
            item = summary.find_item(item_id)
            if item is None or item.status == "DELETED":
                continue
            item.review_reasons.append(f"{REVIEW_PREFIX}({issue.issue_type}): {issue.description}")
            item.status = "REVIEW_REQUIRED"
            marked.add(item_id)
    return len(marked)
