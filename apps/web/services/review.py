"""검토·확정 단계의 데이터 변경 (HITL).

규칙
- 외부 도구로 한 곳이라도 전송되기 전까지는 회의 주관자가 수정할 수 있습니다.
- 확정 후 수정하면 회의가 다시 '검토 대기'가 되고, 다시 확정하면 버전이 올라갑니다.
- 승인한 액션을 수정하면 그 액션은 다시 '검토 전'이 됩니다.
- 모든 변경은 변경 전·후 값과 함께 이력에 남깁니다.
"""
from __future__ import annotations

from data.constants import ACCESS_LABEL, REVIEW_LABEL, SOURCE_LABEL, WORK_LABEL
from services.rules import member_name
from utils.dates import fmt_d, now_time, stamp
from utils.text import cut, new_id

REOPEN = {"f": "검토", "b": "승인", "a": "검토 전 (내용이 바뀌어 다시 검토)"}


# ---------------------------------------------------------------- 상태 판단

def is_sent(meeting: dict) -> bool:
    return any(d["status"] == "SENT" for d in meeting.get("deliveries", []))


def can_edit(meeting: dict, member_id: str) -> bool:
    return meeting["organizer"] == member_id and not is_sent(meeting) and meeting["status"] in ("REVIEW_PENDING", "CONFIRMED")


def is_tracking(meeting: dict) -> bool:
    """외부로 전달된 뒤에는 업무 진행 상태를 관리합니다."""
    return is_sent(meeting) or meeting["status"] in ("SYNCED", "SYNC_PARTIAL")


def action_no(detail: dict, action_id: str) -> int:
    return next(i for i, a in enumerate(detail["actions"]) if a["id"] == action_id) + 1


def find_action(detail: dict, action_id: str) -> dict:
    return next(a for a in detail["actions"] if a["id"] == action_id)


def topic_no(detail: dict, topic_id: str) -> int:
    return next((i for i, t in enumerate(detail["topics"]) if t["id"] == topic_id), 0) + 1


def counts(detail: dict) -> dict:
    acts = detail["actions"]
    return {
        "draft": [a for a in acts if a["review"] == "draft"],
        "approved": [a for a in acts if a["review"] == "approved"],
        "rejected": [a for a in acts if a["review"] == "rejected"],
    }


# ---------------------------------------------------------------- 공통 기록

def _log(meeting: dict, who: str, text: str, changes: list[dict] | None = None) -> None:
    meeting["detail"]["history"].insert(0, {"t": now_time(), "who": who, "text": text, "changes": changes or []})
    # 확정 후 · 전송 전 수정 → 다시 확정 필요
    if meeting["status"] == "CONFIRMED" and not is_sent(meeting):
        meeting["status"] = "REVIEW_PENDING"
        meeting["reconfirm"] = True


# ---------------------------------------------------------------- 액션 아이템

def set_review(meeting: dict, action_id: str, value: str, who: str) -> None:
    a = find_action(meeting["detail"], action_id)
    before = a["review"]
    a["review"] = value
    _log(meeting, who, f"액션 {action_no(meeting['detail'], action_id)} 검토", [{"f": "검토", "b": REVIEW_LABEL[before], "a": REVIEW_LABEL[value]}])


def approve_all_drafts(meeting: dict, who: str) -> int:
    drafts = [a for a in meeting["detail"]["actions"] if a["review"] == "draft"]
    for a in drafts:
        a["review"] = "approved"
    _log(meeting, who, f"검토 전 {len(drafts)}건 일괄 승인")
    return len(drafts)


def assign(meeting: dict, action_id: str, member_id: str, source: str, members: list[dict], who: str) -> bool:
    a = find_action(meeting["detail"], action_id)
    reopen = a["review"] == "approved"
    before = member_name(members, a["assignee"]) + (f" ({SOURCE_LABEL[a['source']]})" if a["source"] else "")
    changes = [{"f": "담당자", "b": before, "a": f"{member_name(members, member_id)} ({SOURCE_LABEL[source]})"}]
    if reopen:
        changes.append(REOPEN)
        a["review"] = "draft"
    a.update(assignee=member_id, source=source, keep=False)
    _log(meeting, who, f"액션 {action_no(meeting['detail'], action_id)} 담당자 변경", changes)
    return reopen


def keep_unassigned(meeting: dict, action_id: str, who: str) -> None:
    find_action(meeting["detail"], action_id)["keep"] = True
    _log(meeting, who, f"액션 {action_no(meeting['detail'], action_id)} 담당자 미지정으로 유지")


def edit_action(meeting: dict, action_id: str, form: dict, who: str) -> tuple[bool, bool]:
    """(바뀐 내용 있음, 다시 검토 전으로 돌아감)"""
    d = meeting["detail"]
    a = find_action(d, action_id)
    ch = []
    if form["title"].strip() != a["title"]:
        ch.append({"f": "업무 내용", "b": a["title"], "a": form["title"].strip()})
    if (form["due"] or None) != a["due"]:
        ch.append({"f": "기한", "b": fmt_d(a["due"]) or "미정", "a": fmt_d(form["due"]) or "미정"})
    if form["priority"] != a["priority"]:
        ch.append({"f": "우선순위", "b": a["priority"], "a": form["priority"]})
    if form["topic"] != a["topic"]:
        ch.append({"f": "관련 안건", "b": f"안건 {topic_no(d, a['topic'])}", "a": f"안건 {topic_no(d, form['topic'])}"})
    if not ch:
        return False, False
    reopen = a["review"] == "approved"
    if reopen:
        ch.append(REOPEN)
        a["review"] = "draft"
    a.update(title=form["title"].strip(), due=form["due"] or None, priority=form["priority"], topic=form["topic"])
    if any(c["f"] in ("업무 내용", "기한") for c in ch):
        a["edited"] = True
    _log(meeting, who, f"액션 {action_no(d, action_id)} 수정", ch)
    return True, reopen


