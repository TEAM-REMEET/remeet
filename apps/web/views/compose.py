"""새 회의록 작성 (로그인 후 첫 화면).

다른 화면과 같은 너비·시작 위치를 쓰고, 위에서부터
  1) 기본 정보: 회의 제목* · 회의 날짜* · 주관자* · 참석자 · 회의 종류·폴더*
  2) 회의 내용: 직접 쓰거나 붙여 넣기 + 「파일 첨부」(txt · md · docx · hwpx)
     (주관자 · 참석자 · 회의 종류·폴더는 칸을 누르면 모달에서 골라요)
  3) 「회의록 생성하기」
글과 파일 중 하나만 있어도 됩니다. 입력칸 key 에 compose_nonce 를 붙여, 생성하고 나면 칸이 비워집니다.
"""

from __future__ import annotations

import streamlit as st

from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import page, page_header
from ui.tags import tag
from utils.text import esc

_FILE_TYPES = ["txt", "md", "docx", "hwpx"]
_NEW = "new"


def _person_label(pid: str) -> str:
    p = sel.person(pid)
    return f"{p.name} · {p.team}" + (" (나)" if pid == ME else "")


def _people_text(pids: list[str]) -> str:
    if not pids:
        return ""
    names = [sel.person(p).name for p in pids]
    return ", ".join(names[:3]) + (f" 외 {len(names) - 3}명" if len(names) > 3 else "") + f"  ({len(names)}명)"


def _folder_text(s: AppState) -> str:
    """'주간 회의 › 프로젝트별 › 11월 릴리즈 준비' · '월간 회의 › 월별 › 11월 (새 폴더)'"""
    t = repo.meeting_types()[s.nn_type].name
    if s.nn_group == "period":
        if not s.nn_period:
            return ""
        new = "" if arc.period(s, s.nn_type, s.nn_period) else " (새 폴더)"
        return f"{t} › {arc.PERIOD_LABEL[s.nn_type]} › {arc.period_label(s.nn_period)}{new}"
    topics = sel.folder_ids_of_type(s, s.nn_type)
    if s.nn_topic in topics:
        return f"{t} › 프로젝트별 › {sel.folders(s)[s.nn_topic].name}"
    return f"{t} › 프로젝트별 › {s.nn_name} (새 폴더)" if s.nn_name.strip() else ""


def _field(label: str, key: str, value: str, placeholder: str, icon_name: str, modal: str) -> None:
    """입력칸처럼 생긴 버튼: 누르면 선택 모달(폴더 · 주관자 · 참석자)이 떠요."""
    markup(f'<span class="cp-flabel">{esc(label)}</span>')
    button(value or placeholder, key=key, variant="field" if value else "fieldempty", icon=icon_name,
           on_click=actions.open_picker, args=(modal,), width="stretch")


def _content(n: int) -> None:
    markup('<h2 class="cp-section">회의 내용</h2>')
    # 우리가 만든 둥근 네모(cp-editor) 안에 테두리 없는 입력칸을 넣어 씀 (입력칸 자체는 모서리를 바꾸기 어려워서)
    with st.container(key="cp-editor", gap=None):
        st.text_area(
            "회의 내용",
            key=f"cp_text_{n}",
            height=400,
            label_visibility="collapsed",
            placeholder="회의 내용을 이곳에 붙여넣거나 직접 작성하세요.\n\n"
                        "회의에서 논의한 안건, 결정사항, 후속 업무 등을 자유롭게 입력할 수 있습니다.",
        )
    with st.container(key="cp-attach-row", horizontal=True, vertical_alignment="center", gap="small"):
        with st.container(key="cp-attach", width="content"):
            st.file_uploader("파일 첨부", type=_FILE_TYPES, accept_multiple_files=True, key=f"cp_files_{n}",
                             label_visibility="collapsed")

def _basic_info(s: AppState, n: int) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup('<h2 class="cp-section">기본 정보</h2>')
        markup('<span class="cp-required">* 필수 입력</span>')

    types = repo.meeting_types()
    folders = sel.folders(s)
    topics = sel.folder_ids_of_type(s, s.nn_type)
    topic = s.nn_topic if s.nn_topic in topics else _NEW

    with st.container(key="cp-grid", gap="small"):
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            st.text_input("회의 제목 *", key=f"cp_title_{n}", placeholder="예: 4분기 계획 회의")
        with c2:
            st.date_input("회의 날짜 *", value=None, key=f"cp_date_{n}", format="YYYY/MM/DD")
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            _field("주관자 *", "cp_host_field", _person_label(s.nn_host) if s.nn_host else "", "주관자 선택",
                   ":material/person:", "cp_host")
        with c2:
            _field("참석자", "cp_people_field", _people_text(s.nn_people), "참석자 선택",
                   ":material/group:", "cp_people")
        _field("회의 종류 · 폴더 *", "cp_folder_field", _folder_text(s), "회의 종류와 폴더 선택",
               ":material/folder:", "cp_folder")
    t = types[s.nn_type].name
    if s.nn_group == "period":
        if not s.nn_period:
            hint = "회의 종류와 폴더를 골라 주세요."
        elif arc.period(s, s.nn_type, s.nn_period):
            hint = f"{t} 「{arc.period_label(s.nn_period)}」 폴더에 쌓여요 · 참석자만 볼 수 있어요."
        else:
            hint = f"{t} 안에 「{arc.period_label(s.nn_period)}」 폴더가 새로 만들어져요."
    elif topic == _NEW:
        hint = (f"{t} 안에 「{s.nn_name}」 폴더가 만들어지고, 내가 폴더 주관자가 돼요." if s.nn_name.strip()
                else "회의 종류와 폴더를 골라 주세요.")
    else:
        hint = f"「{folders[topic].name}」 폴더에 쌓여요 · 참석자만 볼 수 있고, 빠진 멤버에게는 회의 상세에서 공유할 수 있어요."
    markup(f'<span class="cp-hint">{esc(hint)}</span>')


def render() -> None:
    s = get_state()
    n = s.compose_nonce
    with page("compose"):
        with st.container(horizontal=True, vertical_alignment="top"):
            page_header("새 회의록 작성", "회의 내용을 입력하거나 파일을 첨부해 주세요.")
            st.html(tag("새 회의", "att"), width="content")
        markup('<hr class="cp-divider">')
        _basic_info(s, n)  # 1) 기본 정보 (제목 · 날짜 · 주관자 · 참석자 · 회의 종류·폴더)
        markup('<hr class="cp-divider">')
        _content(n)  # 2) 회의 내용 + 파일 첨부
        if s.nn_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')
        with st.container(horizontal=True, horizontal_alignment="right"):
            button("회의록 생성하기", key="cp_submit", variant="primary", icon=":material/auto_awesome:",
                   on_click=actions.submit_compose, args=(n,))
