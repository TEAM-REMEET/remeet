"""로그인 화면 = 랜딩 페이지 (사이드바 없이 전체 화면).

1) 히어로: (왼쪽) 서비스 한 줄 소개 · 둘러보기  /  (오른쪽) 로그인 폼   — 첫 화면 높이에 딱 맞춤
2) 아래로 스크롤하면 서비스 소개 섹션 (components/landing.py)
"""

from __future__ import annotations

import streamlit as st

from components.landing import render_landing
from config import DEMO_EMAIL, DEMO_PASSWORD
from state import actions
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from ui.icons import brand_logo, icon

# 히어로의 가치 3줄
_VALUES = (
    ("lines", "원문 근거와 함께 정리"),
    ("user-check", "담당자 추천 · 사람이 결정"),
    ("wiki", "폴더별로 쌓이는 회의 위키"),
)

_PREVIEW = (
    ("안건 1", "신규 온보딩 플로우", "결정", "#1D6B43", "#E7F5EC"),
    ("안건 2", "결제 오류 재발", "길어짐", "#7A5200", "#FFF5D9"),
    ("안건 3", "11월 릴리즈 일정", "보류", "#474C55", "#F0F1F3"),
)

_HIDE_SIDEBAR = '<style>section[data-testid="stSidebar"]{display:none !important}</style>'


def _hero() -> None:
    values = "".join(f'<li>{icon(ic, 16, "#F37321", 2)}{t}</li>' for ic, t in _VALUES)
    preview = "".join(
        f'<div class="ag"><span class="n">{n}</span>{t}<span class="s" style="color:{fg};background:{bg}">{s}</span></div>'
        for n, t, s, fg, bg in _PREVIEW
    )
    markup(
        '<div class="hero">'
        f'<div class="login-brand">{brand_logo(30)}</div>'
        '<div class="hero-main">'
        '<span class="eyebrow">텍스트 회의록 정리 · 담당자 추천 · 회의 위키</span>'
        "<h1>회의가 끝나면,<br>할 일과 맥락이 <em>남아요</em></h1>"
        "<p>회의록을 올리면 안건·결정·할 일로 나누고,<br>담당자 추천부터 결과 전달까지 이어서 처리해요.</p>"
        f'<ul class="hero-values">{values}</ul>'
        '<div class="login-preview" aria-hidden="true">'
        '<div class="top"><span>주간 회의 — 10월 4주차</span><span class="mine">내 할 일 2</span></div>'
        f"{preview}</div>"
        "</div>"
        '<a class="scroll-cue" href="#overview">RE:MEET 둘러보기<span>↓</span></a>'
        "</div>"
    )


def _fill_demo() -> None:
    st.session_state["login_email"] = DEMO_EMAIL
    st.session_state["login_pw"] = DEMO_PASSWORD
    get_state().login_error = False


def _submit() -> None:
    actions.login(st.session_state.get("login_email", ""), st.session_state.get("login_pw", ""))


def _form() -> None:
    markup('<div class="login-title"><h2 id="login-title">로그인</h2><p>회사 계정으로 로그인하세요</p></div>')
    with st.form("login", border=False, enter_to_submit=True):
        st.text_input("이메일", key="login_email", placeholder="name@company.com", autocomplete="username")
        st.text_input("비밀번호", key="login_pw", type="password", placeholder="비밀번호", autocomplete="current-password")
        if get_state().login_error:
            markup('<p class="login-err" role="alert">이메일 또는 비밀번호가 맞지 않아요. 아래 데모 계정을 써 보세요.</p>')
        st.form_submit_button("로그인", on_click=_submit, width="stretch")
    markup('<div class="login-or"><span></span>또는<span></span></div>')
    button("데모 계정으로 바로 시작", key="demo_start", variant="secondary", on_click=actions.demo_login, width="stretch")
    with st.container(key="demo-box", horizontal=True, vertical_alignment="center"):
        markup(f'<span class="demo-info"><b>데모 계정</b><span>{DEMO_EMAIL} · {DEMO_PASSWORD}</span></span>')
        button("자동 입력", key="demo_fill", variant="mini", on_click=_fill_demo)


def render() -> None:
    st.html(_HIDE_SIDEBAR)
    with st.container(key="login-page", gap=None):  # 히어로와 소개 섹션을 틈 없이 이어 붙임
        # 1) 히어로
        with st.container(key="login"):
            left, right = st.columns([1.33, 1], gap=None)
            with left, st.container(key="login-intro", gap=None):
                _hero()
            with right, st.container(key="login-form", gap="medium"):
                _form()
        # 2) 아래로 스크롤하면 이어지는 소개 섹션
        render_landing()