def add_action(meeting: dict, form: dict, who: str) -> None:
    meeting["detail"]["actions"].append({
        "id": new_id("a"), "title": form["title"].strip(), "topic": form["topic"], "assignee": form.get("assignee") or None,
        "source": "manual" if form.get("assignee") else None, "due": form["due"] or None, "due_raw": None,
        "priority": form["priority"], "ai_priority": form["priority"], "refs": [], "review": "approved", "estimated": False,
        "need_skills": [], "need_dept": "", "keep": False, "edited": False, "manual": True, "work": "todo", "remind": True, "ext": None,
    })
    _log(meeting, who, "액션 직접 추가: " + form["title"].strip())


def set_work(meeting: dict, action_id: str, value: str, who: str) -> None:
    a = find_action(meeting["detail"], action_id)
    before = a["work"]
    a["work"] = value
    meeting["detail"]["history"].insert(0, {"t": now_time(), "who": who, "text": f"액션 {action_no(meeting['detail'], action_id)} 진행 상태",
                                            "changes": [{"f": "진행 상태", "b": WORK_LABEL[before], "a": WORK_LABEL[value]}]})


def toggle_remind(meeting: dict, action_id: str) -> bool:
    a = find_action(meeting["detail"], action_id)
    a["remind"] = not a["remind"]
    return a["remind"]


# ---------------------------------------------------------------- 요약 · 이슈 · 안건

def save_summary(meeting: dict, core: str, notes: list[str], decisions: list[str], who: str) -> bool:
    d = meeting["detail"]
    ch = []
    if core.strip() and core.strip() != d["core"]:
        ch.append({"f": "핵심 요약", "b": cut(d["core"]), "a": cut(core.strip())})
        d["core"], d["core_edited"] = core.strip(), True
    for i, t in enumerate(d["topics"]):
        if notes[i].strip() and notes[i].strip() != t["note"]:
            ch.append({"f": f"안건 {i + 1} 논의", "b": cut(t["note"]), "a": cut(notes[i].strip())})
            t["note"], t["edited"] = notes[i].strip(), True
    for i, x in enumerate(d["decisions"]):
        if decisions[i].strip() and decisions[i].strip() != x["text"]:
            ch.append({"f": f"결정 {i + 1}", "b": cut(x["text"]), "a": cut(decisions[i].strip())})
            x["text"], x["edited"] = decisions[i].strip(), True
    if ch:
        _log(meeting, who, "요약 수정", ch)
    return bool(ch)


def issue_to_agenda(meeting: dict, issue_id: str, who: str) -> None:
    d = meeting["detail"]
    issue = next(x for x in d["issues"] if x["id"] == issue_id)
    issue["to_next"] = True
    d["agenda"].append({"id": new_id("n"), "text": issue["title"], "issue": issue_id})
    _log(meeting, who, "미해결 이슈를 다음 회의 안건에 추가", [{"f": "다음 회의 안건", "b": "-", "a": issue["title"]}])


def save_agenda(meeting: dict, texts: list[str], who: str) -> None:
    d = meeting["detail"]
    before = [x["text"] for x in d["agenda"]]
    keep = [t.strip() for t in texts if t.strip()]
    old = {x["text"]: x for x in d["agenda"]}
    d["agenda"] = [{"id": old[t]["id"] if t in old else new_id("n"), "text": t, "issue": old[t].get("issue") if t in old else None} for t in keep]
    issues_kept = {x["issue"] for x in d["agenda"] if x.get("issue")}
    for issue in d["issues"]:
        issue["to_next"] = issue["id"] in issues_kept
    if before != keep:
        _log(meeting, who, "다음 회의 안건 편집", [{"f": "안건 수", "b": f"{len(before)}건", "a": f"{len(keep)}건"}])


def set_access(meeting: dict, value: str, who: str) -> None:
    before = meeting["access"]
    meeting["access"] = value
    meeting["detail"]["history"].insert(0, {"t": now_time(), "who": who, "text": "공개 범위 변경", "changes": [{"f": "공개 범위", "b": ACCESS_LABEL[before], "a": ACCESS_LABEL[value]}]})


# ---------------------------------------------------------------- 확정

def confirm(meeting: dict, members: list[dict], who: str) -> dict:
    """확정하고 버전을 올립니다. 구성원 업무 이력은 첫 확정 때만 반영합니다."""
    first = not meeting.get("version")
    meeting["version"] = (meeting.get("version") or 0) + 1
    meeting.update(status="CONFIRMED", reconfirm=False, confirmed_at=stamp(), confirmed_by=who)
    meeting["detail"]["history"].insert(0, {"t": now_time(), "who": who, "text": f"결과 확정 (버전 {meeting['version']})", "changes": []})
    approved = [a for a in meeting["detail"]["actions"] if a["review"] == "approved"]
    per: dict[str, int] = {}
    for a in approved:
        if a["assignee"]:
            per[a["assignee"]] = per.get(a["assignee"], 0) + 1
    if first:
        for m in members:
            if per.get(m["id"]):
                m["load"] += per[m["id"]]
                m["history"] = [a["title"] for a in approved if a["assignee"] == m["id"]] + m["history"]
    return {"version": meeting["version"], "per": per}
