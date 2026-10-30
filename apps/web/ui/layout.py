"""헤더·푸터·페이지 제목·패널 등 화면 뼈대."""
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

from core import auth, router
from core.state import notifications, reset_demo
from ui.components import html
from utils.text import esc

NOTI_KIND = {"overdue": "지연", "soon": "기한 임박", "fail": "전달 실패", "done": "분석 완료", "info": "안내"}


# ---------------------------------------------------------------- 패널 · 클릭 영역

def panel(name: str, flush: bool = False):
    """흰 패널. flush=True면 안쪽 여백 없이(표·탭용)."""
    return st.container(key=f"{'panel0' if flush else 'panel'}_{name}")


def panel_head(title: str, side: str = "") -> None:
    html(f'<div class="p-h"><span class="tt">{title}</span><span class="side">{side}</span></div>')


def panel_bar(title: str, side: str = "") -> None:
    html(f'<div class="pbar"><span class="tt">{title}</span><span class="small muted">{side}</span></div>')


@contextmanager
def tap(name: str, on_click, args: tuple = (), kwargs: dict | None = None, label: str = "열기"):
    """영역 전체를 누를 수 있는 블록. 안의 내용을 그린 뒤 투명 버튼으로 덮습니다."""
    with st.container(key=f"tap_{name}") as box:
        yield box
        st.button(label, key=f"tapbtn_{name}", on_click=on_click, args=args, kwargs=kwargs or {})


# ---------------------------------------------------------------- 헤더

def _open_notification(n: dict) -> None:
    n["read"] = True
    router.go("review", meeting_id=n["meeting_id"])


def _on_search() -> None:
    q = st.session_state.get("search_header", "").strip()
    if q:
        router.go("search", q=q)


def app_header() -> None:
    me, user = auth.me(), auth.user()
    active = router.NAV_GROUP.get(router.page(), router.page())
    with st.container(key="bleed_header", horizontal=True, vertical_alignment="center"):
        st.button("RE:MEET", key="nav_brand", type="tertiary", on_click=router.go, args=("main",), help="메인으로")
        with st.container(key="app_nav", horizontal=True, vertical_alignment="center", width="content"):
            for key, label in router.APP_NAV:
                st.button(label, key=f"{'navon' if active == key else 'nav'}_{key}", type="tertiary", on_click=router.go, args=(key,))
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
            if router.page() != "dashboard":
                st.text_input("검색", key="search_header", placeholder="회의·결정사항·업무 검색", label_visibility="collapsed",
                              on_change=_on_search, width=240, icon=":material/search:")
            unread = [n for n in notifications() if not n["read"]]
            with st.container(key="nav_bell", width="content"):
                with st.popover(str(len(unread)) if unread else " ", icon=":material/notifications:"):
                    st.markdown("**알림** · 기한 리마인더")
                    if not notifications():
                        st.caption("새 알림이 없어요.")
                    for n in notifications():
                        st.button(f"[{NOTI_KIND[n['kind']]}] {n['text']}", key=f"noti_{n['id']}", on_click=_open_notification, args=(n,),
                                  type="secondary" if not n["read"] else "tertiary", use_container_width=True)
            html(f'<div class="who"><span class="avatar">{esc(me["name"][0])}</span><strong>{esc(me["name"])}</strong> 님 · {esc(user["role_label"])}</div>', width="content")
            st.button("로그아웃", key="nav_logout", on_click=auth.logout, type="tertiary")


def public_header() -> None:
    with st.container(key="bleed_header", horizontal=True, vertical_alignment="center"):
        st.button("RE:MEET", key="nav_brand", type="tertiary", on_click=router.go, args=("main",))
        with st.container(key="pub_links", horizontal=True, vertical_alignment="center", width="content", gap="medium"):
            for label in ("서비스 소개", "이용 방법", "고객센터"):
                st.button(label, key=f"pub_{label}", type="tertiary")
        with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
            if auth.user():
                st.button("업무 화면으로", key="pub_go_app", type="primary", on_click=router.go, args=("dashboard",))
            else:
                html('<div class="who">데모 계정 비밀번호 <strong>remeet</strong></div>', width="content")


def footer() -> None:
    with st.container(key="bleed_footer", horizontal=True, vertical_alignment="center"):
        html('<span style="color:var(--fg-2);font-weight:600">개인정보처리방침</span>', width="content")
        html('<span>이용약관</span>', width="content")
        html('<span>서비스 상태</span>', width="content")
        html('<span>고객센터 평일 09:00–18:00</span>', width="content")
        st.button("데모 데이터 초기화", key="reset_demo", on_click=reset_demo, type="tertiary")
        with st.container(horizontal=True, horizontal_alignment="right"):
            html('<span>© 2026 RE:MEET · 프로토타입(더미데이터)</span>', width="content")


# ---------------------------------------------------------------- 페이지 제목

def page_head(title: str, desc: str = "", crumb: list[str] | None = None, badge_html: str = "") -> None:
    crumb_html = f'<div class="crumb">{" › ".join(esc(c) for c in crumb)}</div>' if crumb else ""
    desc_html = f'<p class="page-desc">{esc(desc)}</p>' if desc else ""
    html(f'{crumb_html}<div class="page-title">{badge_html}<h1>{esc(title)}</h1></div>{desc_html}')


def head_row():
    """제목(왼쪽)과 버튼(오른쪽)을 나란히 놓는 두 칸."""
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    return left, right.container(horizontal=True, horizontal_alignment="right", vertical_alignment="bottom")
