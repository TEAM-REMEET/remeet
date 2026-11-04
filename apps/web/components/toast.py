from __future__ import annotations

import streamlit as st

from state.store import get_state


def render_toast() -> None:
    s = get_state()
    if s.toast:
        st.toast(s.toast, duration="long")
        s.toast = ""
