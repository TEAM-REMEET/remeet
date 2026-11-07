"""사용자 수정(HITL) 반영 · 근거 연결 · 근거 검증 (흐름은 application/review_meeting.py)

사용자 수정 목록 (schemas.HitlEdits)
    {"edits": [
      {"op": "create", "agenda_id": "M_…_a1", "text": "액션명", "assignee_user_ids": ["u2"], "due": "2005-01-26"},
      {"op": "create", "agenda_id": "기타", "text": "어느 안건에도 해당하지 않는 액션"},
      {"op": "update", "item_id": "M_…_a1_d5", "text": "새 액션명"},     // 쓴 값만 변경, due: null이면 일정을 비움
      {"op": "delete", "item_id": "M_…_a2_d7"}
    ]}

validate_edits      대상 · 담당자 · 액션명 확인 (잘못되면 LLM 호출 전에 중단, 일시는 예외)
apply_edits         사용자 값 반영 + 이력 기록 → 근거 연결 대상 목록
    일시            날짜로 확인되지 않으면 오류가 아니라 빈 일정(null)
    새 액션         선택한 안건에서 마지막 결정 항목 아래, 그 결정의 소주제로 추가
    기타 안건       이미 있으면 그 agenda_id, 없으면 기존 번호의 최댓값 + 1로 새로 만듦 (revision.misc_agenda)
link_evidence · verify_evidence     [LLM] 근거 발화 번호 연결 · 검증
apply_links · to_evidence_issues · clear_unverified     근거 연결 · 검증 결과 반영 (근거 필드만 변경)
    근거 문제       근거가 없거나 마지막 검증까지 문제가 남으면 근거를 빈 배열로 저장 (REVIEW_REQUIRED로 표시하지 않음)
"""

from collections.abc import Callable

from followup import settings as cfg
from followup.analysis.summary.postprocess import ASSIGNEE_REVIEW_PREFIX, _empty_to_none, _normalize_due, evidence_range, refresh_derived
from followup.analysis.summary.preprocessing import llm_metadata, llm_names
from followup.analysis.summary.prompts import link_evidence as link_prompt
from followup.analysis.summary.prompts import verify_evidence as verify_prompt
from followup.analysis.summary.revision import _next_number, _set, misc_agenda, rebuild_subtopics
from followup.analysis.summary.schemas import (ACTION_DECISION, ACTION_TAG, AssignChanges, AssignPerson, DiscussionItem,
                                               HitlEditRecord, HitlEdits, HitlResult,
                                               HitlEvidenceIssue, ItemRevision, LLMEvidenceLinks, LLMEvidenceVerification,
                                               MeetingSummary, make_item_id)

STAGE = "hitl"
EVIDENCE_REJECTED_PREFIX = "근거 검증 미통과"
NO_EVIDENCE_REASON = "유효한 근거 발화 없음"     # 후처리 · 재수정에서 붙은 사유 (사용자가 확정한 액션에서는 해소)

StructuredCall = Callable[..., object]


class HitlEditError(ValueError):
    """사용자 수정 목록 오류 — LLM 호출 전에 중단"""

    def __init__(self, errors: list[str]):
        super().__init__("사용자 수정 목록 오류: " + " / ".join(errors))
        self.errors = errors


# ── 수정 목록 확인 ─────────────────────────────────────
def validate_edits(summary: MeetingSummary, edits: HitlEdits) -> None:
    errors, seen = [], set()
    agendas = {a.agenda_id for a in summary.active_agendas()} | {cfg.MISC_AGENDA_TITLE}
    people = {p.user_id for p in summary.meeting_info.participants}

    def check_values(label: str, e) -> None:
        fields = e.model_fields_set
        if "text" in fields and not (e.text or "").strip():
            errors.append(f"{label}: 액션명이 비어 있음")
        if "assignee_user_ids" in fields:
            if e.assignee_user_ids is None:
                errors.append(f"{label}: 담당자는 배열로 씀 (담당자 없음은 빈 배열)")
            elif unknown := [u for u in e.assignee_user_ids if u not in people]:
                errors.append(f"{label}: 참여자가 아닌 user_id {unknown}")

    for n, e in enumerate(edits.edits, 1):
        label = f"{n}번째 수정({e.op})"
        if e.op == "create":
            if e.agenda_id not in agendas:
                errors.append(f"{label}: 회의록에 없는 안건 {e.agenda_id!r}")
            check_values(label, e)
            continue
        item = summary.find_item(e.item_id)
        if item is None or item.status == "DELETED":
            errors.append(f"{label}: 회의록에 없거나 삭제된 항목 {e.item_id!r}")
        elif not (isinstance(item, DiscussionItem) and item.is_action):
            errors.append(f"{label}: 액션이 아닌 항목 {e.item_id!r}")
        if e.item_id in seen:
            errors.append(f"{label}: 같은 항목을 두 번 수정 {e.item_id!r}")
        seen.add(e.item_id)
        if e.op == "update":
            if not e.model_fields_set & {"text", "assignee_user_ids", "due"}:
                errors.append(f"{label}: 변경할 값이 없음")
            check_values(label, e)
    if errors:
        raise HitlEditError(errors)


