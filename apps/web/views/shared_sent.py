"""공유한 회의: 내가 다른 부서에 보낸 기록."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import empty_state, page, page_header
from ui.tags import tag
from utils.text import esc, md_escape


def render() -> None:
    s = get_state()
    with page("sent"):
        page_header("공유한 회의", "내가 주관한 회의를 다른 부서에 보낸 기록이에요. 받는 쪽은 읽기만 할 수 있어요.")
        c1, c2 = st.columns(2, gap="small")
        with c1:
            markup(f'<div class="scope-help">{tag("요약·결정만", "shared")}<span>핵심 요약·결정·액션 아이템만 보여요. 받는 쪽에서 <b>원문은 잠겨</b> 있어요.</span></div>')
        with c2:
            markup(f'<div class="scope-help">{tag("원문 포함", "host")}<span>정리된 내용과 함께 <b>회의록 원문 전체</b>를 볼 수 있어요.</span></div>')

        if not s.sent_shares:
            empty_state("아직 공유한 회의가 없어요. 내가 주관한 회의의 상세 화면에서 「공유」를 누르세요.", pad=40)
            return
        with st.container(key="share-list", gap=None):
            for i, item in enumerate(s.sent_shares):
                m = repo.meeting(item.meeting_id)
                with st.container(key=f"srow-sent-{i}", horizontal=True, vertical_alignment="center"):
                    with st.container(gap=None):
                        button(md_escape(m.title), key=f"sent_open_{i}", variant="link", on_click=actions.open_meeting, args=(m.id,))
                        markup(f'<span class="row-sub">{esc(sel.folder_label(s, m))} · {esc(item.date)} 공유 → {esc(item.to)}</span>')
                    st.html(tag(sel.scope_label(item.scope), sel.scope_tone(item.scope)), width="content")
                    button("공유 취소", key=f"revoke_{i}", variant="secondary", on_click=actions.revoke_share, args=(i,))
