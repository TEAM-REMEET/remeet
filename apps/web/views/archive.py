"""회의록 화면 (사이드바 트리의 「회의록」과 같은 구조).

  회의록                → 월간 · 주간 · 일반 회의 버튼
  회의 종류              → 주차별(월별·일별) · 프로젝트별 버튼
  주차별                → 주차 폴더 목록
  주차별 목록에서도 전체 / 내가 만든 폴더 / 멤버로 거를 수 있음
  한 주차               → 프로젝트 폴더와 같은 화면 (멤버 · 멤버 관리 · 폴더 삭제 · 회의록 올리기 · 회의록/위키 탭)
  프로젝트별             → 전체 / 내가 만든 폴더 / 멤버 + 폴더 카드 (폴더를 누르면 폴더 화면)
"""

from __future__ import annotations

import streamlit as st

from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page, page_header
from ui.tags import tag
from utils.browser import ime_live
from utils.text import esc
from views.folder import FolderPage, render_folder_page

_TYPE_ORDER = ("monthly", "weekly", "normal")  # 화면 버튼 순서: 월간 · 주간 · 일반

_OWNER_EMPTY = {
    "mine": "내가 만든 폴더가 없어요. 회의록을 올릴 때 새 주제를 적으면 폴더가 만들어져요.",
    "member": "멤버로 들어가 있는 폴더가 없어요.",
    "all": "아직 속한 폴더가 없어요. 회의록을 올릴 때 큰 주제를 적으면 폴더가 만들어져요.",
}


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
def _root(s: AppState) -> None:
    page_header("회의록", "회의 종류를 고르면 기간별 · 프로젝트별로 볼 수 있어요")
    types = repo.meeting_types()

    def draw(t: str) -> None:
        ids = arc.meeting_ids_of_type(s, t)
        _option_card(f"type-{t}", icon_name="folder", title=types[t].name,
                     meta=f"폴더 {len(sel.folder_ids_of_type(s, t))}개 · 회의록 {len(ids)}개",
                     last=_latest(ids), on_click=actions.open_archive, args=(t,))

    _grid(list(_TYPE_ORDER), draw)
    markup(f'<span class="lock-note">{icon("lock", 12, "#80858E", 2)}내가 멤버인 폴더의 회의록만 보여요</span>')


# ---------------------------------------------------------------- 2. 회의 종류
def _type(s: AppState, t: str) -> None:
    mtype = repo.meeting_types()[t]
    page_header(mtype.name, f"{mtype.meta} · 보는 방식을 골라 주세요")
    ps = arc.periods(s, t)
    fids = sel.folder_ids_of_type(s, t)

    def draw(g: str) -> None:
        if g == "period":
            _option_card(f"grp-{t}-period", icon_name="clock", title=arc.PERIOD_LABEL[t],
                         meta=f"{arc.PERIOD_UNIT[t]} {len(ps)}개 · 회의록 {sum(len(p.meeting_ids) for p in ps)}개",
                         last=f"최근 {ps[0].label}" if ps else "아직 회의록이 없어요",
                         on_click=actions.open_archive, args=(t, "period"))
        else:
            _option_card(f"grp-{t}-project", icon_name="folder", title="프로젝트별",
                         meta=f"폴더 {len(fids)}개",
                         last=" · ".join(sel.folders(s)[f].name for f in fids[:3]) or "아직 폴더가 없어요",
                         on_click=actions.open_archive, args=(t, "project"))

    _grid(list(arc.GROUPS), draw, cols=2)


