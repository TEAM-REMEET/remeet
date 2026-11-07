from __future__ import annotations

import streamlit as st

from data import repository as repo
from state.store import get_state


def render_data_errors() -> None:
    """백엔드에서 못 불러온 데이터 안내. 이유가 같으면(예: 백엔드 꺼짐) 한 줄로 묶어요."""
    by_reason: dict[str, list[str]] = {}
    for what, why in repo.data_errors().items():
        by_reason.setdefault(why, []).append(what)
    for why, whats in by_reason.items():
        st.warning(f"{' · '.join(whats)} 불러오기 실패 — {why}", icon=":material/cloud_off:")


def render_toast() -> None:
    s = get_state()
    if s.toast:
        st.toast(s.toast, duration="long")
        s.toast = ""
