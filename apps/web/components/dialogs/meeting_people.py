"""회의 참석자 목록."""

from __future__ import annotations

import streamlit as st

from components.member_pills import ordered_host_first, people_list
from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup


@st.dialog("참석자", width="small", on_dismiss=actions.close_modal)
def people_dialog() -> None:
    m = repo.meeting(get_state().meeting_id)
    markup(f'<h3 style="font-size:16px;font-weight:700">이 회의 참석자 {len(m.people)}명</h3>'
           '<p class="hint" style="font-size:13px;color:#5F646D">누가 볼 수 있는지는 폴더 멤버로 정해져요. 여기서는 실제로 참석한 사람만 표시해요.</p>')
    rows = []
    for pid in ordered_host_first(m.host, list(m.people)):
        p = sel.person(pid)
        host = pid == m.host
        rows.append({"letter": p.initial, "name": p.name + (" (나)" if pid == ME else ""), "team": p.team,
                     "role": "주관" if host else "참석", "tone": "host" if host else "att", "host": host})
    markup(people_list(rows))
    with st.container(horizontal=True, horizontal_alignment="right"):
        if button("닫기", key="ppl_close", variant="dark"):
            actions.close_modal()
            st.rerun()