# ---------------------------------------------------------------- 3-a. 기간 묶음 목록
def _period_owner(s: AppState, p: arc.Period) -> tuple[list[str], list[str], str, bool]:
    """기간 폴더의 (프로젝트 폴더, 멤버, 주관으로 보일 사람, 내가 만든 폴더인지).

    기간 폴더는 안에 든 프로젝트 폴더들을 모아 보여 주는 폴더라서
    멤버 = 그 프로젝트 폴더 멤버를 합친 것, 모든 프로젝트 폴더를 내가 만들었으면 '내가 만든 폴더'.
    """
    fs = sel.folders(s)
    fids = arc.period_folder_ids(s, p)
    members = list(dict.fromkeys(pid for k in fids for pid in sel.members_of(s, k)))
    is_host = bool(fids) and all(fs[k].host == ME for k in fids)
    host = ME if is_host else next(fs[k].host for k in fids if fs[k].host != ME)
    return fids, members, host, is_host


def _period_card(s: AppState, t: str, p: arc.Period) -> None:
    """주차·월·날 폴더 카드 (프로젝트별 폴더 카드와 같은 모양)."""
    ids = list(p.meeting_ids)
    fids, members, _, is_host = _period_owner(s, p)
    left = sum(1 for i in ids if sel.my_todo_status(s, i) == "left")
    names = [sel.folders(s)[k].name for k in fids]
    projects = names[0] + (f" 외 {len(names) - 1}개" if len(names) > 1 else "")
    with st.container(key=f"topic-per-{t}-{p.key}"):
        markup(
            '<div class="topic-card">'
            f'<div class="head"><span class="nm">{icon("folder", 18, "#F37321", 1.9)}{esc(p.label)}</span>'
            f'{tag("내가 만든 폴더" if is_host else "멤버", "host" if is_host else "att")}</div>'
            f'<span class="meta">회의록 {len(ids)}개 · 멤버 {len(members)}명 · {esc(projects)}</span>'
            f'<span class="last">{esc(_latest(ids))}</span>'
            + (f'<span>{tag(f"내 할 일 남은 회의 {left}", "mine")}</span>' if left else "")
            + "</div>"
        )
        button(p.label, key=f"open_per_{t}_{p.key}", variant="secondary", on_click=actions.open_archive,
               args=(t, "period", p.key), width="stretch")


def _periods(s: AppState, t: str) -> None:
    mtype = repo.meeting_types()[t]
    label = arc.PERIOD_LABEL[t]
    page_header(f"{mtype.name} · {label}", f"{mtype.meta} · 회의 날짜 기준으로 {arc.PERIOD_UNIT[t]}마다 묶었어요")
    all_ps = arc.periods(s, t)
    # 카드의 태그와 같은 기준: 안에 든 프로젝트 폴더를 모두 내가 만들었으면 '내가 만든 폴더', 아니면 '멤버'
    mine_ps = [p for p in all_ps if _period_owner(s, p)[3]]
    member_ps = [p for p in all_ps if not _period_owner(s, p)[3]]
    button_group(
        [("all", f"전체 {len(all_ps)}"), ("mine", f"내가 만든 폴더 {len(mine_ps)}"), ("member", f"멤버 {len(member_ps)}")],
        s.archive_owner,
        lambda v: actions.update(archive_owner=v),
        key="powner",
    )
    ps = {"mine": mine_ps, "member": member_ps}.get(s.archive_owner, all_ps)
    if not ps:
        empty_state(_OWNER_EMPTY.get(s.archive_owner, "아직 회의록이 없어요."), pad=40)
        return
    with st.container(key="topic-grid", gap="small"):  # 프로젝트별 폴더와 같은 네모 카드 · 많아지면 이 영역만 스크롤
        for start in range(0, len(ps), 3):
            cols = st.columns(3, gap="small")
            for col, p in zip(cols, ps[start:start + 3]):
                with col:
                    _period_card(s, t, p)


