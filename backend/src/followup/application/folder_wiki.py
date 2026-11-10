"""폴더 위키: 주제(Topic) × 회의 표 (결정 흐름)와 주제별 상태 (미해결 이슈 보드)를 같은 판정으로 만든다.

상태 = 그 주제의 가장 최근 회의에서 다룬 안건만 보고 정한다 (위에서부터 먼저 맞는 것).
    split  의견이 갈림   어떤 소주제에 「반대·우려」가 있고 그 소주제 안에 「결정」이 없음
    open   정해지지 않음 미해결 이슈 · 보류가 있거나 결정이 하나도 없음
    done   정리됨        나머지
정렬 = 「기타」는 맨 뒤 → 안 끝난 주제(split · open) 먼저 → 최근 논의 순.
"""

from __future__ import annotations

import re
from typing import Any

MISC_TITLE = "기타"  # adapters/neo4j/load.MISC_TITLE 과 같은 값
CONCERN, DECIDED, HOLD = "반대·우려", "결정", "보류"
_TAG_PREFIX = re.compile(r"^(\[[^\]]*\])+\s*")  # "[제안][결정] 문장" → "문장"


def discussion_lines(sub: dict[str, Any]) -> list[dict[str, Any]]:
    """Discussion 의 같은 번호 칸끼리 한 줄로 (적재가 중첩 객체를 못 넣어 배열로 나눠 둠)."""
    texts = sub.get("discussions") or []
    tags, dtags, evidence = (sub.get(k) or [] for k in ("discussion_tags", "decision_tags", "evidence"))
    pick = lambda arr, i: arr[i] if i < len(arr) else ""  # noqa: E731 — 길이가 어긋난 옛 데이터도 깨지지 않게
    return [{
        "tag": pick(tags, i),
        "decision_tag": pick(dtags, i),
        "text": _TAG_PREFIX.sub("", text),
        "src": [int(x) for x in str(pick(evidence, i)).split(",") if x.strip().isdigit()],
    } for i, text in enumerate(texts)]


def _round(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """한 회의에서 같은 주제에 붙은 안건(보통 1개)을 하나로 합친다."""
    subs = [discussion_lines(s) for r in rows for s in r.get("subtopics") or []]
    lines = [ln for sub in subs for ln in sub]
    replaced = {s["old"]: s["new"] for r in rows for s in r.get("superseded") or []}
    return {
        "meeting_id": rows[0]["meeting_id"],
        "decisions": [{"text": d, "superseded_by": replaced.get(d)} for r in rows for d in r.get("decisions") or []],
        "unresolved": [u for r in rows for u in r.get("unresolved") or []],
        "next_topics": [n for r in rows for n in r.get("next_topics") or []],
        "concerns": [ln["text"] for ln in lines if ln["tag"] == CONCERN],
        "holds": [ln["text"] for ln in lines if ln["decision_tag"] == HOLD],
        "split": any(any(ln["tag"] == CONCERN for ln in sub) and not any(ln["decision_tag"] == DECIDED for ln in sub)
                     for sub in subs),
        "lines": lines,
    }


def status(last: dict[str, Any]) -> str:
    if last["split"]:
        return "split"
    if last["unresolved"] or last["holds"] or not last["decisions"]:
        return "open"
    return "done"


def build(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: adapters/neo4j/folder_wiki.read_rows 결과 (회의 시간순)."""
    meetings: dict[str, dict] = {}
    grouped: dict[str, dict[str, list]] = {}  # topic_id → meeting_id → rows (시간순 유지)
    titles: dict[str, str] = {}
    for r in rows:
        meetings.setdefault(r["meeting_id"], {"meeting_id": r["meeting_id"], "title": r["meeting_title"] or "", "date": r["date"] or ""})
        grouped.setdefault(r["topic_id"], {}).setdefault(r["meeting_id"], []).append(r)
        titles[r["topic_id"]] = r["topic"] or ""

    topics = []
    for tid, by_meeting in grouped.items():
        rounds = [_round(rs) for rs in by_meeting.values()]
        state = status(rounds[-1])
        for rd in rounds:
            del rd["split"]  # 상태 계산에만 씀
        title = titles[tid]
        # Topic.last_discussed_at 은 지운 회의 날짜일 수 있어서 남은 마지막 회의 날짜로 정렬
        topics.append({"topic_id": tid, "title": title, "last_at": meetings[rounds[-1]["meeting_id"]]["date"],
                       "misc": title == MISC_TITLE, "status": state, "rounds": rounds})
    topics.sort(key=lambda t: t["last_at"], reverse=True)
    topics.sort(key=lambda t: (t["misc"], t["status"] == "done"))  # 안정 정렬: 같은 그룹 안은 최근 순 유지
    return {"meetings": list(meetings.values()), "topics": topics}
