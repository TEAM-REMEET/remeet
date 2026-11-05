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


def _summary(rows: list[AdminMember]) -> None:
    n = len(rows)
    admins = sum(m.role == "admin" for m in rows)
    invited = sum(m.status == "invited" for m in rows)
    inactive = sum(m.status == "inactive" for m in rows)
    items = (("전체", n), ("관리자", admins), ("초대 중", invited), ("비활성", inactive))
    markup('<div class="adm-sum">' + "".join(f"<span>{lb}<b>{v}</b></span>" for lb, v in items) + "</div>")


def _filtered(s: AppState, rows: list[AdminMember]) -> list[AdminMember]:
    q = s.adm_query.lower()
    return [m for m in rows
            if (not s.adm_team or m.team == s.adm_team)
            and (not q or q in m.name.lower() or q in m.email.lower() or q in m.team.lower())]


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
        _summary(rows)
        with st.container(key="adm-filter", horizontal=True, vertical_alignment="center", gap="small"):
            st.text_input(_SEARCH, key="adm_q", value=s.adm_query, live=True, icon=":material/search:",
                          placeholder="이름 · 이메일 · 팀으로 찾기", label_visibility="collapsed",
                          on_change=actions.adm_filter, args=("adm_q",))
            ime_live(_SEARCH)
            teams = [""] + sel.admin_teams()
            st.selectbox("팀", teams, index=teams.index(s.adm_team), key="adm_team_sel",
                         format_func=lambda t: t or "팀 전체", label_visibility="collapsed",
                         on_change=lambda: actions.adm_set_team(st.session_state["adm_team_sel"]))
        shown = _filtered(s, rows)
        with st.container(key="adm-table", gap=None):
            heads = ("이름 · 이메일", "팀", "직무", "직급", "권한", "상태", "최근 접속", "")
            with st.container(key="adm-thead", gap=None):
                c = st.columns(_COLS, gap="small")
                for col, h in zip(c, heads):
                    with col:
                        markup(f'<span class="adm-th">{h}</span>')
            if not shown:
                empty_state("조건에 맞는 구성원이 없어요. 검색어나 팀을 바꿔 보세요.", pad=36)
            for m in shown:
                _row(m)
