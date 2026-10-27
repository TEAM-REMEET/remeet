"""회의 결과 검색 (결정사항·액션 아이템·미해결 이슈·논의)."""
from __future__ import annotations

from data.constants import WORK_LABEL
from services.rules import member_name

TYPE_LABEL = {"decision": "결정사항", "action": "액션 아이템", "issue": "미해결 이슈", "topic": "논의"}


def build_index(meetings: list[dict], members: list[dict]) -> list[dict]:
    rows = []
    for m in meetings:
        d = m.get("detail")
        if not d:
            continue
        for x in d["decisions"]:
            rows.append({"type": "decision", "text": x["text"], "sub": "", "meeting": m, "refs": x["refs"]})
        for a in d["actions"]:
            if a["review"] == "rejected":
                continue
            sub = member_name(members, a["assignee"])
            if m["status"] in ("SYNCED", "SYNC_PARTIAL"):
                sub += " · " + WORK_LABEL[a["work"]]
            rows.append({"type": "action", "text": a["title"], "sub": sub, "meeting": m, "refs": a["refs"]})
        for x in d["issues"]:
            rows.append({"type": "issue", "text": x["title"], "sub": x["text"], "meeting": m, "refs": x["refs"]})
        for t in d["topics"]:
            rows.append({"type": "topic", "text": t["title"], "sub": t["note"], "meeting": m, "refs": [t["from"]]})
    return rows


def search(index: list[dict], query: str, kind: str = "all") -> list[dict]:
    q = query.strip()
    if not q:
        return []
    hits = [r for r in index if q in r["text"] or q in r["sub"]]
    if kind != "all":
        hits = [r for r in hits if r["type"] == kind]
    return sorted(hits, key=lambda r: r["meeting"]["date"], reverse=True)


def meeting_flow(hits: list[dict]) -> list[dict]:
    seen = {}
    for h in hits:
        seen[h["meeting"]["id"]] = h["meeting"]
    return sorted(seen.values(), key=lambda m: m["date"])
