"""회의 상세 페이지: 머리말 + (왼쪽) 원문 패널 + (오른쪽) 인사이트 탭."""

from __future__ import annotations

import streamlit as st

from state.store import get_state
from ui.layout import empty_state, page
from views.meeting import model
from views.meeting.header import render_header
from views.meeting.insight_tabs import render_insights
from views.meeting.raw_panel import render_raw_panel


def render() -> None:
    s = get_state()
    m = model.current(s)
    with page("note"):
        if m.id in s.deleted_meetings:  # 뒤로가기 등으로 삭제한 회의에 다시 온 경우
            empty_state("주관자가 삭제한 회의예요. 회의록과 정리된 내용을 더 볼 수 없어요.", title="삭제된 회의예요", pad=48)
            return
        render_header(s, m)
        with st.container(key="md-split"):
            left, right = st.columns([1, 1.15], gap=None)
            with left, st.container(key="md-left", gap="small"):
                render_raw_panel(s, m)
            with right, st.container(key="md-right", gap=None):
                render_insights(s, m)
