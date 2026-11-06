"""관리자 · 구성원 관리: 조직 구성원 목록(팀 · 직무 · 직급 · 권한 · 상태) · 「수정」 팝업 · 구성원 추가.

여기서 바꾼 값은 서버 공용 값이라 사용자 화면(참석자 · 주관자 고르기, 담당자 지정, 폴더 멤버)에 바로 반영돼요.
"""

from __future__ import annotations

import streamlit as st

from components import member_fields as mf
from data import repository as repo
from state import actions
from state import selectors as sel
from state.selectors import AdminMember
from state.store import AppState, get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.layout import empty_state, page, page_header
from ui.tags import tag
from utils.browser import ime_live
from utils.text import esc

_SEARCH = "구성원 검색"
_STATUS = {"active": ("활성", "on"), "invited": ("초대 중", "wait"), "inactive": ("비활성", "off")}
_ROLE = {"admin": "관리자", "member": "구성원"}
_COLS = [2.4, 1.3, 1.3, 0.8, 0.85, 0.75, 0.95, 0.9]


# 표 머리글: (컬럼, 제목, 정렬 · 필터 가능). 최근 접속은 아직 기록이 없어 둘 다 안 됨
_HEADS = (("name", "이름 · 이메일", True), ("team", "팀", True), ("job", "직무", True), ("position", "직급", True),
          ("role", "권한", True), ("status", "상태", True), ("last", "최근 접속", False), ("", "", False))
_FILTERS = {"team": "팀", "job": "직무", "position": "직급", "role": "권한", "status": "상태"}
_POSITION_RANK = {"junior": 1, "mid": 2, "senior": 3}  # 직급은 글자 순이 아니라 주니어 → 시니어 순


def _value(m: AdminMember, col: str) -> str:
    """화면에 보이는 값 (필터 선택지 · 정렬 기준)."""
    return {"name": m.name, "team": m.team, "job": m.job, "position": mf.POSITION.get(m.position, "-"),
            "role": _ROLE[m.role], "status": _STATUS[m.status][0]}[col]


def _sort_key(m: AdminMember, col: str):
    return _POSITION_RANK.get(m.position, 0) if col == "position" else _value(m, col)


def _filtered(s: AppState, rows: list[AdminMember]) -> list[AdminMember]:
    q = s.adm_query.lower()
    shown = [m for m in rows
             if all(_value(m, col) in picked for col, picked in s.adm_filters.items() if picked)
             and (not q or q in m.name.lower() or q in m.email.lower() or q in m.team.lower())]
    if s.adm_sort:
        shown.sort(key=lambda m: _sort_key(m, s.adm_sort), reverse=s.adm_sort_desc)
        shown.sort(key=lambda m: not m.is_self)  # 내 관리 계정은 늘 맨 위 (stable sort라 순서 유지)
    return shown


def _head(s: AppState, col: str, title: str, sortable: bool) -> None:
    """머리글 한 칸. 기본 ⌃(오름차순), 누르면 ⌄(내림차순)으로 바뀌며 정렬."""
    if not sortable:
        markup(f'<span class="adm-th">{title}</span>')
        return
    on = s.adm_sort == col
    arrow = ":material/keyboard_arrow_down:" if on and s.adm_sort_desc else ":material/keyboard_arrow_up:"
    with st.container(key=f"adm-th-{'on-' if on else ''}{col}", gap=None):
        button(f"{title} {arrow}", key=f"adm_sort_{col}", variant="link", on_click=actions.adm_sort, args=(col,))


def _options(rows: list[AdminMember], col: str) -> list[str]:
    values = {_value(m, col) for m in rows}
    if col == "position":  # 시니어 → 미드 → 주니어 순
        return [v for v in (*[mf.POSITION[k] for k in ("senior", "mid", "junior")], "-") if v in values]
    return sorted(values, key=lambda v: (v == "-", v))  # "-"(값 없음)은 맨 뒤


