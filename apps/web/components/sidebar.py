# from __future__ import annotations

# import streamlit as st

# from data import repository as repo
# from state import actions
# from state import archive as arc
# from state import selectors as sel
# from state.store import get_state
# from ui.avatar import avatar
# from ui.buttons import button
# from ui.html import markup
# from ui.icons import brand_logo
# from utils.text import esc, safe_key

# _TREE_ORDER = ("monthly", "weekly", "normal")  # 회의록 페이지와 같은 순서


# def _nav_item(label: str, key: str, *, on: bool, on_click, args=(), icon_name: str | None = None,
#               sub: bool = False, trail_html: str = "") -> None:
#     variant = ("navsubon" if on else "navsub") if sub else ("navon" if on else "nav")
#     with st.container(key=f"navrow-{key}", gap=None):
#         if trail_html:
#             markup(trail_html)
#         button(label, key=f"nav_{key}", variant=variant, on_click=on_click, args=args, icon=icon_name, width="stretch")


# def _caption(text: str) -> None:
#     markup(f'<div class="nav-caption">{esc(text)}</div>')


# class _Tree:
#     """회의록 트리 한 번 그리기 (지금 위치 강조 · 펼친 노드 기억)."""

#     def __init__(self) -> None:
#         self.s = get_state()
#         self.current, _ = arc.node_of_location(self.s)

#     def folder(self, node: str, label: str, level: int, count: int | None = None, *, new: bool = False) -> bool:
#         """폴더 줄. 펼쳐져 있으면 True (호출한 쪽이 자식을 그림).

#         - 화살표(> / v): 누르면 바로 펼치기/접기만 (화면 이동 없음)
#         - 이름: 그 화면으로 이동 + 펼치기
#         """
#         is_open = node in self.s.tree_open
#         trail = f'<span class="nav-trail count">{count}</span>' if count is not None else ""
#         if new:
#             trail += '<span class="nav-trail newdot tree"></span>'
#         on = node == self.current
#         key = safe_key(node)
#         with st.container(key=f"navrow-tree{level}-{key}", gap=None):
#             if trail:
#                 markup(trail)
#             button(label, key=f"tree_{key}", variant="treeon" if on else "tree", on_click=actions.tree_click,
#                    args=(node,), width="stretch")
#             button("", key=f"twisty_{key}", variant="twisty", on_click=actions.tree_toggle, args=(node,),
#                    icon=":material/expand_more:" if is_open else ":material/chevron_right:")
#         return is_open

#     def leaf(self, node: str, label: str, level: int, count: int, *, new: bool = False) -> None:
#         """맨 아래 폴더 줄 (10월 4주차 · 프로젝트 폴더). 회의 파일은 트리에 펼치지 않고, 누르면 폴더 화면으로."""
#         trail = f'<span class="nav-trail count">{count}</span>'
#         if new:
#             trail += '<span class="nav-trail newdot tree"></span>'
#         key = safe_key(node)
#         with st.container(key=f"navrow-tree{level}-leaf_{key}", gap=None):
#             markup(trail)
#             button(label, key=f"tree_{key}", variant="treeon" if node == self.current else "tree",
#                    on_click=actions.tree_click, args=(node,), icon=":material/folder:", width="stretch")

#     def render(self) -> None:
#         s = self.s
#         types = repo.meeting_types()
#         fs = sel.folders(s)
#         if not self.folder("root", "회의록", 0):
#             return
#         for t in _TREE_ORDER:
#             if not self.folder(f"t:{t}", types[t].name, 1, len(arc.meeting_ids_of_type(s, t))):
#                 continue
#             # 주차별 · 월별 · 일별
#             ps = arc.periods(s, t)
#             if self.folder(f"g:{t}:period", arc.PERIOD_LABEL[t], 2, len(ps)):
#                 for p in ps:
#                     self.leaf(f"p:{t}:{p.key}", p.label, 3, len(p.meeting_ids))
#             # 프로젝트별
#             fids = sel.folder_ids_of_type(s, t)
#             if self.folder(f"g:{t}:project", "프로젝트별", 2, len(fids)):
#                 for fid in fids:
#                     f = fs[fid]
#                     notes = sel.folder_note_ids(s, fid)  # 나에게 보이는 회의만
#                     self.leaf(f"f:{t}:{fid}", f.name, 3, len(notes), new=f.is_new)


