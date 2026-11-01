"""공유 받은 회의: 다른 부서가 보내 준 읽기 전용 회의록."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.layout import page, page_header
from ui.tags import tag
from utils.text import esc, md_escape


def render() -> None:
    with page("recv"):
        page_header("공유 받은 회의", desc_html="다른 부서 주관자가 보내 준 회의록이에요. 읽기만 할 수 있고 내 위키에는 들어가지 않아요. "
                                             "오른쪽 태그는 보낸 사람이 정한 <b>공유 범위</b>예요.")
        with st.container(key="recv-list", gap=None):
            for mid in repo.received_meeting_ids():
                m = repo.meeting(mid)
                with st.container(key=f"srow-recv-{mid}", horizontal=True, vertical_alignment="center", gap="small"):
                    st.html(avatar(sel.person(m.host).initial, size=34), width="content")
                    with st.container(gap=None):
                        button(md_escape(m.title), key=f"recv_open_{mid}", variant="link", on_click=actions.open_meeting, args=(mid,))
                        markup(f'<span class="row-sub">{esc(m.shared.sender)} · {esc(m.shared.received)} 받음</span>')
                    st.html(tag(sel.scope_label(m.shared.scope), sel.scope_tone(m.shared.scope)), width="content")