# ── 사용자 값 반영 ─────────────────────────────────────
def _assignee_values(user_ids: list[str], summary: MeetingSummary) -> dict:
    people = {p.user_id: p for p in summary.meeting_info.participants}
    chosen = [people[u] for u in dict.fromkeys(user_ids)]
    return {"assignees": [p.display_name for p in chosen], "assignee_user_ids": [p.user_id for p in chosen],
            "assignee_names": [p.name for p in chosen], "assignee_jobs": [p.job for p in chosen]}


def _due_value(raw: str | None, item_id: str, history: list[ItemRevision],
               summary: MeetingSummary) -> tuple[str | None, str | None]:
    # (저장할 일시, 안내) — 날짜로 확인되지 않으면 오류로 멈추지 않고 빈 일정
    raw = _empty_to_none(raw)
    due = _normalize_due(raw, item_id, history, summary.postprocess_log, STAGE)
    return due, (f"날짜로 확인되지 않아 빈 일정으로 저장: {raw!r}" if raw is not None and due is None else None)


def _placement(agenda) -> tuple[str, str | None, int]:
    # 새 액션의 위치 → (소주제, subtopic_id, 소주제 안 위치)
    #   마지막 결정 항목 아래 (그 결정에 이어진 액션이 있으면 그 뒤) / 결정이 없으면 마지막 소주제의 끝 / 소주제가 없으면 기본 소주제
    alive = {it.item_id: it for it in agenda.major_discussions if it.status != "DELETED"}
    decision = next((it for it in reversed(agenda.major_discussions)
                     if it.item_id in alive and it.is_counted_decision), None)
    if decision is not None:
        subtopic = next(st for st in agenda.subtopics if decision.item_id in st.item_ids)
        ids = subtopic.item_ids
        position = ids.index(decision.item_id) + 1
        while position < len(ids) and (ids[position] not in alive or alive[ids[position]].is_action):
            position += 1
        return subtopic.title, subtopic.subtopic_id, position
    for subtopic in reversed(agenda.subtopics):
        if any(i in alive for i in subtopic.item_ids):
            return subtopic.title, subtopic.subtopic_id, len(subtopic.item_ids)
    return cfg.DEFAULT_SUBTOPIC, None, 0


def _create(summary: MeetingSummary, e) -> HitlEditRecord:
    agenda = (misc_agenda(summary, {}) if e.agenda_id == cfg.MISC_AGENDA_TITLE
              else next(a for a in summary.agendas if a.agenda_id == e.agenda_id))
    number = _next_number(agenda, "major_discussions")
    item_id = make_item_id(agenda.agenda_id, "major_discussions", number)
    history = [ItemRevision(stage=STAGE, field="item", before=None, after=item_id, reason="사용자 생성")]
    due, note = _due_value(e.due, item_id, history, summary)
    title, subtopic_id, position = _placement(agenda)
    item = DiscussionItem(item_id=item_id, agenda_id=agenda.agenda_id, order=number, text=e.text.strip(),
                          evidence_utterance_indices=[], history=history, subtopic_id=subtopic_id or "", subtopic=title,
                          discussion_tag=ACTION_TAG, decision_tag=ACTION_DECISION, due=due,
                          **_assignee_values(e.assignee_user_ids, summary))
    previous = None
    if subtopic_id:     # 소주제가 없으면 rebuild_subtopics가 만듦
        ids = next(st for st in agenda.subtopics if st.subtopic_id == subtopic_id).item_ids
        previous = ids[position - 1] if position else None
        ids.insert(position, item_id)
    index = next((i for i, it in enumerate(agenda.major_discussions) if it.item_id == previous),
                 len(agenda.major_discussions) - 1)
    agenda.major_discussions.insert(index + 1, item)
    for order, it in enumerate(agenda.major_discussions, 1):
        it.order = order
    return HitlEditRecord(op="create", item_id=item_id, agenda_id=agenda.agenda_id, changed_fields=["item"], note=note)


