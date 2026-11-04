"""상단바(GNB): 현재 위치(경로) + 알림(팝오버) + 새 회의록 버튼."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from utils.text import esc, md_escape

_KIND_ICON = {
    "review": ":material/rate_review:",
    "shared": ":material/inbox:",
    "integration": ":material/power:",
    "member": ":material/group_add:",  # 폴더 멤버로 추가됨
}


def _crumbs() -> None:
    crumbs = sel.breadcrumbs(get_state())
    parts = []
    for i, c in enumerate(crumbs):
        if i:
            parts.append('<span class="sep">/</span>')
        parts.append(f'<span class="{"last" if i == len(crumbs) - 1 else ""}">{esc(c)}</span>')
    markup(f'<nav class="crumbs" aria-label="현재 위치">{"".join(parts)}</nav>')


def _notifications() -> None:
    s = get_state()
    unread = sum(1 for n in repo.notifications() if sel.is_unread(s, n.id))
    with st.container(key="bell-wrap", width="content", gap=None):
        if sel.show_bell_dot(s):
            markup('<span class="bell-dot" aria-label="새 알림"></span>')
        with st.popover("", icon=":material/notifications:", help="알림", key=f"bell_{s.screen}_{s.meeting_id}",
                        on_change=actions.open_bell):
            markup(f'<b style="font-size:15px">알림</b> <span class="hint">읽지 않음 {unread}개</span>')
            for n in repo.notifications():
                new = sel.is_unread(s, n.id)
                text = f"**{md_escape(n.text)}**" if new else md_escape(n.text)
                button(f"{text}  \n:gray[{n.when}]", key=f"noti_{n.id}", variant="noti" if new else "notiread",
                       icon=_KIND_ICON.get(n.kind), on_click=actions.open_notification, args=(n.id,), width="stretch")


def render_topbar() -> None:
    with st.container(key="topbar", horizontal=True, vertical_alignment="center", gap="small"):
        _crumbs()
        _notifications()
        button("새 회의록", key="topbar_new", variant="primary", icon=":material/add:", on_click=actions.open_new_note)
