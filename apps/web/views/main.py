"""메인(로그인 · 서비스 소개)."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import meetings, members, notifications
from data.seed import DEMO_PASSWORD, USERS
from services.rules import member_name
from ui.components import html, logo_html
from ui.layout import tap
from ui.landing import feature_sections, help_band, hero_left
from utils.text import esc


def _fill_demo(user_id: str) -> None:
    st.session_state.login_id = user_id
    st.session_state.login_pw = DEMO_PASSWORD
    st.session_state.login_error = ""


def _submit() -> None:
    ok, msg = auth.check_login(st.session_state.get("login_id", ""), st.session_state.get("login_pw", ""))
    st.session_state.login_error = msg
    if ok:
        auth.login(st.session_state.login_id.strip())


def _login_card() -> None:
    with st.container(key="login_card"):
        html(f'<div class="card-head">{logo_html()}<p>로그인</p><h2>업무를 이어서 시작하세요</h2></div>')
        st.text_input("아이디", key="login_id", placeholder="아이디 (사내 계정)", label_visibility="collapsed")
        st.text_input("비밀번호", key="login_pw", type="password", placeholder="비밀번호", label_visibility="collapsed", on_change=_submit)
        if st.session_state.get("login_error"):
            st.error(st.session_state.login_error)
        c1, c2 = st.columns(2, vertical_alignment="center")
        c1.checkbox("로그인 상태 유지", key="login_keep")
        c2.button("비밀번호 재설정", key="pw_reset", type="tertiary", use_container_width=True)
        st.button("로그인", key="login_btn", type="primary", use_container_width=True, on_click=_submit)
        html('<div class="or">또는</div>')
        st.button("🔒 회사 계정(SSO)으로 로그인", key="sso_btn", use_container_width=True, on_click=auth.login, args=("seoyeon.lee", "SSO"))
        st.caption("데모 계정으로 입력 · 비밀번호 remeet")
        cols = st.columns(len(USERS), gap="small")
        for col, u in zip(cols, USERS):
            name = member_name(members(), u["member_id"])
            with col, tap(f"demo_{u['id']}", _fill_demo, (u["id"],), label=u["desc"]):
                html(f'<div class="demo"><span class="avatar">{esc(name[0])}</span><div><strong>{esc(name)}</strong><span>{esc(u["role_label"])}</span></div></div>')
        st.caption("처음 사용하시나요? 관리자에게 초대를 요청하세요.")


def _user_card() -> None:
    me, user = auth.me(), auth.user()
    review = sum(1 for m in meetings() if m["organizer"] == me["id"] and m["status"] == "REVIEW_PENDING")
    unread = sum(1 for n in notifications() if not n["read"])
    with st.container(key="login_card"):
        html(f'<div class="card-head">{logo_html()}<p>로그인됨</p><h2>{esc(me["name"])} 님, 다시 오셨네요</h2>'
             f'<div class="small muted">{esc(me["dept"])} · {esc(user["role_label"])} · 최근 로그인 {esc(st.session_state.last_login)}</div></div>')
        c = st.columns(2)
        c[0].metric("검토 대기", f"{review}건")
        c[1].metric("새 알림", f"{unread}건")
        st.button("업무 화면으로 이동", key="go_app", type="primary", use_container_width=True, on_click=router.go, args=("dashboard",))
        st.button("＋ 새 회의 등록", key="go_register", use_container_width=True, on_click=router.go, args=("register",))
        st.button("로그아웃", key="main_logout", type="tertiary", use_container_width=True, on_click=auth.logout)


def render() -> None:
    with st.container(key="bleed_hero"):
        left, right = st.columns([7, 4], gap="large", vertical_alignment="center")
        with left:
            hero_left()
        with right:
            _user_card() if auth.user() else _login_card()
    feature_sections()
    help_band()