# ---------------------------------------------------------------- 3-b. 기간 폴더 (프로젝트 폴더와 같은 화면)
def _period(s: AppState, t: str, key: str) -> None:
    p = arc.period(s, t, key)
    if not p:
        empty_state("이 기간에 볼 수 있는 회의록이 없어요.", title="빈 폴더예요", pad=48)
        return
    fids, members, host, is_host = _period_owner(s, p)
    mine_fids = [k for k in fids if sel.folders(s)[k].host == ME]
    render_folder_page(s, FolderPage(
        key="period",
        title=p.label,
        host=host,
        is_host=is_host,
        can_manage=bool(mine_fids),  # 내가 만든 프로젝트 폴더가 하나라도 있으면 그 폴더 멤버는 관리 가능
        members=members,
        notes=list(p.meeting_ids),
        wiki_folders=fids,
        on_members=(actions.open_members, ((mine_fids or fids)[0], fids)),
        on_delete=(actions.open_delete, ("period", t, key)),
    ))


# ---------------------------------------------------------------- 3-c. 프로젝트(폴더) 목록
def _topic_card(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    notes = sel.folder_note_ids(s, fid)
    last = f"최근 {repo.meeting(notes[0]).date} · {repo.meeting(notes[0]).title}" if notes else "아직 회의록이 없어요"
    left = sum(1 for i in notes if sel.my_todo_status(s, i) == "left")
    is_host = f.host == ME
    with st.container(key=f"topic-{fid}"):
        markup(
            '<div class="topic-card">'
            f'<div class="head"><span class="nm">{icon("folder", 18, "#F37321", 1.9)}{esc(f.name)}</span>'
            f'{tag("내가 만든 폴더" if is_host else "멤버", "host" if is_host else "att")}</div>'
            f'<span class="meta">회의록 {len(notes)}개 · 멤버 {len(sel.members_of(s, fid))}명</span>'
            f'<span class="last">{esc(last)}</span>'
            + (f'<span>{tag(f"내 할 일 남은 회의 {left}", "mine")}</span>' if left else "")
            + "</div>"
        )
        button(f.name, key=f"open_topic_{fid}", variant="secondary", on_click=actions.open_folder, args=(fid,), width="stretch")


def _projects(s: AppState, t: str) -> None:
    mtype = repo.meeting_types()[t]
    mine_ids = sel.folder_ids_of_type(s, t)
    own = [k for k in mine_ids if sel.folders(s)[k].host == ME]
    member = [k for k in mine_ids if sel.folders(s)[k].host != ME]
    shown = {"mine": own, "member": member}.get(s.folder_owner_filter, mine_ids)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        page_header(f"{mtype.name} · 프로젝트별", f"{mtype.meta} · 큰 주제별로 폴더에 쌓여요")
        button("회의록 올리기", key="type_upload", variant="primary", on_click=actions.open_new_note)
    button_group(
        [("all", f"전체 {len(mine_ids)}"), ("mine", f"내가 만든 폴더 {len(own)}"), ("member", f"멤버 {len(member)}")],
        s.folder_owner_filter,
        lambda v: actions.update(folder_owner_filter=v),
        key="owner",
    )
    if shown:
        with st.container(key="topic-grid", gap="small"):  # 카드가 많아지면 이 영역만 스크롤
            for start in range(0, len(shown), 3):
                cols = st.columns(3, gap="small")
                for col, fid in zip(cols, shown[start:start + 3]):
                    with col:
                        _topic_card(s, fid)
    else:
        empty_state(_OWNER_EMPTY.get(s.folder_owner_filter, _OWNER_EMPTY["all"]), pad=40)
    markup(
        f'<span class="lock-note">{icon("lock", 12, "#80858E", 2)}'
        f"내가 멤버인 폴더만 보여요 · 멤버가 아닌 폴더 {mtype.hidden_count}개는 보이지 않아요</span>"
    )


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
    t, g, p = s.archive_type, s.archive_group, s.archive_period
    with page("archive"):
        if t not in repo.meeting_types():
            _search_bar(s)
            _root(s)
        elif g == "project":
            _projects(s, t)
        elif g == "period" and p:
            _period(s, t, p)
        elif g == "period":
            _periods(s, t)
        else:
            _type(s, t)