def _update(summary: MeetingSummary, e) -> HitlEditRecord:
    item = summary.find_item(e.item_id)
    fields, note, values = e.model_fields_set, None, {}
    if "text" in fields:
        values["text"] = e.text.strip()
    if "assignee_user_ids" in fields:
        values.update(_assignee_values(e.assignee_user_ids, summary))
    if "due" in fields:
        values["due"], note = _due_value(e.due, item.item_id, item.history, summary)
    changed = [f for f, v in values.items() if _set(item, f, v, item.history, STAGE, "사용자 수정")]
    if "assignee_user_ids" in fields:     # 사용자가 담당자를 확인함 (값이 같아도) → 담당자 대조 사유는 해소
        item.review_reasons = [r for r in item.review_reasons if not r.startswith(ASSIGNEE_REVIEW_PREFIX)]
        if item.status == "REVIEW_REQUIRED" and not item.review_reasons:
            _set(item, "status", "ACTIVE", item.history, STAGE, "사용자가 담당자 확인")
    return HitlEditRecord(op="update", item_id=item.item_id, agenda_id=item.agenda_id, changed_fields=changed,
                          evidence_before=list(item.evidence_utterance_indices),
                          evidence_status="LINKED" if changed else "NOT_NEEDED",
                          note=note or (None if changed else "기존 값과 같아 변경 없음"))


def _delete(summary: MeetingSummary, e) -> HitlEditRecord:
    item = summary.find_item(e.item_id)
    _set(item, "status", "DELETED", item.history, STAGE, "사용자 삭제")
    return HitlEditRecord(op="delete", item_id=item.item_id, agenda_id=item.agenda_id, changed_fields=["status"],
                          evidence_before=list(item.evidence_utterance_indices), evidence_status="NOT_NEEDED")


def apply_edits(summary: MeetingSummary, edits: HitlEdits) -> tuple[list[HitlEditRecord], list[str]]:
    # 삭제 → 변경 → 생성 순서 (삭제된 결정 아래에 새 액션이 붙지 않도록) / (기록, 근거 연결 대상 item_id)
    by_op = lambda op: [e for e in edits.edits if e.op == op]
    records = [_delete(summary, e) for e in by_op("delete")]
    records += [_update(summary, e) for e in by_op("update")]
    records += [_create(summary, e) for e in by_op("create")]
    for agenda in summary.agendas:
        rebuild_subtopics(agenda)
    refresh_derived(summary)
    return records, [r.item_id for r in records if r.evidence_status != "NOT_NEEDED"]


# ── 담당자 추천 단계 수정 (최종 JSON에 바로 반영 · LLM 없음) ──
ASSIGN_STAGE = "assign"
ASSIGN_REASON = "담당자 추천 단계 수정"


def validate_assignments(summary: MeetingSummary, changes: AssignChanges) -> None:
    errors, seen = [], set()
    for n, c in enumerate(changes.changes, 1):
        label = f"{n}번째 변경"
        item = summary.find_item(c.item_id)
        if item is None or item.status == "DELETED":
            errors.append(f"{label}: 회의록에 없거나 삭제된 항목 {c.item_id!r}")
        elif not (isinstance(item, DiscussionItem) and item.is_action):
            errors.append(f"{label}: 액션이 아닌 항목 {c.item_id!r}")
        if c.item_id in seen:
            errors.append(f"{label}: 같은 항목을 두 번 변경 {c.item_id!r}")
        seen.add(c.item_id)
        fields = c.model_fields_set
        if not fields & {"assignees", "due"}:
            errors.append(f"{label}: 변경할 값이 없음")
        if "assignees" in fields:
            if c.assignees is None:
                errors.append(f"{label}: 담당자는 배열로 씀 (담당자 없음은 빈 배열)")
            elif any(not p.user_id.strip() or not p.name.strip() for p in c.assignees):
                errors.append(f"{label}: 담당자의 user_id · 이름이 비어 있음")
    if errors:
        raise HitlEditError(errors)


