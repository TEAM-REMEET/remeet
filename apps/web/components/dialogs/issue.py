"""홈 · 내 Jira 할 일 상세 모달.

Jira 이슈의 라벨 remeet-todo-{id} 로 RE:MEET 할 일 · 회의를 찾아 보여 줘요 (이미 받아 둔 회의 데이터에서 · 서버 호출 없음).
회의(제목 · 날짜 · 주관자) · 안건 · 담당자 전체 · 근거 발언 · Jira 상태 / 마감일 + 「회의록 보기」 · 「Jira 에서 열기」.
RE:MEET 에서 만든 일이 아니면(라벨 없음 · 볼 수 없는 회의) Jira 정보만.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions, export
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import notice
from ui.tags import tag
from utils.text import esc

MAX_EVIDENCE = 2  # 근거 발언은 앞에서 두 개만 (길어지지 않게)


def _find(todo_id: int | None):
    """할 일 id → (회의, 할 일) · 내가 볼 수 있는 회의에서만 찾음 (없으면 None)."""
    if not todo_id:
        return None
    return next(((m, t) for m in repo.meetings().values() for t in m.todos if t.todo_id == todo_id), None)


def _follows(issue: dict) -> None:
    """선행 업무 (이전 회의 업무 · Neo4j 후속 업무) · 내가 볼 수 있는 회의 것만.
    한 줄 = 「업무 (담당자)」 + 회의 제목 링크 (누르면 모달을 닫고 그 회의로)."""
    seen = repo.meetings()
    rows = [f for f in issue.get("follows") or [] if f.get("meeting_id") in seen]
    if not rows:
        return
    with st.container(key="dash-follows", gap=None):
        markup('<span class="dash-follow-label">선행 업무</span>')
        for n, f in enumerate(rows):
            owners = ", ".join(sel.name_of(u) for u in f.get("owners") or []) or "담당자 미지정"
            with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"dash-follow-{n}"):
                markup(f'<span class="dash-follow">{esc(f["text"])} <em>({esc(owners)})</em></span>')
                button(f["meeting"], key=f"issue_follow_{n}", variant="link", icon=":material/link:",
                       on_click=actions.open_issue_meeting, args=(f["meeting_id"],))


def _evidence(m, t) -> str:
    lines = {p.id: p for p in m.paragraphs}
    quotes = [lines[i] for i in t.src if i in lines][:MAX_EVIDENCE]
    return "".join(f'<blockquote class="dash-quote"><b>{esc(p.speaker)}</b> {esc(p.text[:200])}'
                   f'{"…" if len(p.text) > 200 else ""}</blockquote>' for p in quotes)


@st.dialog("할 일 상세", width="medium", on_dismiss=actions.close_modal)
def issue_detail_dialog() -> None:
    issue = st.session_state.get("_issue_detail") or {}
    markup(f'<p class="dash-detail-title"><b>{esc(issue.get("title") or "")}</b></p>'
           f'<p class="dash-detail-sub">{esc(issue.get("key") or "")} · {tag(issue.get("status") or "-", "info")}'
           f' · 마감 {esc(issue.get("due") or "없음")}</p>')
    found = _find(issue.get("todo_id"))
    if found:
        m, t = found
        d = sel.meeting_date(m)
        agenda = t.agenda or export._agenda_titles(get_state(), m.id).get(t.item_id, "")
        owners = ", ".join(sel.name_of(u) for u in t.owners) or "미지정"
        evidence = _evidence(m, t)
        markup('<dl class="ca-info dash-info">'
               f"<dt>회의</dt><dd>{esc(m.title)} · {d.year}.{d.month}.{d.day} · {esc(sel.name_of(m.host))} 주관</dd>"
               + (f"<dt>안건</dt><dd>{esc(agenda)}</dd>" if agenda else "")
               + f"<dt>담당자</dt><dd>{esc(owners)}</dd>"
               + (f"<dt>근거 발언</dt><dd>{evidence}</dd>" if evidence else "")
               + "</dl>")
        _follows(issue)
    else:
        notice("RE:MEET 회의에서 만든 일이 아니거나 볼 수 없는 회의라 Jira 정보만 보여 줘요.")
    with st.container(horizontal=True, gap="small"):
        if found:
            button("회의록 보기", key="issue_detail_meeting", variant="primary", icon=":material/description:",
                   on_click=actions.open_issue_meeting, args=(found[0].id,))
        if issue.get("url"):
            st.link_button("Jira 에서 열기 ↗", issue["url"])
