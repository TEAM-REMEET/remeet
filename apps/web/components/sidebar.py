from __future__ import annotations

import streamlit as st

from data import api, repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import brand_logo
from ui.tags import stage_trail
from utils.text import esc, safe_key

_TREE_ORDER = ("monthly", "weekly", "normal")  # 회의록 페이지와 같은 순서


def _nav_item(label: str, key: str, *, on: bool, on_click, args=(), icon_name: str | None = None,
              sub: bool = False, trail_html: str = "") -> None:
    variant = ("navsubon" if on else "navsub") if sub else ("navon" if on else "nav")
    with st.container(key=f"navrow-{key}", gap=None):
        if trail_html:
            markup(trail_html)
        button(label, key=f"nav_{key}", variant=variant, on_click=on_click, args=args, icon=icon_name, width="stretch")


def _caption(text: str) -> None:
    markup(f'<div class="nav-caption">{esc(text)}</div>')


class _Tree:
    """회의록 트리 한 번 그리기 (지금 위치 강조 · 펼친 노드 기억)."""

    def __init__(self) -> None:
        self.s = get_state()
        self.current, _ = arc.node_of_location(self.s)

    def pending(self, ids: list[str]) -> int:
        """남은 단계가 있는 회의 수 (검토 중 · 전달 전 · 외부 도구 전송 전 …)."""
        return sum(1 for mid in ids if stage_trail(sel.quick_stage(self.s, mid)))

    def folder(self, node: str, label: str, level: int, count: int | None = None, *, new: bool = False,
               pending: int = 0) -> bool:
        """폴더 줄. 펼쳐져 있으면 True (호출한 쪽이 자식을 그림).

        - 화살표(> / v): 누르면 바로 펼치기/접기만 (화면 이동 없음)
        - 이름: 그 화면으로 이동 + 펼치기
        """
        is_open = node in self.s.tree_open
        trail = f'<span class="nav-trail count">{count}</span>' if count is not None else ""
        if new:
            trail += '<span class="nav-trail newdot tree"></span>'
        if pending:  # 접어 둬도 안에 남은 단계가 있는 회의가 있다는 걸 알 수 있게
            trail += (f'<span class="nav-trail pend{" shift" if new else ""}" '
                      f'title="남은 단계가 있는 회의 {pending}개">{pending}</span>')
        on = node == self.current
        key = safe_key(node)
        with st.container(key=f"navrow-tree{level}-{key}", gap=None):
            if trail:
                markup(trail)
            button(label, key=f"tree_{key}", variant="treeon" if on else "tree", on_click=actions.tree_click,
                   args=(node,), width="stretch")
            button("", key=f"twisty_{key}", variant="twisty", on_click=actions.tree_toggle, args=(node,),
                   icon=":material/expand_more:" if is_open else ":material/chevron_right:")
        return is_open

    def meeting(self, mid: str, label: str, level: int, hint: str | None = None) -> None:
        """회의 파일 한 줄. 누르면 그 회의 화면으로. 마우스를 올리면 hint (기본: 회의 제목)가 보여요."""
        node = f"m:{mid}"
        key = safe_key(node)
        with st.container(key=f"navrow-tree{level}-leaf_{key}", gap=None):
            if trail := stage_trail(sel.quick_stage(self.s, mid)):  # 남은 단계 (검토 중 · 전달 전 …)
                markup(trail)
            button(label, key=f"tree_{key}", variant="treeon" if node == self.current else "tree",
                   on_click=actions.tree_click, args=(node,), icon=":material/description:", width="stretch",
                   help=hint or repo.meeting(mid).title)

    def render(self) -> None:
        if not self.folder("root", "회의록", 0):
            return
        if self.s.tree_mode == "project":
            self._projects()
        else:
            self._timeline()

    def _timeline(self) -> None:
        """정기 회의: 월간 ─ 연도 ─ 회의 / 주간 ─ 연도 ─ 월 ─ 회의 / 일반 ─ 회의"""
        types = repo.meeting_types()
        for t in _TREE_ORDER:
            ids = arc.meeting_ids_of_type(self.s, t)  # 최근 순
            if not self.folder(f"t:{t}", types[t].name, 1, len(ids), pending=self.pending(ids)):
                continue
            if t == "normal":
                for mid in ids:
                    self.meeting(mid, arc.dotted(arc.meeting_day(mid)), 2)
                continue
            # 주간 회의는 그 주 월요일 기준으로 연도 · 월을 나눠요 (주차 계산과 같은 기준)
            anchor = (lambda mid: arc.week_of(arc.meeting_day(mid))[0]) if t == "weekly" else arc.meeting_day
            for year, year_ids in _group(ids, lambda mid: anchor(mid).year):
                if not self.folder(f"y:{t}:{year}", f"{year}년", 2, len(year_ids), pending=self.pending(year_ids)):
                    continue
                if t == "monthly":
                    for mid in year_ids:
                        d = arc.meeting_day(mid)
                        self.meeting(mid, f"{d.month}월 ({arc.dotted(d)})", 3)
                    continue
                for month, month_ids in _group(year_ids, lambda mid: anchor(mid).month):
                    if self.folder(f"ym:{t}:{year}:{month}", f"{month}월", 3, len(month_ids),
                                   pending=self.pending(month_ids)):
                        for mid in month_ids:
                            d = arc.meeting_day(mid)
                            self.meeting(mid, f"{arc.week_of(d)[1]}주차 ({arc.dotted(d)})", 4)

    def _projects(self) -> None:
        """프로젝트 단위: 프로젝트 ─ 프로젝트 폴더 ─ 회의.

        shortcut: 지금은 기존 프로젝트 폴더를 그대로 프로젝트로 보여줌, 회의 ↔ 프로젝트 매핑 규약이 생기면 교체
        """
        fs = sel.folders(self.s)
        fids = sel.all_my_folder_ids(self.s)
        if not self.folder("proj", "프로젝트", 1, len(fids)):
            return
        for fid in fids:
            notes = sel.folder_note_ids(self.s, fid)  # 나에게 보이는 회의만 (최근 순)
            if self.folder(f"pf:{fid}", fs[fid].name, 2, len(notes), new=fs[fid].is_new,
                           pending=self.pending(notes)):
                for mid in notes:
                    self.meeting(mid, repo.meeting(mid).title, 3, hint=arc.dotted(arc.meeting_day(mid)))