# def render_sidebar() -> None:
#     s = get_state()
#     me = sel.me()
#     with st.sidebar, st.container(key="sidebar"):
#         with st.container(key="sb-logo-link", gap=None):  # 로고를 누르면 새 회의록으로
#             markup(f'<div class="sb-logo">{brand_logo(20)}</div>')
#             button("새 회의록으로", key="logo_home", variant="ghost", on_click=actions.open_new_note)
#         with st.container(key="sidebar-groups", gap=None):
#             with st.container(key="navgroup-main"):
#                 _nav_item("새 회의록", "compose", on=s.screen == "compose" and not s.not_found, on_click=actions.open_new_note,
#                           icon_name=":material/edit_square:")

#             with st.container(key="navgroup-meetings", gap=None):
#                 _Tree().render()

#             with st.container(key="navgroup-share"):
#                 _caption("공유")
#                 _nav_item("공유한 회의", "sent", on=s.screen == "sent", on_click=actions.go, args=("sent",),
#                           icon_name=":material/send:",
#                           trail_html=f'<span class="nav-trail count">{len(s.sent_shares)}</span>')
#                 _nav_item("공유 받은 회의", "recv", on=s.screen == "recv", on_click=actions.go, args=("recv",),
#                           icon_name=":material/inbox:",
#                           trail_html=f'<span class="nav-trail badge">{len(repo.received_meeting_ids())}</span>')

#             with st.container(key="navgroup-integ"):
#                 _caption("연동")
#                 warn = sel.has_integration_warning(s)
#                 _nav_item("외부 연동", "mcp", on=s.screen == "mcp", on_click=actions.go, args=("mcp",),
#                           icon_name=":material/power:",
#                           trail_html='<span class="nav-trail dot" title="확인이 필요한 연동이 있어요"></span>' if warn else "")

#         with st.container(key="sidebar-user", horizontal=True, vertical_alignment="center", gap="small", wrap=False):
#             markup(
#                 f'<div class="me-card">{avatar(me.initial, size=30, accent=True)}'
#                 f'<span class="who"><b>{esc(me.name)}</b><span>{esc(me.team)}</span></span></div>'
#             )
#             button("로그아웃", key="logout", variant="ghost", on_click=actions.logout)


from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.icons import brand_logo
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

    def folder(self, node: str, label: str, level: int, count: int | None = None, *, new: bool = False) -> bool:
        """폴더 줄. 펼쳐져 있으면 True (호출한 쪽이 자식을 그림).

        - 화살표(> / v): 누르면 바로 펼치기/접기만 (화면 이동 없음)
        - 이름: 그 화면으로 이동 + 펼치기
        """
        is_open = node in self.s.tree_open
        trail = f'<span class="nav-trail count">{count}</span>' if count is not None else ""
        if new:
            trail += '<span class="nav-trail newdot tree"></span>'
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

    def leaf(self, node: str, label: str, level: int, count: int, *, new: bool = False) -> None:
        """맨 아래 폴더 줄 (10월 4주차 · 프로젝트 폴더). 회의 파일은 트리에 펼치지 않고, 누르면 폴더 화면으로."""
        trail = f'<span class="nav-trail count">{count}</span>'
        if new:
            trail += '<span class="nav-trail newdot tree"></span>'
        key = safe_key(node)
        with st.container(key=f"navrow-tree{level}-leaf_{key}", gap=None):
            markup(trail)
            button(label, key=f"tree_{key}", variant="treeon" if node == self.current else "tree",
                   on_click=actions.tree_click, args=(node,), icon=":material/folder:", width="stretch")

    def render(self) -> None:
        s = self.s
        types = repo.meeting_types()
        fs = sel.folders(s)
        if not self.folder("root", "회의록", 0):
            return
        for t in _TREE_ORDER:
            if not self.folder(f"t:{t}", types[t].name, 1, len(arc.meeting_ids_of_type(s, t))):
                continue
            # 주차별 · 월별 · 일별
            ps = arc.periods(s, t)
            if self.folder(f"g:{t}:period", arc.PERIOD_LABEL[t], 2, len(ps)):
                for p in ps:
                    self.leaf(f"p:{t}:{p.key}", p.label, 3, len(p.meeting_ids))
            # 프로젝트별
            fids = sel.folder_ids_of_type(s, t)
            if self.folder(f"g:{t}:project", "프로젝트별", 2, len(fids)):
                for fid in fids:
                    f = fs[fid]
                    notes = sel.folder_note_ids(s, fid)  # 나에게 보이는 회의만
                    self.leaf(f"f:{t}:{fid}", f.name, 3, len(notes), new=f.is_new)


