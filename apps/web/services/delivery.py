"""외부 도구 전달 (PRD 3.6).

- 확정한 결과만, 사람이 승인한 뒤에만 보냅니다.
- 대상마다 같은 확정 버전은 한 번만 보냅니다(멱등 키).
- 실패한 대상만 다시 보낼 수 있습니다.
"""
from __future__ import annotations

import json

from services.rules import member_name
from utils.dates import add_days, fmt_d, stamp, to_iso
from utils.text import sid, srange

ALL_ITEMS = {"summary": True, "decisions": True, "actions": True, "issues": True, "agenda": True}
ITEM_LABEL = {"summary": "회의 개요 · 안건별 논의", "decisions": "결정사항", "actions": "액션 아이템 (승인된 항목)", "issues": "미해결 이슈", "agenda": "다음 회의 안건"}
TARGET_ORDER = ["slack", "email", "calendar", "jira", "notion", "teams", "confluence"]


def approved_actions(detail: dict) -> list[dict]:
    return [a for a in detail["actions"] if a["review"] == "approved"]


def next_meeting_date(meeting: dict) -> str:
    return to_iso(add_days(meeting["date"], 7))


def idempotency_key(meeting: dict, target: str) -> str:
    return f"{meeting['id']}:{target}:v{meeting.get('version') or 1}"


def build_payload(meeting: dict, members: list[dict], items: dict | None = None) -> dict:
    d, it = meeting["detail"], items or ALL_ITEMS
    nm = lambda mid: member_name(members, mid, None)  # noqa: E731
    p = {
        "meeting_id": meeting["id"], "version": meeting.get("version") or 1, "confirmed_at": meeting.get("confirmed_at"), "confirmed_by": meeting.get("confirmed_by"),
        "meeting": {"title": meeting["title"], "date": meeting["date"], "time": f"{meeting['start']}-{meeting['end']}", "organizer": nm(meeting["organizer"]), "attendees": [nm(x) for x in meeting["attendees"]]},
    }
    if it.get("summary"):
        p["summary"] = {"purpose": d["purpose"], "overview": d["core"], "topics": [{"title": t["title"], "discussion": t["note"], "evidence": [t["from"], t["to"]]} for t in d["topics"]]}
    if it.get("decisions"):
        p["decisions"] = [{"text": x["text"], "evidence": x["refs"]} for x in d["decisions"]]
    if it.get("actions"):
        p["action_items"] = [{"title": a["title"], "assignee": nm(a["assignee"]), "assignee_source": a["source"], "due": a["due"], "due_raw": a["due_raw"], "priority": a["priority"], "evidence": a["refs"]} for a in approved_actions(d)]
    if it.get("issues"):
        p["open_issues"] = [{"title": x["title"], "detail": x["text"], "evidence": x["refs"]} for x in d["issues"]]
    if it.get("agenda"):
        p["next_agenda"] = [x["text"] for x in d["agenda"]]
    return p


def payload_json(meeting: dict, members: list[dict], target: str, items: dict | None = None) -> str:
    return json.dumps({"target": target, "idempotency_key": idempotency_key(meeting, target), "payload": build_payload(meeting, members, items)}, ensure_ascii=False, indent=2)


def build_markdown(meeting: dict, members: list[dict], items: dict | None = None, include_drafts: bool = False) -> str:
    """전체 미리보기·이메일·Notion·Confluence에 쓰는 공통 문서 (Markdown)."""
    d, it = meeting["detail"], items or ALL_ITEMS
    nm = lambda mid: member_name(members, mid, "미정")  # noqa: E731
    acts = [a for a in d["actions"] if a["review"] == "approved" or (include_drafts and a["review"] == "draft")]
    out = [f"## {meeting['title']}", f"{fmt_d(meeting['date'], True)} {meeting['start']}–{meeting['end']} · 주관 {nm(meeting['organizer'])}  ",
           "참석 " + ", ".join(nm(x) for x in meeting["attendees"]), ""]
    if it.get("summary"):
        out += ["#### 회의 개요", d["core"], "", f"목적: {d['purpose']}", "", "#### 안건별 논의"]
        out += [f"{i + 1}. **{t['title']}** — {t['note']}" for i, t in enumerate(d["topics"])] + [""]
    if it.get("decisions"):
        out += ["#### 결정사항"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["decisions"])] + [""]
    if it.get("actions"):
        out += ["#### 액션 아이템", "| 업무 | 담당자 | 기한 | 우선순위 |", "|---|---|---|---|"]
        for a in acts:
            mark = " `검토 전`" if a["review"] == "draft" else ""
            out.append(f"| {a['title']}{mark} | {nm(a['assignee'])} | {fmt_d(a['due']) or '미정'} | {a['priority']} |")
        out.append("")
    if it.get("issues"):
        out += ["#### 미해결 이슈"] + [f"- **{x['title']}** — {x['text']}" for x in d["issues"]] + [""]
    if it.get("agenda"):
        out += ["#### 다음 회의 안건"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["agenda"])]
    return "\n".join(out)


