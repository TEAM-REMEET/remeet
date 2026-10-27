"""연동 설정: 외부 도구 연결, 업무 상태 자동 동기화·기한 리마인더(Phase 2)."""
from __future__ import annotations

import streamlit as st

from core import auth
from core.state import flash, integrations, settings
from data.constants import INTEG_STATUS
from ui.components import badge, html, phase
from ui.layout import page_head, panel, panel_head
from utils.text import esc

SAFETY = [
    "확정한 결과만, 사람이 승인한 뒤에만 보내요.",
    "AI는 허용된 전송 작업만 할 수 있고, 회의록 내용으로 다른 도구 작업을 실행하지 않아요.",
    "같은 확정 버전은 대상마다 한 번만 보내고, 실패한 대상만 다시 보내요.",
]


def _set(target: str, status: str) -> None:
    info = integrations()[target]
    info["status"] = status
    if status == "connected" and not info["account"]:
        info["account"] = "사내 계정"
    flash(f"{info['name']} " + ("연결했어요." if status == "connected" else "연결을 해제했어요."))


def _tool_row(key: str, info: dict, can: bool) -> None:
    label, kind = INTEG_STATUS[info["status"]]
    with st.container(key=f"tr_ig_{key}", horizontal=True, vertical_alignment="center", gap="medium"):
        html(f'<span class="ig-mark">{esc(info["name"][0].upper())}</span>', width="content")
        err = '<div class="small c-err" style="margin-top:2px">인증이 만료되어 전송이 실패할 수 있어요. 다시 연결해 주세요.</div>' if info["status"] == "expired" else ""
        html(f'<div><span class="fw6" style="font-size:15px">{esc(info["name"])}</span> {phase(2) if info["phase"] == 2 else ""} {badge(label, kind)}</div>'
             f'<div class="small muted" style="margin-top:2px">{esc(info["desc"])}' + (f' · {esc(info["account"])}' if info["account"] else "") + f"</div>{err}")
        if info["status"] == "connected":
            with st.container(key=f"sm_off_{key}", width="content"):
                st.button("연결 해제", key=f"ig_off_{key}", disabled=not can, on_click=_set, args=(key, "disconnected"))
        elif info["status"] == "expired":
            with st.container(key=f"line_on_{key}", width="content"):
                st.button("다시 연결", key=f"ig_on_{key}", disabled=not can, on_click=_set, args=(key, "connected"))
        else:
            with st.container(key=f"sm_on_{key}", width="content"):
                st.button("연결하기", key=f"ig_on_{key}", type="primary", disabled=not can, on_click=_set, args=(key, "connected"))


def _side_head(title: str) -> None:
    html(f'<div class="p-h"><span class="tt">{title}</span>{phase(2)}</div>')


def render() -> None:
    page_head("연동 설정", "확정한 회의 결과를 보낼 외부 도구를 연결합니다. 인증 정보는 암호화해 보관하고 화면이나 로그에 표시하지 않아요.", ["홈", "연동 설정"])
    can = auth.is_admin() or auth.user()["role"] == "organizer"
    if not can:
        st.info("연동 설정은 회의 주관자와 시스템 관리자만 바꿀 수 있어요.")

    left, right = st.columns([2.75, 1], gap="medium")
    with left, panel("ig_tools", flush=True):
        html('<div class="pbar"><span class="tt">외부 도구</span><span class="small muted">조직 · 데모컴퍼니</span></div>')
        with st.container(key="ig_tools_body"):
            for key, info in integrations().items():
                _tool_row(key, info, can)

    s = settings()
    with right:
        with panel("ig_sync"):
            _side_head("업무 상태 자동 동기화")
            s["sync"] = st.checkbox("Jira 이슈 상태를 액션 아이템에 반영", value=s["sync"], disabled=not can, key="ig_sync")
            options = ["5분", "15분", "1시간"]
            s["sync_every"] = st.selectbox("확인 주기", options, index=options.index(s["sync_every"]), disabled=not (can and s["sync"]), key="ig_every")
        with panel("ig_remind"):
            _side_head("기한 리마인더")
            r = s["remind"]
            r["before1"] = st.checkbox("기한 1일 전", value=r["before1"], disabled=not can, key="ig_r1")
            r["today"] = st.checkbox("기한 당일 오전 9시", value=r["today"], disabled=not can, key="ig_r2")
            r["overdue"] = st.checkbox("기한이 지나면 매일", value=r["overdue"], disabled=not can, key="ig_r3")
            channels = ["앱 알림", "앱 알림 + 이메일", "앱 알림 + Slack DM"]
            s["remind_channel"] = st.selectbox("받는 곳", channels, index=channels.index(s["remind_channel"]), disabled=not can, key="ig_ch")
        with panel("ig_safety"):
            panel_head("전송 안전장치")
            html('<ul class="bul">' + "".join(f"<li>{esc(x)}</li>" for x in SAFETY) + "</ul>")
        with panel("ig_data"):
            _side_head("데이터 보관")
            html('<p class="small muted" style="margin:0;line-height:1.6">회의 데이터는 조직 단위로 분리해 보관하고, 회의마다 공개 범위(참석자 · 팀 전체 · 주관자만)를 정할 수 있어요.</p>')