def _admin_sidebar() -> None:
    """관리자 계정: 관리 메뉴 3개 + 관리자 카드."""
    s = get_state()
    me = sel.admin_profile(s)
    with st.sidebar, st.container(key="sidebar"):
        with st.container(key="sb-logo-link", gap=None):  # 로고를 누르면 비용 관리로
            markup(f'<div class="sb-logo">{brand_logo(20)}<span class="sb-admin-tag">관리자</span></div>')
            button("비용 관리로", key="logo_home", variant="ghost", on_click=actions.go, args=("adm_cost",))
        with st.container(key="sidebar-groups", gap=None):
            with st.container(key="navgroup-admin"):
                _caption("관리")
                _nav_item("비용 관리", "adm_cost", on=s.screen == "adm_cost" and not s.not_found, on_click=actions.go,
                          args=("adm_cost",), icon_name=":material/payments:")
                _nav_item("구성원 관리", "adm_members", on=s.screen == "adm_members", on_click=actions.go,
                          args=("adm_members",), icon_name=":material/group:",
                          trail_html=f'<span class="nav-trail count">{len(sel.admin_members(s))}</span>')
                warn = sel.has_integration_warning(s)
                _nav_item("MCP 관리", "adm_mcp", on=s.screen == "adm_mcp", on_click=actions.go, args=("adm_mcp",),
                          icon_name=":material/hub:",
                          trail_html='<span class="nav-trail dot" title="확인이 필요한 연결이 있어요"></span>' if warn else "")
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
        with st.container(key="sb-logo-link", gap=None):  # 로고를 누르면 새 회의록으로
            markup(f'<div class="sb-logo">{brand_logo(20)}</div>')
            button("새 회의록으로", key="logo_home", variant="ghost", on_click=actions.open_new_note)
        with st.container(key="sidebar-groups", gap=None):
            with st.container(key="navgroup-main"):
                _nav_item("새 회의록", "compose", on=s.screen == "compose" and not s.not_found, on_click=actions.open_new_note,
                          icon_name=":material/edit_square:")

            with st.container(key="navgroup-meetings", gap=None):
                _Tree().render()

            with st.container(key="navgroup-share"):
                _caption("공유")
                _nav_item("공유한 회의", "sent", on=s.screen == "sent", on_click=actions.go, args=("sent",),
                          icon_name=":material/send:",
                          trail_html=f'<span class="nav-trail count">{len(s.sent_shares)}</span>')
                _nav_item("공유 받은 회의", "recv", on=s.screen == "recv", on_click=actions.go, args=("recv",),
                          icon_name=":material/inbox:",
                          trail_html=f'<span class="nav-trail badge">{len(repo.received_meeting_ids())}</span>')

        with st.container(key="sidebar-user", horizontal=True, vertical_alignment="center", gap="small", wrap=False):
            markup(
                f'<div class="me-card">{avatar(me.initial, size=30, accent=True)}'
                f'<span class="who"><b>{esc(me.name)}</b><span>{esc(me.team)}</span></span></div>'
            )
            button("로그아웃", key="logout", variant="ghost", on_click=actions.logout)
