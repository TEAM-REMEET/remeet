"""회의노트 — Streamlit 진입점.

실행:  streamlit run app.py

흐름: 상태 준비 → 전역 CSS → URL 라우팅(st.navigation) → (로그인 전) 로그인 화면
                                                    → (로그인 후) 사이드바 · 상단바 · 알림 · 현재 페이지 · 열린 모달
"""

from __future__ import annotations

import streamlit as st

from components.dialogs import render_active_dialog
from components.live import watch_notifications
from components.sidebar import render_sidebar
from components.toast import render_data_errors, render_toast
from components.topbar import render_topbar
from config import APP_ICON, APP_TITLE, LOGIN_COOKIE
from data import api
from data import repository as repo
from state import actions, delivery, export, router
from state.store import get_state, reset_state
from ui.theme import inject_css
from utils import browser
from views import ROUTES, login, not_found

st.set_page_config(page_title=f"{APP_TITLE} 프로토타입", page_icon=APP_ICON, layout="wide",
                   initial_sidebar_state="expanded")


def main() -> None:
    state = get_state()
    delivery.sync(state)  # 진행 중이던 전달이 끝났으면 상태 확정
    export.sync(state)  # 진행 중이던 외부 도구 전송이 끝났으면 실패 기록 · 상태 확정
    inject_css()
    js_host = st.container(key="js-host")  # 스크립트 전용 숨은 영역 (레이아웃에 영향 없음)
    js_host.html("<span></span>")  # 항상 내용이 있어야 영역이 생겼다 사라지지 않음
    browser.set_host(js_host)
    # 새 알림 지켜보기 전용 숨은 영역 (몇 초마다 다시 그려지는 영역이라 js-host 와 따로 둬서 위치가 바뀌지 않게)
    live_host = st.container(key="live-host")

    # 새로고침하면 Streamlit 세션이 새로 시작되므로, 쿠키의 출입증(토큰)으로 로그인을 되살림
    cookie = st.context.cookies.get(LOGIN_COOKIE)
    if not state.logged_in and not state.logged_out and cookie:
        try:
            actions.start_session(cookie, repo.me(cookie), restore=True)
        except api.ApiError:
            pass  # 만료 · 위조 · 백엔드 꺼짐 → 로그인 화면

    # URL 라우팅: 화면마다 st.Page 하나. 메뉴는 직접 만든 사이드바를 쓰므로 기본 메뉴는 숨김
    pages = router.build_pages(ROUTES)
    current = st.navigation(list(pages.values()), position="hidden")
    router.check_not_found(pages)  # 처음 들어온 주소가 없는 주소면 404 화면으로

    if not state.logged_in:
        if state.logged_out:
            browser.clear_cookie(LOGIN_COOKIE)
            router.apply_pending(pages)  # 로그아웃 → 주소도 "/" 로 되돌림
        login.render()
        return
    router.apply_pending(pages)  # 버튼으로 예약된 이동 → URL 변경 (브라우저 기록에 남음)
    router.sync_from_url(current.url_path)  # URL → 상태 (뒤로가기 포함)
    router.guard(current.url_path)  # 관리자는 관리자 화면만, 사용자는 관리자 화면 못 봄
    if not state.cookie_written:  # 로그인 후 한 번만 쿠키 기록 (화면 이동이 끝난 뒤라야 스크립트가 실행됨)
        browser.set_cookie(LOGIN_COOKIE, state.token)
        state.cookie_written = True

    render_sidebar()
    render_topbar()
    with live_host:
        watch_notifications()  # 다른 계정이 만든 새 알림 (관리자 ↔ 사용자) · 숨은 영역에서 몇 초마다 확인
    if state.is_admin:
        actions.check_budget_alert()  # 이번 달 예산 알림 기준을 넘으면 관리자 알림 (한 번만)
    with st.container(key="content", gap="medium"):
        render_toast()
        render_data_errors()  # 회의 · 프로젝트를 백엔드에서 못 불러왔으면 이유 안내 (빈 화면만 보이지 않게)
        if state.not_found:
            not_found.render()  # 없는 주소 → 404
        else:
            current.run()
    render_active_dialog()


try:
    main()
except api.ApiError as e:  # 화면을 그리다 백엔드 호출이 실패함
    if e.status == 401:  # 로그인 만료 · 서명 키 변경 → 다시 로그인
        reset_state(logged_out=True, login_error="로그인이 만료됐어요. 다시 로그인해 주세요.")
        st.rerun()
    st.error(f"데이터를 불러오지 못했어요: {e}")
