"""회의록 화면.

  회의록        → 월간 · 주간 · 일반 회의 카드 (+ 검색)
  회의 종류      → 그 종류의 회의 목록 (전체 / 내 할 일이 있는 회의 / 내가 주관 / 참석)

프로젝트 ─ 회의 구조이고, 회의마다 종류(월간 · 주간 · 일반)가 있어요. 프로젝트 화면은 views/folder.py
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page, page_header
from components.meeting_table import meeting_table
from utils.browser import ime_live
from utils.text import esc, safe_key

_TYPE_ORDER = ("monthly", "weekly", "normal")  # 화면 버튼 순서: 월간 · 주간 · 일반

# ---------------------------------------------------------------- 공통
def _option_card(key: str, *, icon_name: str, title: str, meta: str, last: str, on_click, args=()) -> None:
    """큰 선택 버튼 (카드 전체가 버튼)."""
    with st.container(key=f"acard-{key}"):
        markup(
            '<div class="acard">'
            f'<span class="ic">{icon(icon_name, 20, "#F37321", 1.9)}</span>'
            f'<span class="tx"><b>{esc(title)}</b><span class="meta">{esc(meta)}</span>'
            + (f'<span class="last">{esc(last)}</span>' if last else "")
            + f'</span>{icon("chevron-right", 18, "#9CA1A9", 2)}</div>'
        )
        button(title, key=f"open_{key}", variant="secondary", on_click=on_click, args=args, width="stretch")


def _latest(ids: list[str] | tuple[str, ...]) -> str:
    if not ids:
        return "아직 회의록이 없어요"
    m = repo.meeting(ids[0])
    return f"최근 {m.date} · {m.title}"


def _grid(items: list, draw, cols: int = 3) -> None:
    with st.container(key="archive-grid", gap="small"):
        for start in range(0, len(items), cols):
            columns = st.columns(cols, gap="small")
            for col, item in zip(columns, items[start:start + cols]):
                with col:
                    draw(item)


# ---------------------------------------------------------------- 1. 회의록
def _projects_root(s: AppState) -> None:
    """사이드바 「프로젝트」 탭의 회의록: 프로젝트 폴더 카드 (정기 회의 탭의 월간 · 주간 · 일반 대신)."""
    fs = sel.folders(s)
    fids = sel.all_my_folder_ids(s)
    page_header("회의록", f"프로젝트 {len(fids)}개 · 프로젝트를 고르면 그 프로젝트의 회의록을 볼 수 있어요")

    def draw(fid: str) -> None:
        notes = sel.folder_note_ids(s, fid)  # 나에게 보이는 회의만 (최근 순) · 사이드바 트리와 같은 기준
        _option_card(f"proj-{safe_key(fid)}", icon_name="folder", title=fs[fid].name, meta=f"회의록 {len(notes)}개",
                     last=_latest(notes), on_click=actions.open_folder, args=(fid,))

    if fids:
        _grid(fids, draw)
    else:
        empty_state("아직 참여한 프로젝트가 없어요.", pad=40)
    markup(f'<span class="lock-note">{icon("lock", 12, "#80858E", 2)}내가 멤버인 프로젝트만 보여요</span>')


def _root(s: AppState) -> None:
    if s.tree_mode == "project":
        _projects_root(s)
        return
    page_header("회의록", "회의 종류를 고르면 그 종류의 회의를 최근 순으로 볼 수 있어요")
    types = repo.meeting_types()

    def draw(t: str) -> None:
        ids = arc.meeting_ids_of_type(s, t)
        _option_card(f"type-{t}", icon_name="folder", title=types[t].name,
                     meta=f"회의록 {len(ids)}개",
                     last=_latest(ids), on_click=actions.open_archive, args=(t,))

    _grid(list(_TYPE_ORDER), draw)
    markup(f'<span class="lock-note">{icon("lock", 12, "#80858E", 2)}내가 멤버인 프로젝트의 회의록만 보여요</span>')


# ---------------------------------------------------------------- 2. 회의 종류 → 그 종류의 회의 목록
def _type(s: AppState, t: str) -> None:
    """월간 · 주간 · 일반 회의를 누르면 그 종류의 회의를 최근 순으로 바로 보여 줘요 (기간 · 프로젝트로 나누지 않음)."""
    mtype = repo.meeting_types()[t]
    ids = arc.meeting_ids_of_type(s, t)  # 최근 순
    with st.container(horizontal=True, vertical_alignment="bottom"):
        page_header(mtype.name, f"{mtype.meta} · 회의록 {len(ids)}개")
        button("회의록 올리기", key="type_upload", variant="primary", on_click=actions.open_new_note)
    host_ids, att_ids = arc.by_role(ids, "host"), arc.by_role(ids, "att")
    todo_ids = [i for i in ids if sel.my_todo_status(s, i) == "left"]
    button_group(
        [("all", f"전체 {len(ids)}"), ("todo", f"내 할 일이 있는 회의 {len(todo_ids)}"),
         ("host", f"내가 주관 {len(host_ids)}"), ("att", f"참석 {len(att_ids)}")],
        s.archive_role,
        lambda v: actions.update(archive_role=v),
        key="trole",
    )
    shown = {"todo": todo_ids, "host": host_ids, "att": att_ids}.get(s.archive_role, ids)
    if shown:
        meeting_table([sel.meeting_row(s, i) for i in shown], key=f"type-{t}")
    else:
        empty_state("아직 회의록이 없어요." if not ids
                    else "내 할 일이 있는 회의가 없어요." if s.archive_role == "todo"
                    else "조건에 맞는 회의록이 없어요.", pad=40)


# ---------------------------------------------------------------- 검색 (회의록 첫 화면에만)
def _search_bar(s: AppState) -> None:
    """치는 즉시(한글 포함) 검색 결과 페이지로 이동. 결과는 views/search.py"""
    key = f"archive_q_{s.archive_qnonce}"
    st.text_input("회의록 검색", key=key, placeholder="회의명 · 폴더명 · 구성원 · 회의 내용으로 찾기",
                  label_visibility="collapsed", icon=":material/search:", live=True,
                  on_change=actions.start_search, args=(key,))
    ime_live("회의록 검색")


# ---------------------------------------------------------------- 진입점
def render() -> None:
    s = get_state()
    t = s.archive_type
    with page("archive"):
        if t not in repo.meeting_types():
            _search_bar(s)
            _root(s)
        else:
            _type(s, t)
