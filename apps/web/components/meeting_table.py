"""회의 목록 표 (홈 · 폴더 공통)."""

from __future__ import annotations

import streamlit as st

from state import actions
from state.selectors import MeetingRow
from ui.buttons import button
from ui.html import markup
from ui.tags import stage_badge, tag
from utils.text import esc

COLUMNS = (2.15, 1.25, 1.0, 1.35, 1.1, 1.1)  # 관리 칸: 「외부 도구 전송」이 한 줄에 들어가게 (제목 칸에서 덜어 옴)
HEADERS = ("회의명", "회의 일시", "주관자", "진행 상태", "액션 아이템", "관리")

_BUTTON_VARIANT = {
    "review": "accentline",
    "deliver": "accentline",
    "export": "accentline",
    "retry": "dangerline",
    "progress": "secondary",
    "view": "secondary",
}


def _header() -> None:
    template = " ".join(f"{c}fr" for c in COLUMNS)
    cells = "".join(f'<span role="columnheader">{h}</span>' for h in HEADERS)
    markup(f'<div class="mtable-head" role="row" style="grid-template-columns:{template}">{cells}</div>')


def _row(row: MeetingRow, key: str) -> None:
    with st.container(key=f"mrow-{key}-{row.id}"):
        c_title, c_date, c_host, c_status, c_act, c_btn = st.columns(COLUMNS, vertical_alignment="center", gap="small")
        with c_title:
            with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
                button(row.title, key=f"{key}_open_{row.id}", variant="link", on_click=actions.open_meeting, args=(row.id,))
                if badge := stage_badge(row.stage):  # 남은 단계가 있는 회의 (검토 중 · 전달 전 …)
                    st.html(badge, width="content")
                if row.is_deep:
                    st.html(tag("심화", "deep"), width="content")
                if row.absent_shared:
                    st.html(tag("미참석 · 공유받음", "shared"), width="content")
            markup(f'<div class="cell"><span class="sub">{esc(row.sub)}</span></div>')
        with c_date:
            markup(f'<div class="cell"><span class="main">{esc(row.date_str)}</span><span class="sub">{esc(row.time)}</span></div>')
        with c_host:
            me = '<span class="me">(나)</span>' if row.is_me else ""
            # 주관자 이름 (아래 줄은 참석 인원)
            markup(f'<div class="cell"><span class="main">{esc(row.host)}{me}</span><span class="sub">참석자 {row.count}명</span></div>')
        with c_status:
            markup(f'<div class="cell status">{tag(row.status, row.status_tone)}<span class="sub">{esc(row.status_sub)}</span></div>')
        with c_act:
            markup(f'<div class="cell"><span class="main">{row.actions}건</span><span class="sub">{esc(row.action_sub)}</span></div>')
        with c_btn:
            button(row.button, key=f"{key}_act_{row.id}", variant=_BUTTON_VARIANT[row.button_kind],
                   on_click=actions.run_row_action, args=(row.id, row.button_kind))


def meeting_table(rows: list[MeetingRow], *, key: str) -> None:
    with st.container(key=f"mtable-{key}", gap=None):
        _header()
        with st.container(key=f"mtbody-{key}", gap=None):  # 표 본문만 스크롤
            for row in rows:
                _row(row, key)
