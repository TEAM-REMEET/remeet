"""관리자 관련 모달.

- 관리자에게 문의 (사용자): 기술 이슈(API · 인증 문제)로 외부 전송이 실패했을 때 관리자 이메일 안내
- 구성원 수정 (관리자): 팀 · 직무 · 직급 · 권한 · 활성 · 스킬
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote

import streamlit as st

from config import ADMIN_EMAIL
from components import member_fields as mf
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button, button_group
from ui.html import markup
from utils.browser import copy_to_clipboard
from utils.text import esc


@st.dialog("관리자에게 문의", width="small", on_dismiss=actions.close_modal)
def contact_admin_dialog() -> None:
    s = get_state()
    f = next(x for x in repo.export_failures() if x.id == s.contact_failure)
    title = f.title or f.meeting  # 실패 기록에 같이 온 회의 제목
    tool = repo.tool(f.tool)
    markup(
        '<p class="ca-lead"><b>API · 인증 같은 기술 문제</b>라서 직접 다시 보낼 수 없어요.<br>'
        "아래 내용을 관리자 이메일로 알려 주세요.<br>연동이 복구되면 관리자가 다시 보내 드려요.</p>"
        '<dl class="ca-info">'
        f"<dt>회의록</dt><dd>{esc(title)}</dd>"
        f"<dt>보낼 곳</dt><dd>{esc(tool.name)}" + (f" · {esc(tool.target)}" if tool.target else "") + "</dd>"
        f"<dt>실패 시각</dt><dd>{esc(f.when)}</dd>"
        f"<dt>원인</dt><dd>{esc(f.reason)}<span class=\"code\">{esc(f.code)}</span></dd>"
        "</dl>"
    )
    with st.container(key="ca-mail", horizontal=True, vertical_alignment="center", gap="small"):
        markup(f'<span class="ca-mail"><span class="lb">관리자 이메일</span><b>{esc(ADMIN_EMAIL)}</b></span>')
        copied = s.copied == "admin_mail"
        button("복사됨" if copied else "복사", key="ca_copy", variant="mini", on_click=actions.mark_copied,
               args=("admin_mail",))
    if copied:
        # 복사 스크립트는 이메일 줄 밖의 숨은 칸에 (줄 안에 넣으면 칸이 하나 늘어 버튼이 왼쪽으로 밀림)
        with st.container(key="ca-js"):
            copy_to_clipboard(ADMIN_EMAIL, nonce="admin_mail")

    subject = f"[RE:MEET] {tool.name} 전송 실패 문의 — {title}"
    body = (f"회의록: {title}\n보낼 곳: {tool.name} {tool.target}\n실패 시각: {f.when}\n"
            f"원인: {f.reason} ({f.code})\n")
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("닫기", key="ca_close", variant="secondary"):
            actions.close_modal()
            st.rerun()
        st.link_button("메일 쓰기", f"mailto:{ADMIN_EMAIL}?subject={quote(subject)}&body={quote(body)}",
                       type="primary", icon=":material/mail:")


# ---------------------------------------------------------------- 조직 선택 (관리자 · 구성원 추가)
_ORG_TABS = (("recent", "최근"), ("fav", "즐겨찾기 ☆"), ("all", "전체 조직"))  # 사진처럼 탭 3개


def _k(name: str) -> str:
    """한글 조직명 → 위젯 key 용 짧은 영문 (이름마다 다름)."""
    return hashlib.md5(name.encode()).hexdigest()[:8]


def _team_row(s, team: str, *, level: int = 0, star: bool = False) -> None:
    on = s.adm_org_pick == team
    with st.container(key=f"orgrow-{level}-{'on' if on else 'off'}-{_k(team)}", horizontal=True,
                      vertical_alignment="center", gap=None):
        if not star:  # 트리: 펼치기 화살표 자리만큼 비움 (폴더와 줄맞춤)
            markup('<span class="org-gap"></span>')
        button(team, key=f"orgteam_{level}_{_k(team)}", variant="ghost", width="stretch",
               icon=":material/check_box:" if on else ":material/check_box_outline_blank:",
               on_click=actions.org_pick, args=(team,))
        if star:
            fav = team in s.adm_org_fav
            button("", key=f"orgfav_{level}_{_k(team)}", variant="ghost",
                   icon=":material/star:" if fav else ":material/star_outline:", on_click=actions.org_fav, args=(team,))


def _tree(s, nodes: list[dict], level: int = 0) -> None:
    for n in nodes:
        kids = n.get("children")
        if not kids:
            _team_row(s, n["name"], level=level)
            continue
        is_open = n["name"] in s.adm_org_open
        with st.container(key=f"orgrow-{level}-dir-{_k(n['name'])}", horizontal=True, vertical_alignment="center",
                          gap=None):
            button("", key=f"orgtw_{_k(n['name'])}", variant="ghost",
                   icon=":material/expand_more:" if is_open else ":material/chevron_right:",
                   on_click=actions.org_toggle, args=(n["name"],))
            button(n["name"], key=f"orgdir_{_k(n['name'])}", variant="ghost", width="stretch",
                   icon=":material/folder_open:" if is_open else ":material/folder:",
                   on_click=actions.org_toggle, args=(n["name"],))
        if is_open:
            _tree(s, kids, level + 1)


@st.dialog(":material/account_tree: 조직 선택", width="small", on_dismiss=actions.close_modal)
def org_picker_dialog() -> None:
    s = get_state()
    markup('<p class="ca-lead org-lead">조직도에서 팀을 선택하거나, 조직명을 검색해 빠르게 찾을 수 있어요.</p>')
    qkey = f"org_q_{s.adm_org_nonce}"
    st.text_input("조직명 검색", key=qkey, value=s.adm_org_query, placeholder="조직명 검색", icon=":material/search:",
                  label_visibility="collapsed", live=True, on_change=actions.org_query, args=(qkey,))
    q = s.adm_org_query.lower()
    if not q:  # 탭: 최근 · 즐겨찾기 · 전체 조직 (검색 중엔 검색 결과만)
        with st.container(key="org-tabs"):
            button_group(list(_ORG_TABS), s.adm_org_tab, actions.org_tab, key="org_tab", style="tab")
    with st.container(key="org-body", height=420, gap=None):
        if q:  # 검색: 팀 이름 · 본부 · 부로 찾기
            hits = [t for t in sel.admin_teams() if q in t.lower() or q in sel.team_path(t).lower()]
            for t in hits:
                _team_row(s, t, star=True)
                if sel.team_path(t):
                    markup(f'<span class="org-path">{esc(sel.team_path(t))}</span>')
            if not hits:
                markup('<p class="hint" style="padding:24px 0;text-align:center">찾는 조직이 없어요.</p>')
        else:
            if s.adm_org_tab == "all":
                _tree(s, repo.org_tree())
            else:
                items = s.adm_org_recent if s.adm_org_tab == "recent" else sorted(s.adm_org_fav)
                for t in items:
                    _team_row(s, t, star=True)
                    if sel.team_path(t):
                        markup(f'<span class="org-path">{esc(sel.team_path(t))}</span>')
                if not items:
                    empty = "최근 고른 팀이 없어요." if s.adm_org_tab == "recent" else "☆ 를 눌러 자주 고르는 팀을 즐겨찾기에 넣어 보세요."
                    markup(f'<p class="hint" style="padding:24px 0;text-align:center">{empty}</p>')
    markup(f'<div class="org-picked"><span>선택된 팀</span><b>{esc(s.adm_org_pick or "없음")}</b>'
           + (f'<em>{esc(sel.team_path(s.adm_org_pick))}</em>' if s.adm_org_pick and sel.team_path(s.adm_org_pick) else "")
           + "</div>")
    if button("선택 완료", key="org_done", variant="primary", width="stretch", disabled=not s.adm_org_pick):
        actions.org_done()
        st.rerun()


# ---------------------------------------------------------------- 구성원 수정 (관리자 · 구성원 관리)
_PERMISSION = {"member": "구성원", "admin": "관리자"}


@st.dialog("구성원 수정", width="large", on_dismiss=actions.close_modal)
def member_edit_dialog() -> None:
    s = get_state()
    row = next((r for r in repo.people_rows() if r["user_id"] == s.adm_edit_pid), None)
    if row is None:
        markup('<p class="hint">이 구성원을 찾을 수 없어요. 목록을 새로 고쳐 주세요.</p>')
        return
    p = f"edit_{s.adm_edit_nonce}"
    markup(f'<div class="adm-who">{avatar(row["name"][:1], size=36, accent=row["permission"] == "admin")}'
           f'<span><b>{esc(row["name"])}</b><span>{esc(row["email"] or "-")} · {esc(row["user_id"])}</span></span></div>')
    teams = [t["name"] for t in repo.catalog("teams")]
    jobs = repo.job_catalog()
    c = st.columns(2, gap="small")
    with c[0]:
        st.selectbox("팀", teams, index=teams.index(row["team"]) if row["team"] in teams else None, key=f"{p}_team",
                     placeholder="팀 검색")
    with c[1]:
        st.selectbox("직무", jobs, index=jobs.index(row["job"]) if row["job"] in jobs else None, key=f"{p}_job",
                     placeholder="직무 검색 (없으면 직접 입력)", accept_new_options=True)
    c = st.columns(3, gap="small", vertical_alignment="bottom")
    with c[0]:
        mf.position_select(p, row["position"])
    with c[1]:
        st.selectbox("권한", list(_PERMISSION), index=list(_PERMISSION).index(row["permission"]), key=f"{p}_permission",
                     format_func=_PERMISSION.get)
    with c[2]:
        st.toggle("활성 계정", value=row["is_active"] is not False, key=f"{p}_active",
                  help="끄면 비활성 구성원이 돼요. 삭제하지 않고 기록은 남아요.")
    mf.skill_fields(p, {k["skill_id"]: k["level"] for k in row["skills"]})
    if s.adm_edit_error:
        markup(f'<span role="alert" class="cp-error">{esc(s.adm_edit_error)}</span>')
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="me_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("저장", key="me_save", variant="primary") and actions.save_member_edit(p):
            st.rerun()
