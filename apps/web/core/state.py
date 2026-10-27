"""세션 상태 관리.

모든 화면은 이 모듈의 함수로만 상태를 읽고 바꿉니다.
데이터는 브라우저 세션 동안만 유지되는 더미데이터입니다.
"""
from __future__ import annotations

import copy

import streamlit as st

from data.seed import INTEGRATIONS, MEMBERS, NOTIFICATIONS, seed_meetings

DEFAULT_SETTINGS = {
    "sync": True, "sync_every": "15분",
    "remind": {"before1": True, "today": True, "overdue": True}, "remind_channel": "앱 알림 + 이메일",
    "graph_at": "10.22 15:10", "synced_at": "15:12",
}


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("user", None)
    ss.setdefault("route", {"page": "main", "args": {}})
    ss.setdefault("meetings", seed_meetings())
    ss.setdefault("members", copy.deepcopy(MEMBERS))
    ss.setdefault("integrations", copy.deepcopy(INTEGRATIONS))
    ss.setdefault("notifications", copy.deepcopy(NOTIFICATIONS))
    ss.setdefault("settings", copy.deepcopy(DEFAULT_SETTINGS))
    ss.setdefault("last_login", "2026.10.21 18:02")
    ss.setdefault("flash", [])


def reset_demo() -> None:
    ss = st.session_state
    ss.meetings = seed_meetings()
    ss.members = copy.deepcopy(MEMBERS)
    ss.integrations = copy.deepcopy(INTEGRATIONS)
    ss.notifications = copy.deepcopy(NOTIFICATIONS)
    ss.settings = copy.deepcopy(DEFAULT_SETTINGS)
    flash("데모 데이터를 처음 상태로 되돌렸어요.")


# ---- 읽기 -----------------------------------------------------------------

def meetings() -> list[dict]:
    return st.session_state.meetings


def members() -> list[dict]:
    return st.session_state.members


def integrations() -> dict:
    return st.session_state.integrations


def notifications() -> list[dict]:
    return st.session_state.notifications


def settings() -> dict:
    return st.session_state.settings


def get_meeting(meeting_id: str | None) -> dict | None:
    return next((m for m in meetings() if m["id"] == meeting_id), None)


# ---- 쓰기 -----------------------------------------------------------------

def add_meeting(meeting: dict) -> None:
    st.session_state.meetings.insert(0, meeting)


def remove_meeting(meeting_id: str) -> None:
    st.session_state.meetings = [m for m in meetings() if m["id"] != meeting_id]


def push_notification(kind: str, text: str, meeting_id: str, time: str) -> None:
    from utils.text import new_id
    st.session_state.notifications.insert(0, {"id": new_id("nt"), "kind": kind, "text": text, "meeting_id": meeting_id, "time": time, "read": False})


# ---- 안내 메시지(토스트) ----------------------------------------------------
# 콜백 안에서 남긴 메시지를 다음 렌더 때 보여줍니다.

def flash(message: str, icon: str = "✅") -> None:
    st.session_state.flash.append((message, icon))


def show_flash() -> None:
    for message, icon in st.session_state.flash:
        st.toast(message, icon=icon)
    st.session_state.flash = []


# ---- 분석 진행 ---------------------------------------------------------------

def tick_analysis() -> list[dict]:
    """분석 중인 회의를 경과 시간만큼 진행시키고, 끝난 회의는 알림을 남깁니다."""
    from services.analysis import advance_processing
    from utils.dates import now_time
    finished = advance_processing(meetings())
    for m in finished:
        push_notification("done", f"‘{m['title']}’ 분석이 끝났어요. 검토해 주세요.", m["id"], now_time())
    return finished