def _assigned_people(people: list[AssignPerson], summary: MeetingSummary) -> dict:
    # 참석자는 회의 정보의 값, 아니면 보낸 이름 · 직무 (참석자 · 다른 담당자와 이름이 겹치면 표시 이름에 직무를 붙임)
    attendees = {p.user_id: p for p in summary.meeting_info.participants}
    chosen = list({p.user_id: p for p in reversed(people)}.values())[::-1]     # 같은 사람은 처음 것만
    outside = [p.name.strip() for p in chosen if p.user_id not in attendees]
    taken = {a.name for a in attendees.values()} | {a.display_name for a in attendees.values()}
    values = {"assignees": [], "assignee_user_ids": [], "assignee_names": [], "assignee_jobs": []}
    for p in chosen:
        if p.user_id in attendees:
            a = attendees[p.user_id]
            shown, name, job = a.display_name, a.name, a.job
        else:
            name, job = p.name.strip(), (p.job or "").strip() or None
            clash = name in taken or outside.count(name) > 1
            shown = f"{name}({job})" if clash and job else name
        for key, value in zip(values, (shown, p.user_id, name, job)):
            values[key].append(value)
    return values


def apply_assignments(summary: MeetingSummary, changes: AssignChanges) -> list[HitlEditRecord]:
    # 담당자 · 마감일만 바꿈 (근거는 그대로) → 변경 기록을 summary.hitl.edits에 덧붙임
    records = []
    for c in changes.changes:
        item, fields, values, note = summary.find_item(c.item_id), c.model_fields_set, {}, None
        if "assignees" in fields:
            values.update(_assigned_people(c.assignees, summary))
        if "due" in fields:
            raw = _empty_to_none(c.due)
            values["due"] = _normalize_due(raw, item.item_id, item.history, summary.postprocess_log, ASSIGN_STAGE)
            if raw is not None and values["due"] is None:
                note = f"날짜로 확인되지 않아 빈 일정으로 저장: {raw!r}"
        changed = [f for f, v in values.items() if _set(item, f, v, item.history, ASSIGN_STAGE, ASSIGN_REASON)]
        if "assignees" in fields:   # 사용자가 고른 담당자이므로 담당자 대조 사유는 해소
            item.review_reasons = [r for r in item.review_reasons if not r.startswith(ASSIGNEE_REVIEW_PREFIX)]
            if item.status == "REVIEW_REQUIRED" and not item.review_reasons:
                _set(item, "status", "ACTIVE", item.history, ASSIGN_STAGE, ASSIGN_REASON)
        if changed:
            records.append(HitlEditRecord(op="update", item_id=item.item_id, agenda_id=item.agenda_id,
                                          changed_fields=changed, evidence_status="NOT_NEEDED",
                                          note=ASSIGN_REASON + (f" · {note}" if note else "")))
    refresh_derived(summary)
    if records:
        if summary.hitl is None:
            summary.hitl = HitlResult(status="COMPLETED", applied_at="", max_rounds=cfg.HITL_MAX_VERIFY_ROUNDS)
        summary.hitl.status = "COMPLETED"
        summary.hitl.edits += records
    return records


# ── LLM 입력 (전사문과 같은 이름 사용: 동명이인은 A · B) ──
def _utterances(summary: MeetingSummary) -> list[dict]:
    names = llm_names(summary.meeting_info.participants)
    return [{**u.model_dump(), "llm_name": names.get(u.speaker_user_id)} for u in summary.transcript]


def _metadata(summary: MeetingSummary) -> dict[str, str]:
    info = summary.meeting_info
    values = {cfg.TITLE_KEYS[0]: info.title, cfg.PARTICIPANT_KEYS[0]: info.participants_raw, cfg.DATETIME_KEYS[0]: info.datetime_raw}
    return llm_metadata({k: v for k, v in values.items() if v}, info.participants)


def format_targets(summary: MeetingSummary, item_ids: list[str], label: str) -> str:
    # item_id | 안건: 제목 | assignees | due | 액션명 | {label} [발화 번호]
    names = llm_names(summary.meeting_info.participants)
    lines = []
    for item_id in item_ids:
        item, agenda = summary.find_item(item_id), summary.agenda_of(item_id)
        people = [names.get(u, shown) for u, shown in zip(item.assignee_user_ids, item.assignees)]
        evidence = ", ".join(map(str, item.evidence_utterance_indices)) or "-"
        lines.append(f"- {item_id} | 안건: {agenda.agenda_title} | assignees: {', '.join(people) or '[]'} | "
                     f"due: {item.due or 'null'} | {item.text} | {label} [{evidence}]")
    return "\n".join(lines)


