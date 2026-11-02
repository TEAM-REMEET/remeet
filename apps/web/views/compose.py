# """새 회의록 작성 (로그인 후 첫 화면).

# 다른 화면과 같은 너비·시작 위치를 쓰고, 위에서부터
#   1) 회의 내용: 직접 쓰거나 붙여 넣기 + 「파일 첨부」(txt · md · docx · hwpx)
#   2) 기본 정보: 회의 제목* · 회의 날짜* · 주관자* · 참석자 · 회의 종류* · 폴더*
#   3) 「회의록 생성하기」
# 글과 파일 중 하나만 있어도 됩니다. 입력칸 key 에 compose_nonce 를 붙여, 생성하고 나면 칸이 비워집니다.
# """

# from __future__ import annotations

# import streamlit as st

# from config import CURRENT_USER_ID as ME
# from data import repository as repo
# from state import actions
# from state import selectors as sel
# from state.store import AppState, get_state
# from ui.buttons import button
# from ui.html import markup
# from ui.layout import page, page_header
# from ui.tags import tag
# from utils.text import esc

# _FILE_TYPES = ["txt", "md", "docx", "hwpx"]
# _NEW = "new"


# def _people_ids() -> list[str]:
#     ids = list(repo.people())
#     return [ME] + [p for p in ids if p != ME]


# def _person_label(pid: str) -> str:
#     p = sel.person(pid)
#     return f"{p.name} · {p.team}" + (" (나)" if pid == ME else "")


# def _content(n: int) -> None:
#     markup('<span class="cp-label">회의 내용</span>')
#     st.text_area(
#         "회의 내용",
#         key=f"cp_text_{n}",
#         height=220,
#         label_visibility="collapsed",
#         placeholder="회의 내용을 이곳에 붙여넣거나 직접 작성하세요.\n\n"
#                     "회의에서 논의한 안건, 결정사항, 후속 업무 등을 자유롭게 입력할 수 있습니다.",
#     )
#     with st.container(key="cp-attach-row", horizontal=True, vertical_alignment="top", gap="small"):
#         with st.container(key="cp-attach", width="content"):
#             st.file_uploader("파일 첨부", type=_FILE_TYPES, accept_multiple_files=True, key=f"cp_files_{n}",
#                              label_visibility="collapsed")
#         markup('<span class="cp-types">TXT · MD · DOCX · HWPX</span>')
#         markup('<span class="cp-either">텍스트 또는 파일 입력</span>')


# def _basic_info(s: AppState, n: int) -> None:
#     with st.container(horizontal=True, vertical_alignment="bottom"):
#         markup('<h2 class="cp-section">기본 정보</h2>')
#         markup('<span class="cp-required">* 필수 입력</span>')

#     types = repo.meeting_types()
#     folders = sel.folders(s)
#     topics = sel.folder_ids_of_type(s, s.nn_type)
#     topic = s.nn_topic if s.nn_topic in topics else _NEW
#     people = _people_ids()

#     with st.container(key="cp-grid", gap="small"):
#         c1, c2 = st.columns(2, gap="medium")
#         with c1:
#             st.text_input("회의 제목 *", key=f"cp_title_{n}", placeholder="예: 4분기 계획 회의")
#         with c2:
#             st.date_input("회의 날짜 *", value=None, key=f"cp_date_{n}", format="YYYY/MM/DD")
#         c1, c2 = st.columns(2, gap="medium")
#         with c1:
#             st.selectbox("주관자 *", people, index=0, key=f"cp_host_{n}", format_func=_person_label,
#                          placeholder="주관자 선택")
#         with c2:
#             st.multiselect("참석자", people, key=f"cp_people_{n}", format_func=_person_label,
#                            placeholder="참석자 검색 및 추가")
#         c1, c2 = st.columns(2, gap="medium")
#         with c1:
#             st.selectbox("회의 종류 *", list(sel.TYPE_ORDER), index=list(sel.TYPE_ORDER).index(s.nn_type),
#                          key=f"cp_type_{n}", format_func=lambda k: types[k].name,
#                          on_change=lambda: actions.set_new_note_type(st.session_state[f"cp_type_{n}"]))
#         with c2:
#             options = topics + [_NEW]
#             st.selectbox("폴더 *", options, index=options.index(topic), key=f"cp_topic_{n}_{s.nn_type}",
#                          format_func=lambda k: "+ 새 폴더 만들기" if k == _NEW else folders[k].name,
#                          on_change=lambda: actions.update(nn_topic=st.session_state[f"cp_topic_{n}_{s.nn_type}"]))
#         if topic == _NEW:
#             c1, c2 = st.columns(2, gap="medium")
#             with c2:
#                 st.text_input("새 폴더 이름 *", key=f"cp_name_{n}", value=s.nn_name,
#                               placeholder="이번 회의의 큰 주제 (예: 정산 자동화)",
#                               on_change=lambda: actions.update(nn_name=st.session_state[f"cp_name_{n}"]))
#     if topic == _NEW:
#         hint = "적은 이름으로 폴더가 만들어지고, 내가 폴더 주관자가 돼요."
#     else:
#         hint = f"「{folders[topic].name}」 폴더에 쌓여요 · 참석자만 볼 수 있고, 빠진 멤버에게는 회의 상세에서 공유할 수 있어요."
#     markup(f'<span class="cp-hint">{esc(hint)}</span>')


