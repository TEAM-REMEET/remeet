"""회의 상세 머리말: 경로 되돌아가기, 제목·역할, 참석자, 편집/공유/확정 버튼."""

from __future__ import annotations

import streamlit as st

from components.member_pills import member_pills
from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState
from ui.buttons import button
from ui.html import markup
from ui.layout import notice
from ui.tags import tag
from utils.text import esc
from views.meeting import model


def render_header(s: AppState, m: Meeting) -> None:
    host = model.is_host(m)
    editing = s.editing and host
    role = sel.my_role(m)
    role_label, role_tone = ("내가 주관", "host") if role == "host" else sel.ROLE_TAGS[role]

    # 1행: (왼쪽) 뒤로가기 · 제목  /  (오른쪽) 편집·공유·확정 또는 읽기 전용 안내
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(gap="small"):
            markup(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
                   f'<h1 class="section-title" style="font-size:20px">{esc(m.title)}</h1>{tag(role_label, role_tone)}</div>')
        with st.container(horizontal=True, width="content", gap="small"):
            if host and not editing:
                button("편집", key="md_edit", variant="secondary", icon=":material/edit:", on_click=actions.start_edit)
                button("회의 공유하기", key="md_share", variant="primary", icon=":material/send:",
                       on_click=actions.open_share)
            elif host:
                button("취소", key="md_cancel", variant="secondary", on_click=actions.stop_edit)
                button("확정하기", key="md_confirm", variant="primary", on_click=actions.start_delivery, args=(m.id, 1))
            else:
                st.html(f'<span class="read-note">{esc(model.read_note(m))}</span>', width="content")

    # 2행: (왼쪽) 일시 · 참석자 알약  /  (오른쪽, 위 버튼과 같은 오른쪽 끝) 참석자 보기
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        button("참석자 보기", key="open_people", variant="secondary", icon=":material/group:",
               on_click=actions.open_modal, args=("people",))

    if editing:
        notice("편집 중이에요. 요약·안건을 고친 뒤 <b>확정하기</b>를 누르면 참석자에게 결과가 전달돼요.")
    if s.just_shared:
        notice(f"{esc(s.last_share_to)}에게 공유했어요. 「공유한 회의」에서 확인할 수 있어요.", "success")
