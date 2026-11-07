"""백엔드 요약 결과(MeetingSummary JSON) → 회의 상세 화면의 Meeting 내용."""

from __future__ import annotations

from dataclasses import replace

from data.models import NO_SPEAKER, Agenda, AgendaTopic, Evidence, Meeting, MeetingResult, Paragraph, TaggedPoint, Todo


_ASSIGNEE_CHECK = "담당자 확인 필요"  # review_reasons 앞부분: AI가 적은 담당자를 참석자와 연결하지 못함 (backend postprocess.ASSIGNEE_REVIEW_PREFIX)
_VERIFY_FAILED = "검증 미통과"  # review_reasons 앞부분: 재수정 후에도 검증 오류가 남은 문장 (backend revision.REVIEW_PREFIX)


def is_shown(item: dict) -> bool:
    """사용자에게 보여 줄 문장: 삭제됐거나 검증 오류가 남은 문장은 빼고, 근거 · 담당자 확인만 필요한 문장은 보여 줘요."""
    return item["status"] != "DELETED" and not any(r.startswith(_VERIFY_FAILED) for r in item.get("review_reasons") or [])


def _src(indices: list[int]) -> tuple[str, ...]:
    return tuple(f"p{i}" for i in indices)


def _active(items: list[dict]) -> list[dict]:
    return [it for it in items if is_shown(it)]


def _evidence(items: list[dict]) -> tuple[Evidence, ...]:
    return tuple(Evidence(it["text"], _src(it["evidence_utterance_indices"])) for it in items)


def _is_decision(item: dict) -> bool:
    return item["decision_tag"] == "결정" and item["discussion_tag"] != "액션"


def _due(due: str | None) -> str:
    """2026-11-06 → 11/6 · 2026-11-06 14:00 → 11/6 14:00 (화면의 마감일 표기)"""
    if not due:
        return ""
    day, _, clock = due.partition(" ")
    parts = day.split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return due
    return f"{int(parts[1])}/{int(parts[2])}" + (f" {clock}" if clock else "")


def _topics(agenda: dict) -> tuple[AgendaTopic, ...]:
    items = {it["item_id"]: it for it in _active(agenda["major_discussions"])}
    topics = []
    for sub in agenda["subtopics"]:
        points = [items[i] for i in sub["item_ids"] if i in items]
        if points:
            topics.append(AgendaTopic(sub["title"], tuple(
                TaggedPoint(p["discussion_tag"], p["text"], _src(p["evidence_utterance_indices"])) for p in points)))
    return tuple(topics)


def _agenda(agenda: dict) -> Agenda:
    discussions = _active(agenda["major_discussions"])
    items = [*_active(agenda["discussion_background"]), *discussions, *_active(agenda["unresolved_issues"]),
             *_active(agenda["next_meeting_topics"])]
    evidence = sorted({i for it in items for i in it["evidence_utterance_indices"]})
    return Agenda(
        title=agenda["agenda_title"], status="논의됨", tone="info", deep=False, src=_src(evidence), points=(),
        background=_evidence(_active(agenda["discussion_background"])),
        topics=_topics(agenda),
        decisions=_evidence([d for d in discussions if _is_decision(d)]),
        open_issues=_evidence(_active(agenda["unresolved_issues"])),
        next_candidates=_evidence(_active(agenda["next_meeting_topics"])),
        detailed=True, agenda_id=agenda["agenda_id"],
    )


def apply_summary(meeting: Meeting, summary: dict) -> Meeting:
    """미리보기 회의의 원문 · 안건 · 회의 결과 · 액션 아이템을 백엔드 결과로 바꿈 (제목 · 날짜 · 참석자 등은 그대로)."""
    shown = [a for a in summary["agendas"] if any(
        _active(a[name]) for name in ("discussion_background", "major_discussions", "unresolved_issues", "next_meeting_topics"))]
    agendas = tuple(_agenda(a) for a in shown)
    results: list[MeetingResult] = []
    todos: list[Todo] = []
    for number, (raw, view) in enumerate(zip(shown, agendas), 1):
        results += [MeetingResult("결정", e.text, (number,), e.src) for e in view.decisions]
        results += [MeetingResult("미해결", e.text, (number,), e.src) for e in view.open_issues]
        todos += [Todo(d["text"], tuple(u for u in d["assignee_user_ids"] if u), _due(d["due"]),
                       _src(d["evidence_utterance_indices"]), item_id=d["item_id"], due_iso=d["due"] or "",
                       assignee_check=any(r.startswith(_ASSIGNEE_CHECK) for r in d.get("review_reasons") or []))
                  for d in _active(raw["major_discussions"]) if d["discussion_tag"] == "액션"]
    return replace(
        meeting,
        paragraphs=tuple(Paragraph(f'p{u["utterance_index"]}', u["speaker_display"] or NO_SPEAKER, u["text"],
                                   u.get("speaker_user_id") or "")
                         for u in summary["transcript"]),
        summary=Evidence("", ()),
        discussion=None,
        keywords=(),
        decisions=tuple(e for a in agendas for e in a.decisions),
        open_issues=tuple(e for a in agendas for e in a.open_issues),
        agendas=agendas,
        todos=tuple(todos),
        results=tuple(results),
    )