# def render() -> None:
#     s = get_state()
#     n = s.compose_nonce
#     with page("compose"):
#         with st.container(horizontal=True, vertical_alignment="top"):
#             page_header("새 회의록 작성", "회의 내용을 입력하거나 파일을 첨부해 주세요.")
#             st.html(tag("새 회의", "att"), width="content")
#         markup('<hr class="cp-divider">')
#         _content(n)
#         markup('<hr class="cp-divider">')
#         _basic_info(s, n)
#         if s.nn_error:
#             markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')
#         with st.container(horizontal=True, horizontal_alignment="right"):
#             button("회의록 생성하기", key="cp_submit", variant="primary", icon=":material/auto_awesome:",
#                    on_click=actions.submit_compose, args=(n,))


"""새 회의록 작성 (로그인 후 첫 화면).

다른 화면과 같은 너비·시작 위치를 쓰고, 위에서부터
  1) 회의 내용: 직접 쓰거나 붙여 넣기 + 「파일 첨부」(txt · md · docx · hwpx)
  2) 기본 정보: 회의 제목* · 회의 날짜* · 주관자* · 참석자 · 회의 종류* · 폴더*
     (주관자 · 참석자 · 폴더는 칸을 누르면 모달에서 팀 → 사람 / 폴더를 골라요)
  3) 「회의록 생성하기」
글과 파일 중 하나만 있어도 됩니다. 입력칸 key 에 compose_nonce 를 붙여, 생성하고 나면 칸이 비워집니다.
"""

from __future__ import annotations

import streamlit as st

from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
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


def _field(label: str, key: str, value: str, placeholder: str, icon_name: str, modal: str) -> None:
    """입력칸처럼 생긴 버튼: 누르면 선택 모달(폴더 · 주관자 · 참석자)이 떠요."""
    markup(f'<span class="cp-flabel">{esc(label)}</span>')
    button(value or placeholder, key=key, variant="field" if value else "fieldempty", icon=icon_name,
           on_click=actions.open_picker, args=(modal,), width="stretch")


def _content(n: int) -> None:
    markup('<span class="cp-label">회의 내용</span>')
    st.text_area(
        "회의 내용",
        key=f"cp_text_{n}",
        height=220,
        label_visibility="collapsed",
        placeholder="회의 내용을 이곳에 붙여넣거나 직접 작성하세요.\n\n"
                    "회의에서 논의한 안건, 결정사항, 후속 업무 등을 자유롭게 입력할 수 있습니다.",
    )
    with st.container(key="cp-attach-row", horizontal=True, vertical_alignment="top", gap="small"):
        with st.container(key="cp-attach", width="content"):
            st.file_uploader("파일 첨부", type=_FILE_TYPES, accept_multiple_files=True, key=f"cp_files_{n}",
                             label_visibility="collapsed")
        markup('<span class="cp-types">TXT · MD · DOCX · HWPX</span>')
        markup('<span class="cp-either">텍스트 또는 파일 입력</span>')


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
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            st.selectbox("회의 종류 *", list(sel.TYPE_ORDER), index=list(sel.TYPE_ORDER).index(s.nn_type),
                         key=f"cp_type_{n}", format_func=lambda k: types[k].name,
                         on_change=lambda: actions.set_new_note_type(st.session_state[f"cp_type_{n}"]))
        with c2:
            if topic == _NEW:
                value = f"+ 새 폴더 · {s.nn_name}" if s.nn_name.strip() else ""
            else:
                value = folders[topic].name
            _field("폴더 *", "cp_folder_field", value, "폴더 선택", ":material/folder:", "cp_folder")
    if topic == _NEW:
        hint = "적은 이름으로 폴더가 만들어지고, 내가 폴더 주관자가 돼요." if s.nn_name.strip() else "폴더를 골라 주세요."
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
        _content(n)
        markup('<hr class="cp-divider">')
        _basic_info(s, n)
        if s.nn_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')
        with st.container(horizontal=True, horizontal_alignment="right"):
            button("회의록 생성하기", key="cp_submit", variant="primary", icon=":material/auto_awesome:",
                   on_click=actions.submit_compose, args=(n,))
