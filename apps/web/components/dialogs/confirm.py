"""회의록 확정 확인 창: 「확정하기」를 한 번 더 확인한 뒤 결과 전달로."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from utils.text import esc


@st.dialog("회의록 확정", width="small", on_dismiss=actions.close_modal)
def confirm_meeting_dialog() -> None:
    s = get_state()
    m = repo.meeting(s.meeting_id)
    if sel.my_role(m) != "host":
        markup('<p class="hint" style="font-size:13px">회의록은 주관자만 확정할 수 있어요.</p>')
        return
    n_people = len([p for p in m.people if p != m.host])
    n_todo = len(sel.todo_items(s, m.id))
    unassigned = sel.unassigned_count(s, m.id)
    markup(f'<div class="assign-target"><span class="assign-target-label">확정할 회의록</span><h3>{esc(m.title)}</h3></div>'
           '<p class="confirm-lead">확정하면 아래 내용을 참석자에게 보내고 위키에 반영해요. '
           "확정한 뒤에는 요약·담당자를 고칠 수 없어요.</p>"
           f'<ul class="confirm-list"><li>참석자 <b>{n_people}명</b>에게 요약 · 결정 · 할 일 전송</li>'
           f'<li>액션 아이템 <b>{n_todo}건</b> 담당자에게 배정</li>'
           '<li>Jira · Slack 같은 외부 도구 전송은 결과 전달이 끝난 뒤 따로 진행해요</li></ul>'
           + (f'<p class="confirm-warn">담당자가 정해지지 않은 액션 아이템이 <b>{unassigned}건</b> 있어요.</p>'
              if unassigned else ""))
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="confirm_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("확정하기", key="confirm_ok", variant="primary", icon=":material/task_alt:"):
            actions.confirm_meeting(m.id)
            st.rerun()
