"""왼쪽 사이드바: 워크스페이스, 메뉴, 폴더 트리, 사용자."""

from __future__ import annotations

import streamlit as st

from config import WORKSPACE_NAME, WORKSPACE_PLAN
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.icons import brand_logo, icon
from utils.text import esc


def _nav_item(label: str, key: str, *, on: bool, on_click, args=(), icon_name: str | None = None,
              sub: bool = False, trail_html: str = "") -> None:
    variant = ("navsubon" if on else "navsub") if sub else ("navon" if on else "nav")
    with st.container(key=f"navrow-{key}", gap=None):
        if trail_html:
            markup(trail_html)
        button(label, key=f"nav_{key}", variant=variant, on_click=on_click, args=args, icon=icon_name, width="stretch")


def _caption(text: str) -> None:
    markup(f'<div class="nav-caption">{esc(text)}</div>')


def render_sidebar() -> None:
    s = get_state()
    me = sel.me()
    with st.sidebar, st.container(key="sidebar"):
        markup(f'<div class="sb-logo">{brand_logo(20)}</div>')  
        markup(
            '<div class="ws"><span class="mark">데</span>'
            f'<span class="name"><b>{esc(WORKSPACE_NAME)}</b><span>{esc(WORKSPACE_PLAN)}</span></span>'
            f'{icon("chevrons-updown", 14, "#80858E", 2)}</div>'
        )

        with st.container(key="sidebar-groups", gap=None):
            with st.container(key="navgroup-main"):
                _nav_item("내 회의", "home", on=s.screen == "home", on_click=actions.go, args=("home",),
                          icon_name=":material/home:")
                _nav_item("내 위키", "wiki", on=s.screen == "wiki", on_click=actions.go, args=("wiki",),
                          icon_name=":material/hub:")

            with st.container(key="navgroup-meetings"):
                _caption("회의")
                for mtype in sel.TYPE_ORDER:
                    on = s.screen == "folder" and s.folder_type == mtype and s.folder_id == "all"
                    _nav_item(repo.meeting_types()[mtype].name, f"type_{mtype}", on=on,
                              on_click=actions.open_folder_type, args=(mtype,), icon_name=":material/folder:")
                    for fid in sel.folder_ids_of_type(s, mtype):
                        f = sel.folders(s)[fid]
                        trail = '<span class="nav-trail bullet"></span>'
                        if f.is_new:
                            trail += '<span class="nav-trail newdot"></span>'
                        _nav_item(f.name, f"folder_{fid}", on=s.screen == "folder" and s.folder_id == fid,
                                  on_click=actions.open_folder, args=(fid,), sub=True, trail_html=trail)

            with st.container(key="navgroup-share"):
                _caption("공유")
                _nav_item("공유한 회의", "sent", on=s.screen == "sent", on_click=actions.go, args=("sent",),
                          icon_name=":material/send:",
                          trail_html=f'<span class="nav-trail count">{len(s.sent_shares)}</span>')
                _nav_item("공유 받은 회의", "recv", on=s.screen == "recv", on_click=actions.go, args=("recv",),
                          icon_name=":material/inbox:",
                          trail_html=f'<span class="nav-trail badge">{len(repo.received_meeting_ids())}</span>')

            with st.container(key="navgroup-integ"):
                _caption("연동")
                warn = sel.has_integration_warning(s)
                _nav_item("외부 연동", "mcp", on=s.screen == "mcp", on_click=actions.go, args=("mcp",),
                          icon_name=":material/power:",
                          trail_html='<span class="nav-trail dot" title="확인이 필요한 연동이 있어요"></span>' if warn else "")

        with st.container(key="sidebar-user", horizontal=True, vertical_alignment="center", gap="small", wrap=False):
            markup(
                f'<div class="me-card">{avatar(me.initial, size=30, accent=True)}'
                f'<span class="who"><b>{esc(me.name)}</b><span>{esc(me.team)}</span></span></div>'
            )
            button("로그아웃", key="logout", variant="ghost", on_click=actions.logout)
