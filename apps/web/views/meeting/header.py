"""회의 상세 머리말: 경로 되돌아가기, 제목·역할, 참석자, 편집/공유/확정 버튼."""

from __future__ import annotations

import streamlit as st

from components.member_pills import member_pills
from data import repository as repo
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
                button("회의 삭제", key="md_delete", variant="dangerline", icon=":material/delete:",
                       on_click=actions.open_delete, args=("meeting", m.id))
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
    _export_notices(s, m)


def _export_notices(s: AppState, m: Meeting) -> None:
    """외부 도구(Slack · Jira 등)로 보내다 실패한 회의록에서만.

    - 단순 이슈(일시적인 네트워크 오류 등): 사용자가 「다시 보내기」로 직접 해결
    - 기술 이슈(API · 인증 문제): 안내 문구만 (「관리자에게 문의」 모달은 결과 전달 화면에서 전송이 끝날 때 뜸)
    """
    for f in repo.export_failures(m.id):
        tool = repo.tool(f.tool)
        n = len(m.todos)
        if not sel.failure_open(s, f.id):
            notice(f"액션 아이템 {n}건을 {esc(tool.name)}(으)로 다시 보냈어요. ({esc(tool.target)})", "success")
            continue
        with st.container(key=f"export-{f.id}", horizontal=True, vertical_alignment="center", gap="small"):
            if f.kind == "simple":
                notice(f"{esc(f.reason)} 액션 아이템 {n}건을 {esc(tool.name)}(으)로 보내지 못했어요. "
                       + ("다시 보내 보세요." if model.is_host(m) else "주관자가 다시 보낼 수 있어요."), "danger")
                if model.is_host(m):
                    button("다시 보내기", key=f"export_resend_{f.id}", variant="secondary", icon=":material/refresh:",
                           on_click=actions.resend_export, args=(f.id,))
            else:
                notice(f"{esc(tool.name)} 연동 문제로 액션 아이템 {n}건을 보내지 못했어요. "
                       "연동 설정은 관리자만 고칠 수 있어요.", "danger")