def _filter_bar(s: AppState, rows: list[AdminMember]) -> None:
    """노션 표처럼 표 위 필터 줄: [팀 ▾] [직무 ▾] [+ 필터] ······ 초기화"""
    with st.container(key="adm-fbar", horizontal=True, vertical_alignment="center", gap="small"):
        for col in list(s.adm_filters):
            title, picked = _FILTERS[col], s.adm_filters[col]
            label = title + (f": {picked[0]}" + (f" 외 {len(picked) - 1}" if len(picked) > 1 else "") if picked else "")
            with st.container(key=f"adm-chip-{col}", gap=None):  # key가 바뀌면 메뉴가 닫혀서 고정, 색은 표시용 span으로
                markup(f'<span class="adm-chip-{"on" if picked else "off"}"></span>')
                with st.popover(label, key=f"adm_chip_pop_{col}", on_change="rerun"):
                    markup(f'<span class="adm-fhead">{title}</span>')
                    for i, v in enumerate(_options(rows, col)):
                        st.checkbox(v, value=v in picked, key=f"adm_f_{col}_{i}_{v in picked}",
                                    on_change=actions.adm_set_filter, args=(col, v))
                    button("필터 삭제", key=f"adm_frm_{col}", variant="ghost", icon=":material/delete:",
                           on_click=actions.adm_remove_filter, args=(col,))
        left = [c for c in _FILTERS if c not in s.adm_filters]
        if left:
            with st.container(key="adm-fadd", gap=None):
                with st.popover("필터", icon=":material/add:", key="adm_fadd_pop", on_change="rerun"):
                    for col in left:
                        button(_FILTERS[col], key=f"adm_fadd_{col}", variant="ghost", on_click=actions.adm_add_filter,
                               args=(col,), width="stretch")
        markup('<span class="adm-fspacer"></span>')
        button("초기화", key="adm_reset", variant="ghost", on_click=actions.adm_reset_table)


def _cell(text: str, muted: bool = False) -> None:
    markup(f'<span class="adm-cell{" muted" if muted else ""}">{esc(text or "-")}</span>')


def _row(m: AdminMember) -> None:
    with st.container(key=f"adm-mrow-{m.id}", gap=None):
        c = st.columns(_COLS, vertical_alignment="center", gap="small")
        with c[0]:
            me = '<span class="me">나</span>' if m.is_self else ""
            markup(f'<div class="adm-who">{avatar(m.name[:1], size=32, accent=m.role == "admin")}'
                   f'<span><b>{esc(m.name)}{me}</b><span>{esc(m.email)}</span></span></div>')
        with c[1]:
            _cell(m.team)
        with c[2]:
            _cell(m.job)
        with c[3]:
            _cell(mf.POSITION.get(m.position, "-"))
        with c[4]:
            if m.role == "admin":
                markup(tag(_ROLE[m.role], "host"))
            else:
                _cell(_ROLE[m.role])
        with c[5]:
            label, tone = _STATUS[m.status]
            markup(tag(label, tone))
        with c[6]:
            _cell(m.last, muted=True)
        with c[7]:
            if m.is_self:
                _cell("-", muted=True)
            else:
                button("", key=f"adm_edit_{m.id}", variant="secondary", icon=":material/edit:",
                       on_click=actions.open_member_edit, args=(m.id,), help=f"{m.name} 님 정보 수정")


