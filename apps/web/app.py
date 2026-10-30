"""RE:MEET — 회의록 기반 후속 업무 관리 (Streamlit 프로토타입).

실행: streamlit run app.py
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

FAVICON = Path(__file__).parent / "assets" / "logo_header.svg"   
st.set_page_config(page_title="RE:MEET", page_icon=str(FAVICON), layout="wide", initial_sidebar_state="collapsed")


from core import auth, router  
from core.state import init_state, show_flash, tick_analysis 
from ui.layout import app_header, footer, public_header  
from ui.theme import inject_css  
from views import dashboard, delivery, integrations, main, meetings as meetings_view, members, progress, register, review, search  # noqa: E402

PAGES = {
    "main": main.render,
    "dashboard": dashboard.render,
    "meetings": meetings_view.render,
    "register": register.render,
    "progress": progress.render,
    "review": review.render,
    "delivery": delivery.render,
    "search": search.render,
    "members": members.render,
    "integrations": integrations.render,
}
TITLES = {"main": "RE:MEET", "dashboard": "대시보드", "meetings": "회의 목록", "register": "새 회의 등록", "progress": "분석 진행 현황",
          "review": "결과 검토", "delivery": "결과 전달", "search": "검색 결과", "members": "구성원 관리", "integrations": "연동 설정"}


def _page(name: str):
    """URL 하나 = 화면 하나. main은 기본 주소(/)예요."""
    return st.Page(lambda: _render(name), title=f"{TITLES[name]} · RE:MEET" if name != "main" else "RE:MEET",
                   url_path=None if name == "main" else name, default=name == "main")


def _render(name: str) -> None:
    router.sync(name)
    if name != "main" and not auth.user():  
        router.go_now("main")
    if name == "main":
        public_header()
    else:
        app_header()
    show_flash()
    PAGES[name]()
    footer()


def main_app() -> None:
    init_state()
    inject_css()
    tick_analysis()
    pages = {name: _page(name) for name in PAGES}
    current = st.navigation(list(pages.values()), position="hidden")
    target = router.pending()
    if target:  
        name, args = target
        st.switch_page(pages.get(name, pages["main"]), query_params=args or None)
    current.run()


main_app()