"""404: 없는 주소로 들어왔을 때.

Streamlit 은 모르는 주소면 자체 「Page not found」 창을 띄우고 첫 화면을 보여 줘요.
app.py 가 처음 들어온 주소를 확인해서 없는 주소면 이 화면을 대신 보여 주고, 그 창은 숨깁니다.
"""

from __future__ import annotations

import streamlit as st

from state import actions
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import page
from utils.text import esc


def render() -> None:
    s = get_state()
    with page("notfound", max_width=640):
        markup(
            '<div class="nf">'
            '<span class="nf-code">404</span>'
            "<h1>페이지를 찾을 수 없어요</h1>"
            "<p>주소가 바뀌었거나 삭제된 페이지예요. 주소를 다시 확인하거나 아래에서 이동해 주세요.</p>"
            f'<code class="nf-path">{esc(s.not_found)}</code>'
            "</div>"
        )
        with st.container(key="nf-actions", horizontal=True, horizontal_alignment="center", gap="small"):

            button("새 회의록", key="nf_compose", variant="primary", icon=":material/edit_square:",
                   on_click=actions.open_new_note)
