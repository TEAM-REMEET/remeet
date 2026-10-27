"""로그인과 권한.

- 회의 수정·확정·전달: 회의 주관자
- 회의 열람: 공개 범위(참석자 / 팀 전체 / 주관자만)에 따라
- 구성원 프로필 수정: 시스템 관리자
"""
from __future__ import annotations

import streamlit as st

from core import router
from core.state import flash
from data.seed import DEMO_PASSWORD, USERS
from utils.dates import TODAY, now_time


def user() -> dict | None:
    return st.session_state.user


def me() -> dict | None:
    u = user()
    if not u:
        return None
    return next(m for m in st.session_state.members if m["id"] == u["member_id"])


def is_admin() -> bool:
    u = user()
    return bool(u and u["role"] == "admin")


def check_login(user_id: str, password: str) -> tuple[bool, str]:
    if not user_id.strip():
        return False, "아이디를 입력해 주세요."
    if not password:
        return False, "비밀번호를 입력해 주세요."
    u = next((x for x in USERS if x["id"] == user_id.strip()), None)
    if not u or password != DEMO_PASSWORD:
        return False, "아이디 또는 비밀번호가 맞지 않아요. 데모 계정 버튼을 눌러 입력할 수 있어요."
    return True, ""


def login(user_id: str, how: str = "") -> None:
    u = next(x for x in USERS if x["id"] == user_id)
    st.session_state.user = u
    st.session_state.last_login = f"{TODAY.year}.{TODAY.month:02d}.{TODAY.day:02d} {now_time()}"
    name = next(m["name"] for m in st.session_state.members if m["id"] == u["member_id"])
    flash(f"{name} 님, {'회사 계정으로 ' if how == 'SSO' else ''}로그인했어요.", "👋")
    router.go("dashboard")


def logout() -> None:
    st.session_state.user = None
    flash("로그아웃했어요.", "👋")
    router.go("main")


def can_view(meeting: dict) -> bool:
    u, m = user(), me()
    if not u:
        return False
    if u["role"] == "admin":
        return True
    if meeting["access"] == "organizer":
        return meeting["organizer"] == m["id"]
    if meeting["access"] == "attendees":
        return m["id"] in meeting["attendees"] or meeting["organizer"] == m["id"]
    return True


def is_owner(meeting: dict) -> bool:
    m = me()
    return bool(m and meeting["organizer"] == m["id"])
