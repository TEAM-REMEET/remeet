"""홈: 내 회의 목록."""

from __future__ import annotations

import streamlit as st

from components.meeting_table import meeting_table
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page

_EMPTY = {
    "host": "내가 주관한 회의가 아직 없어요.",
    "att": "참석한 회의가 아직 없어요.",
    "all": "아직 회의록이 없어요.",
}


def _upload_cta() -> None:
    with st.container(key="upload-cta"):
        markup(
            '<div class="upload-cta">'
            f'<span class="ic">{icon("upload", 22, "#F37321", 2)}</span>'
            '<span class="tx"><b>새 회의록 올리기</b><span>회의록 파일을 올리면 안건·결정·할 일로 정리해 드려요</span></span>'
            f'{icon("chevron-right", 20, "#9CA1A9", 2)}</div>'
        )
        button("새 회의록 올리기", key="home_upload", variant="secondary", on_click=actions.open_new_note, width="stretch")


def render() -> None:
    s = get_state()
    ids = sel.home_meeting_ids(s)
    host_n = sum(1 for i in ids if sel.my_role(repo.meeting(i)) == "host")
    shown = [i for i in ids if s.home_role == "all" or sel.my_role(repo.meeting(i)) == s.home_role]

    with page("home"):
        markup(f'<h1 class="section-title" style="font-size:20px">{sel.me().short}님, 오늘은 어떤 회의였나요?</h1>')
        _upload_cta()

        with st.container(horizontal=True, vertical_alignment="center"):
            markup('<h2 class="section-title" id="mine">내 회의록</h2>')
            button_group(
                [("all", f"전체 {len(ids)}"), ("host", f"내가 주관 {host_n}"), ("att", f"참석 {len(ids) - host_n}")],
                s.home_role,
                lambda v: actions.update(home_role=v),
                key="home_role",
            )
        if shown:
            meeting_table([sel.meeting_row(s, i) for i in shown], key="home")
        else:
            empty_state(_EMPTY[s.home_role])