def build_chat_text(meeting: dict, members: list[dict], items: dict | None = None) -> str:
    """Slack·Teams 메시지 (표 없이 목록, mrkdwn)."""
    d, it = meeting["detail"], items or ALL_ITEMS
    nm = lambda mid: member_name(members, mid, "미정")  # noqa: E731
    out = [f"*[회의 결과] {meeting['title']}*", f"{fmt_d(meeting['date'], True)} {meeting['start']}–{meeting['end']} · 주관 {nm(meeting['organizer'])} · 참석 {len(meeting['attendees'])}명", ""]
    if it.get("summary"):
        out += ["*회의 개요*", d["core"], "", "*안건별 논의*"] + [f"{i + 1}. {t['title']} — {t['note']}" for i, t in enumerate(d["topics"])] + [""]
    if it.get("decisions"):
        out += ["*결정사항*"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["decisions"])] + [""]
    if it.get("actions"):
        out += ["*액션 아이템*"] + [f"• {a['title']} — @{nm(a['assignee'])} · {fmt_d(a['due']) or '기한 미정'} · {a['priority']}" for a in approved_actions(d)] + [""]
    if it.get("issues"):
        out += ["*미해결 이슈*"] + [f"• {x['title']} — {x['text']}" for x in d["issues"]] + [""]
    if it.get("agenda"):
        out += ["*다음 회의 안건*"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["agenda"])]
    return "\n".join(out).strip()


def build_plain_text(meeting: dict, members: list[dict], items: dict | None = None) -> str:
    """캘린더 일정 설명란용 일반 텍스트."""
    d, it = meeting["detail"], items or ALL_ITEMS
    nm = lambda mid: member_name(members, mid, "미정")  # noqa: E731
    out = []
    if it.get("summary"):
        out += ["[회의 개요]", d["core"], "", "[안건별 논의]"] + [f"{i + 1}. {t['title']} - {t['note']}" for i, t in enumerate(d["topics"])] + [""]
    if it.get("decisions"):
        out += ["[결정사항]"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["decisions"])] + [""]
    if it.get("actions"):
        out += ["[액션 아이템]"] + [f"- {a['title']} / {nm(a['assignee'])} / {fmt_d(a['due']) or '기한 미정'} / {a['priority']}" for a in approved_actions(d)] + [""]
    if it.get("issues"):
        out += ["[미해결 이슈]"] + [f"- {x['title']}: {x['text']}" for x in d["issues"]] + [""]
    if it.get("agenda"):
        out += ["[다음 회의 안건]"] + [f"{i + 1}. {x['text']}" for i, x in enumerate(d["agenda"])]
    return "\n".join(out).strip()


def jira_issues(meeting: dict, members: list[dict], project: str) -> list[dict]:
    d = meeting["detail"]
    issues = []
    for i, a in enumerate(approved_actions(d)):
        evidence = next((s["text"] for s in d["segments"] if a["refs"] and s["id"] == a["refs"][0]), None)
        issues.append({
            "key": f"{project}-{231 + i}", "summary": a["title"], "assignee": member_name(members, a["assignee"], "없음"),
            "due": fmt_d(a["due"], True) or "없음", "priority": a["priority"],
            "description": f"회의: {meeting['title']} ({fmt_d(meeting['date'])})" + (f"\n근거 {srange(a['refs'][0], a['refs'][-1])}: “{evidence}”" if evidence else ""),
        })
    return issues


def result_for(target: str, meeting: dict, options: dict) -> str:
    acts = approved_actions(meeting["detail"])
    return {
        "slack": f"{options.get('channel', '#온보딩-tf')} 채널에 게시 · 메시지 링크 저장",
        "email": f"{len(meeting['attendees']) + len(options.get('extra', []))}명에게 발송 · Message-ID 저장",
        "calendar": f"다음 회의 일정 등록 · {fmt_d(next_meeting_date(meeting))} {meeting['start']}",
        "jira": f"이슈 {len(acts)}건 생성 · {options.get('project', 'ONB')}-231~{230 + len(acts)}",
        "notion": "회의록 페이지 생성", "teams": "팀 채널에 게시", "confluence": "회의록 페이지 생성",
    }[target]


def finalize_status(meeting: dict) -> None:
    ds = meeting["deliveries"]
    if ds and all(x["status"] == "SENT" for x in ds):
        meeting["status"] = "SYNCED"
    elif any(x["status"] == "FAILED" for x in ds):
        meeting["status"] = "SYNC_PARTIAL"


def send(meeting: dict, targets: list[str], integrations: dict, options: dict) -> None:
    """선택한 대상으로 전송합니다(데모: 연결 상태로 성공·실패 결정)."""
    keep = [x for x in meeting["deliveries"] if x["target"] not in targets]
    for t in targets:
        ok = integrations[t]["status"] == "connected"
        keep.append({
            "target": t, "status": "SENT" if ok else "FAILED", "at": stamp(), "key": idempotency_key(meeting, t),
            "result": result_for(t, meeting, options) if ok else None,
            "error": None if ok else f"{integrations[t]['name']} 연결이 끊겨 있어요. 다시 연결한 뒤 재시도해 주세요.",
        })
    meeting["deliveries"] = keep
    finalize_status(meeting)


def already_sent(meeting: dict, target: str) -> bool:
    return any(x["target"] == target and x["status"] == "SENT" for x in meeting["deliveries"])


def evidence_label(refs: list[int]) -> str:
    if not refs:
        return "직접 추가"
    return srange(refs[0], refs[-1]) if len(refs) > 1 else sid(refs[0])