# ── LLM 단계 ──────────────────────────────────────────
def link_evidence(summary: MeetingSummary, targets: list[str], round_no: int, issues: list[HitlEvidenceIssue],
                  call: StructuredCall) -> list[str]:
    mode = summary.source_document.speaker_mode
    llm = call(link_prompt.build_system_prompt(mode),
               link_prompt.build_user_prompt(_utterances(summary), _metadata(summary),
                                             format_targets(summary, targets, "이전 근거"), issues),
               LLMEvidenceLinks, step=f"hitl_link{round_no}", effort=cfg.HITL_EFFORT)
    return apply_links(summary, llm, targets, round_no)


def verify_evidence(summary: MeetingSummary, targets: list[str], round_no: int, call: StructuredCall) -> list[HitlEvidenceIssue]:
    mode = summary.source_document.speaker_mode
    llm = call(verify_prompt.build_system_prompt(mode),
               verify_prompt.build_user_prompt(_utterances(summary), _metadata(summary),
                                               format_targets(summary, targets, "근거")),
               LLMEvidenceVerification, step=f"hitl_verify{round_no}", effort=cfg.HITL_EFFORT)
    return to_evidence_issues(llm, targets, round_no)


# ── 근거 연결 · 검증 결과 반영 ─────────────────────────
def _set_evidence(item, indices: list[int], stage: str, reason: str) -> None:
    # 근거가 비어도 REVIEW_REQUIRED로 표시하지 않음 — 다른 사유(요약 검증 미통과 등)가 남아 있을 때만 그 상태를 유지
    _set(item, "evidence_utterance_indices", indices, item.history, stage, reason)
    item.review_reasons = [r for r in item.review_reasons if r != NO_EVIDENCE_REASON]
    item.status = "REVIEW_REQUIRED" if item.review_reasons else "ACTIVE"


def _refresh_evidence_range(summary: MeetingSummary) -> None:
    for agenda in summary.agendas:
        agenda.evidence_range = evidence_range(agenda)


def apply_links(summary: MeetingSummary, llm: LLMEvidenceLinks, targets: list[str], round_no: int) -> list[str]:
    # 대상 항목의 근거 발화만 바꿈 (대상이 아닌 item_id는 무시) / 안내 문구 반환
    stage = f"{STAGE}_link_{round_no}"
    valid = {u.utterance_index for u in summary.transcript}
    notes, linked = [], set()
    for link in llm.links:
        if link.item_id not in targets:
            notes.append(f"연결 대상이 아닌 item_id 무시: {link.item_id}")
            continue
        if invalid := sorted({i for i in link.evidence_utterance_indices if i not in valid}):
            notes.append(f"{link.item_id}: 전사문에 없는 근거 발화 번호 {invalid} 제외")
        _set_evidence(summary.find_item(link.item_id), sorted({i for i in link.evidence_utterance_indices if i in valid}),
                      stage, link.reason.strip())
        linked.add(link.item_id)
    for item_id in targets:
        if item_id not in linked:       # 기존 근거를 유지하고 검증에 맡김 (비어 있으면 '근거 없음'으로 처리됨)
            notes.append(f"모델 출력에 없는 항목: {item_id}")
            item = summary.find_item(item_id)
            _set_evidence(item, item.evidence_utterance_indices, stage, "연결 결과 없음")
    _refresh_evidence_range(summary)
    return notes


def to_evidence_issues(llm: LLMEvidenceVerification, targets: list[str], round_no: int) -> list[HitlEvidenceIssue]:
    return [HitlEvidenceIssue(issue_id=f"h{round_no}_i{n}", round=round_no, item_id=i.item_id, issue_type=i.issue_type,
                              description=i.description.strip(), utterance_indices=sorted(set(i.utterance_indices)))
            for n, i in enumerate((i for i in llm.issues if i.item_id in targets), 1)]


def clear_unverified(summary: MeetingSummary, issues: list[HitlEvidenceIssue], round_no: int) -> None:
    # 마지막 검증까지 문제가 남은 항목 → 근거를 비움 (액션은 유지, 비우기 전 근거는 항목 history에 남음)
    for issue in issues:
        _set_evidence(summary.find_item(issue.item_id), [], f"{STAGE}_clear_{round_no}",
                      f"{EVIDENCE_REJECTED_PREFIX}({issue.issue_type}): {issue.description}")
    _refresh_evidence_range(summary)


def finish_records(summary: MeetingSummary, records: list[HitlEditRecord], failed: set[str]) -> None:
    for r in records:
        if r.evidence_status == "NOT_NEEDED":
            continue
        r.evidence_after = list(summary.find_item(r.item_id).evidence_utterance_indices)
        r.evidence_status = "UNVERIFIED" if r.item_id in failed else "LINKED" if r.evidence_after else "NO_EVIDENCE"