def _group(ids: list[str], key) -> list[tuple[int, list[str]]]:
    """순서를 지키며 key가 같은 회의끼리 묶기 (최근 순 입력 → 최근 묶음 먼저)."""
    out: dict[int, list[str]] = {}
    for mid in ids:
        out.setdefault(key(mid), []).append(mid)
    return list(out.items())


def _member_count(s) -> str:
    """구성원 수 배지. 백엔드가 잠깐 안 되면 배지만 빼고 메뉴는 그대로 그림 (본문에서 오류를 보여 줌)."""
    try:
        return f'<span class="nav-trail count">{len(sel.admin_members(s))}</span>'
    except api.ApiError:
        return ""


def _admin_sidebar() -> None:
    """관리자 계정: 관리 메뉴 3개 + 관리자 카드."""
    s = get_state()
    me = sel.admin_profile(s)
    with st.sidebar, st.container(key="sidebar"):
        with st.container(key="sb-logo-link", gap=None):  # 로고를 누르면 구성원 관리로
            markup(f'<div class="sb-logo">{brand_logo(20)}<span class="sb-admin-tag">관리자</span></div>')
            button("구성원 관리로", key="logo_home", variant="ghost", on_click=actions.go, args=("adm_members",),
                   width="stretch")
        with st.container(key="sidebar-groups", gap=None):
            with st.container(key="navgroup-admin"):
                _caption("관리")
                _nav_item("구성원 관리", "adm_members", on=s.screen == "adm_members", on_click=actions.go,
                          args=("adm_members",), icon_name=":material/group:",
                          trail_html=_member_count(s))
                warn = sel.has_integration_warning(s)
                _nav_item("MCP 관리", "adm_mcp", on=s.screen == "adm_mcp", on_click=actions.go, args=("adm_mcp",),
                          icon_name=":material/hub:",
                          trail_html='<span class="nav-trail dot" title="확인이 필요한 연결이 있어요"></span>' if warn else "")
                _nav_item("비용 관리", "adm_cost", on=s.screen == "adm_cost" and not s.not_found, on_click=actions.go,
                          args=("adm_cost",), icon_name=":material/payments:")
        with st.container(key="sidebar-user", horizontal=True, vertical_alignment="center", gap="small", wrap=False):
            markup(
                f'<div class="me-card">{avatar(me["name"][:1], size=30, accent=True)}'
                f'<span class="who"><b>{esc(me["name"])}</b><span>{esc(me["email"])}</span></span></div>'
            )
            button("로그아웃", key="logout", variant="ghost", on_click=actions.logout)


def render_sidebar() -> None:
    s = get_state()
    if s.is_admin:
        _admin_sidebar()
        return
    me = sel.me()
    with st.sidebar, st.container(key="sidebar"):
        with st.container(key="sb-logo-link", gap=None):  # 로고를 누르면 홈으로
            markup(f'<div class="sb-logo">{brand_logo(20)}</div>')
            button("홈으로", key="logo_home", variant="ghost", on_click=actions.go, args=("home",), width="stretch")
        with st.container(key="sidebar-groups", gap=None):
            with st.container(key="navgroup-main"):
                _nav_item("홈", "home", on=s.screen == "home" and not s.not_found, on_click=actions.go, args=("home",),
                          icon_name=":material/home:")
                _nav_item("새 회의록", "compose", on=s.screen == "compose" and not s.not_found, on_click=actions.open_new_note,
                          icon_name=":material/edit_square:")

            with st.container(key="navgroup-meetings", gap=None):
                button_group([("time", "정기 회의"), ("project", "프로젝트")], s.tree_mode, actions.set_tree_mode,
                             key="tree_mode", style="seg", gap=None)  # 폭 채우기는 workspace.css
                _Tree().render()

            with st.container(key="navgroup-share"):
                _caption("공유")
                _nav_item("공유한 회의", "sent", on=s.screen == "sent", on_click=actions.go, args=("sent",),
                          icon_name=":material/send:",
                          trail_html=f'<span class="nav-trail count">{len(sel.sent_shares(s))}</span>')
                _nav_item("공유 받은 회의", "recv", on=s.screen == "recv", on_click=actions.go, args=("recv",),
                          icon_name=":material/inbox:",
                          trail_html=f'<span class="nav-trail badge">{len(repo.received_meeting_ids())}</span>')

            with st.container(key="navgroup-ask"):  # 공유 아래 · 회의 기록에 묻는 온보딩 에이전트
                _nav_item("회의에 물어보기", "ask", on=s.screen == "ask", on_click=actions.go, args=("ask",),
                          icon_name=":material/forum:")

        with st.container(key="sidebar-user", horizontal=True, vertical_alignment="center", gap="small", wrap=False):
            markup(
                f'<div class="me-card">{avatar(me.initial, size=30, accent=True)}'
                f'<span class="who"><b>{esc(me.name)}</b><span>{esc(me.team)}</span></span></div>'
            )
            button("로그아웃", key="logout", variant="ghost", on_click=actions.logout)
