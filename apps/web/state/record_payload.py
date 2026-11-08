"""화면의 Meeting → 회의록 저장 API(POST /api/drafts) 본문.

data/repository.py 의 _meeting(d) 의 반대 방향이에요 (DB 에 넣었다가 GET /api/meetings/{id} 로 다시 읽으면 같은 Meeting).
"""

from __future__ import annotations

from datetime import date

from data.models import Agenda, Evidence, Meeting, Todo


def _ev(items: tuple[Evidence, ...]) -> list[dict]:
    return [{"text": e.text, "src": list(e.src)} for e in items if e.text.strip()]


def _agenda(a: Agenda) -> dict:
    out = {"title": a.title, "status": a.status, "tone": a.tone, "deep": a.deep, "src": list(a.src),
           "points": _ev(a.points), "detailed": a.detailed}
    if a.detailed:
        out |= {
            "background": _ev(a.background),
            "topics": [{"title": t.title, "points": [{"kind": p.kind, "text": p.text, "src": list(p.src)}
                                                     for p in t.points if p.text.strip()]} for t in a.topics],
            "decisions": _ev(a.decisions),
            "open_issues": _ev(a.open_issues),
            "next": _ev(a.next_candidates),
        }
    return out


def content(m: Meeting) -> dict:
    """요약 내용 (summary · discussion · decisions · open_issues · results · agendas)."""
    return {
        "summary": {"text": m.summary.text, "src": list(m.summary.src)},
        "discussion": [{"label": d.label, "text": d.text, "src": list(d.src)} for d in m.discussion or () if d.text.strip()],
        "decisions": _ev(m.decisions),
        "open_issues": _ev(m.open_issues),
        "results": [{"kind": r.kind, "text": r.text, "agendas": list(r.agendas), "src": list(r.src)}
                    for r in m.results if r.text.strip()],
        "agendas": [_agenda(a) for a in m.agendas if a.title.strip()],
    }


def todo(t: Todo) -> dict:
    return {"text": t.text, "owners": list(t.owners), "due": t.due, "due_date": (t.due_iso or "")[:10] or None,
            "src": list(t.src), "urgent": t.urgent, "item_id": t.item_id or None, "agenda": t.agenda}


def draft_payload(s, m: Meeting, gone: list[int] | None = None, summary_json: dict | None = None) -> dict:
    """작성 화면 값(s.nn_*) + 미리보기 Meeting → POST /api/drafts 본문. gone = 미리보기에서 삭제한 할 일 번호."""
    day: date | None = s.nn_date
    skip = set(gone or ())
    return {
        "meeting_id": m.id,
        "content": content(m),
        "title": s.nn_title.strip() or m.title,
        "directory_id": m.directory_id,
        "project_id": m.project_id,
        "host_id": s.nn_host or None,
        "held_at": f"{day.isoformat()}T00:00:00+09:00" if day else None,
        "time_label": m.time,
        "file_name": m.file,
        "attendees": list(dict.fromkeys([s.nn_host, *s.nn_people])) if s.nn_host else list(s.nn_people),
        "transcript": [{"line_id": p.id, "speaker": p.speaker, "text": p.text} for p in m.paragraphs if p.text.strip()],
        "todos": [todo(t) for i, t in enumerate(m.todos) if i not in skip and t.text.strip()],
        "summary_json": summary_json,
    }
