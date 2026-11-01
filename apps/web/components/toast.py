"""화면 위쪽 한 줄 알림 (닫기 가능)."""

from __future__ import annotations

import streamlit as st

from state import actions
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from utils.text import esc


def render_toast() -> None:
    text = get_state().toast
    if not text:
        return
    with st.container(key="toast", horizontal=True, vertical_alignment="center"):
        markup(f'<span role="status">{esc(text)}</span>')
        button("", key="toast_close", variant="ghost", icon=":material/close:", on_click=actions.close_toast,
               help="알림 닫기")
