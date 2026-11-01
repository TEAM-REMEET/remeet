"""새 회의록 올리기."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from utils.text import esc

SAMPLE_FILES = {"weekly": "주간회의_1102.txt", "monthly": "월간회의_10월.docx", "normal": "회의메모_1031.md"}


def _on_upload() -> None:
    f = st.session_state.get("nn_uploader")
    actions.update(nn_file=f.name if f else "", nn_error=False)


def _hint(topic: str) -> str:
    s = get_state()
    if topic == "new":
        name = s.nn_name.strip()
        if name:
            return f"「{name}」 폴더가 {repo.meeting_types()[s.nn_type].name} 안에 자동으로 만들어지고, 내가 폴더 주관자가 돼요."
        return "큰 주제를 적으면 그 이름으로 폴더가 자동으로 만들어져요."
    return f"「{sel.folders(s)[topic].name}」 멤버 {len(sel.members_of(s, topic))}명만 이 회의록을 볼 수 있어요."


@st.dialog("새 회의록 올리기", width="medium", on_dismiss=actions.close_modal)
def new_note_dialog() -> None:
    s = get_state()
    types = repo.meeting_types()
    topics = sel.folder_ids_of_type(s, s.nn_type)
    topic = s.nn_topic if s.nn_topic in topics else "new"

    markup('<span style="font-size:13px;font-weight:600">회의 종류</span>')
    button_group([(k, types[k].name) for k in sel.TYPE_ORDER], s.nn_type, actions.set_new_note_type,
                 key="nn_type", style="seg", gap=None)

    markup('<span style="font-size:13px;font-weight:600">큰 주제 (폴더)</span>')
    folders = sel.folders(s)
    button_group([(k, folders[k].name) for k in topics] + [("new", "\\+ 새 주제")], topic,
                 lambda v: actions.update(nn_topic=v), key="nn_topic", style="chip")
    if topic == "new":
        st.text_input("새 주제 이름", key="nn_name_input", value=s.nn_name, placeholder="이번 회의의 큰 주제 (예: 정산 자동화)",
                      label_visibility="collapsed", on_change=lambda: actions.update(nn_name=st.session_state["nn_name_input"]))
    markup(f'<span style="font-size:12px;color:#2B2F36">{esc(_hint(topic))}</span>')

    markup('<span style="font-size:13px;font-weight:600">회의록 파일</span>')
    if s.nn_file:
        with st.container(horizontal=True, vertical_alignment="center", key="nn-file"):
            markup(f'<div style="display:flex;align-items:center;gap:12px;border:1px solid #E3E6EA;background:#F8F9FA;'
                   f'border-radius:4px;padding:12px 14px">{icon("file", 18, "#F37321")}'
                   f'<span style="display:flex;flex-direction:column;line-height:1.4"><b style="font-weight:600">{esc(s.nn_file)}</b>'
                   '<span style="font-size:12px;color:#5F646D">업로드 준비 완료</span></span></div>')
            button("빼기", key="nn_remove", variant="ghost", icon=":material/close:", on_click=actions.update,
                   kwargs={"nn_file": ""})
    else:
        st.file_uploader("파일을 선택하거나 여기로 끌어 놓으세요", type=["txt", "docx", "md", "pdf"], key="nn_uploader",
                         on_change=_on_upload, help="txt · docx · md · pdf · 최대 20MB")
        button(f"샘플 파일로 채우기 ({SAMPLE_FILES[s.nn_type]})", key="nn_sample", variant="back",
               on_click=actions.update, kwargs={"nn_file": SAMPLE_FILES[s.nn_type], "nn_error": False})
    if s.nn_error:
        markup('<span role="alert" style="font-size:13px;color:#B3261E">회의록 파일을 먼저 올려 주세요.</span>')

    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="nn_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("올리고 정리하기", key="nn_save", variant="primary"):
            if actions.save_new_note():
                st.rerun()
            else:
                st.rerun(scope="fragment")