def _add_form(s: AppState) -> None:
    """구성원 바로 추가 (초대 메일 없이 바로 「활성」).

    팀이 많을 땐: 조직 선택칸에서 검색하거나 「조직도」로 본부 › 부 › 팀을 펼쳐 고르기
    직무가 많을 땐: 직무 검색(목록에 없으면 직접 입력) · 「최근 사용」 칩
    """
    n = s.adm_nonce
    if s.adm_team_pending:  # 조직 선택 모달에서 고른 팀 (선택칸을 그리기 전에 넣어야 함)
        st.session_state[f"add_team_{n}"] = s.adm_team_pending
        s.adm_team_pending = ""
    with st.container(key="adm-add", gap="small"):
        markup('<b class="adm-add-h">구성원 추가</b>'
               '<span class="adm-note">추가하면 바로 활성 계정이 되고, 회의 참석자 · 주관자 · 담당자 · 폴더 멤버로 고를 수 있어요.</span>')
        c = st.columns([1, 1.2, 1.95, 1.6, 0.9, 0.55], gap="small", vertical_alignment="top")
        with c[0]:
            st.text_input("이름", key=f"add_name_{n}", placeholder="홍길동")
        with c[1]:
            st.text_input("이메일", key=f"add_email_{n}", placeholder="name@democompany.kr")
        with c[2]:  # 조직 선택: 네모 안에 돋보기 · 팀 검색 · 「조직도 열기」
            with st.container(key="adm-field-team", gap=None):
                markup('<span class="adm-flabel">조직 선택 (팀)</span>')
                with st.container(key="adm-searchbox-team", horizontal=True, vertical_alignment="center", gap="small"):
                    st.selectbox("조직 선택 (팀)", sel.admin_teams(), index=None, key=f"add_team_{n}",
                                 placeholder="팀 검색 또는 조직도에서 선택", label_visibility="collapsed")
                    button("조직도 열기", key="adm_org_open", variant="accentline", icon=":material/account_tree:",
                           on_click=actions.open_org_picker, args=("add",))
                team = st.session_state.get(f"add_team_{n}")
                if team and sel.team_path(team):
                    markup(f'<span class="org-path">{esc(sel.team_path(team))}</span>')
        with c[3]:  # 직무: 돋보기 검색(자동완성) + 최근 사용
            with st.container(key="adm-field-job", gap=None):
                markup('<span class="adm-flabel">직무</span>')
                with st.container(key="adm-searchbox-job", gap=None):
                    st.selectbox("직무", repo.job_catalog(), index=None, key=f"add_job_{n}", placeholder="직무 검색",
                                 accept_new_options=True, label_visibility="collapsed")
                with st.container(key="adm-recent-jobs", horizontal=True, vertical_alignment="center", gap="xsmall"):
                    markup('<span class="adm-recent-lb">최근 사용:</span>')
                    for j in s.adm_recent_jobs:
                        jobs = repo.job_catalog()
                        button(j, key=f"recent_job_{jobs.index(j) if j in jobs else abs(hash(j))}",
                               variant="chip", on_click=actions.add_set_job, args=(n, j))
        with c[4]:
            st.selectbox("권한", ["member", "admin"], key=f"add_role_{n}", format_func=_ROLE.get)
        with c[5]:
            button("추가", key="adm_add_ok", variant="primary", on_click=actions.adm_add_member, args=(n,))
        c = st.columns([1, 4.2], gap="small", vertical_alignment="top")
        with c[0]:
            mf.position_select(f"add_{n}")
        with c[1]:
            mf.skill_fields(f"add_{n}")
        if s.adm_invite_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.adm_invite_error)}</span>')


def render() -> None:
    s = get_state()
    rows = sel.admin_members(s)
    with page("adm-members"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            page_header("구성원 관리", "RE:MEET을 쓰는 조직 구성원의 권한과 계정 상태를 관리해요.")
            button("닫기" if s.adm_adding else "구성원 추가", key="adm_add_toggle",
                   variant="secondary" if s.adm_adding else "primary",
                   icon=None if s.adm_adding else ":material/person_add:", on_click=actions.toggle_add_member)
        if s.adm_adding:
            _add_form(s)
        with st.container(key="adm-filter", horizontal=True, vertical_alignment="center", gap="small"):
            st.text_input(_SEARCH, key="adm_q", value=s.adm_query, live=True, icon=":material/search:",
                          placeholder="이름 · 이메일 · 팀으로 찾기", label_visibility="collapsed",
                          on_change=actions.adm_filter, args=("adm_q",))
            ime_live(_SEARCH)
            on = any(s.adm_filters.values())
            with st.container(key=f"adm-ftoggle{'-on' if s.adm_filter_open else ''}{'-dot' if on else ''}", gap=None):
                button("", key="adm_ftoggle", variant="ghost", icon=":material/filter_list:",
                       on_click=actions.adm_open_filter_bar, help="필터")
        if s.adm_filter_open:
            _filter_bar(s, rows)
        shown = _filtered(s, rows)
        markup(f'<span class="adm-count">{len(shown)}명 / 전체 {len(rows)}명</span>')
        with st.container(key="adm-table", gap=None):
            with st.container(key="adm-thead", gap=None):
                c = st.columns(_COLS, gap="small", vertical_alignment="center")
                for col, (key, title, sortable) in zip(c, _HEADS):
                    with col:
                        _head(s, key, title, sortable)
            if not shown:
                empty_state("조건에 맞는 구성원이 없어요. 검색어나 필터를 바꿔 보세요.", pad=36)
            for m in shown:
                _row(m)
