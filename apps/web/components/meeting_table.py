"""회의 목록 표 (홈 · 폴더 공통)."""

from __future__ import annotations

import streamlit as st

from state import actions
from state.selectors import MeetingRow
from ui.buttons import button
from ui.html import markup
from ui.tags import tag
from utils.text import esc

COLUMNS = (2.3, 1.25, 1.0, 1.35, 1.1, 0.95)
HEADERS = ("회의명", "회의 일시", "주관자", "진행 상태", "액션 아이템", "관리")

_BUTTON_VARIANT = {
    "review": "accentline",
    "deliver": "accentline",
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


_SECTION_DESC = {
    "left": "나에게 맡겨진 할 일이 있어요",
}
_SECTION_EMPTY = {
    "left": "내 할 일이 있는 회의가 없어요.",
}


def meeting_sections(state, ids: list[str], *, key: str) -> None:
    """내 할 일 상태별 판넬 3개 + 고른 판넬의 회의 표.

    판넬(내 할 일이 있는 회의)을 누르면 그 회의들만 표로 보여 줍니다.
    처음 들어오면 회의가 있는 첫 판넬을 엽니다. (나에게 맡겨진 할 일이 없는 회의는 판넬에 넣지 않아요)
    """
    from state import actions
    from state import selectors as sel  # selectors 가 MeetingRow 를 정의해서 함수 안에서 불러옴

    parts = {g: [i for i in ids if sel.my_todo_status(state, i) == g] for g, _ in sel.TODO_GROUPS}
    # 처음 들어오면 회의가 있는 첫 판넬 · 직접 고른 판넬은 비어 있어도 그대로 (빈 안내 표시)
    current = state.todo_group if state.todo_group in parts else next((g for g, _ in sel.TODO_GROUPS if parts[g]), "left")

    with st.container(key=f"tgpanels-{key}", horizontal=True, gap="small"):
        for gkey, title in sel.TODO_GROUPS:
            on = gkey == current
            with st.container(key=f"tgpanel-{'on' if on else 'off'}-{gkey}-{key}"):
                markup(
                    f'<div class="tgp tgp-{gkey}"><span class="tt"><span class="dot"></span>{esc(title)}</span>'
                    f'<b class="n">{len(parts[gkey])}</b><span class="desc">{esc(_SECTION_DESC[gkey])}</span></div>'
                )
                button(title, key=f"tg_{key}_{gkey}", variant="secondary", on_click=actions.update,
                       kwargs={"todo_group": gkey}, width="stretch")

    if parts[current]:
        meeting_table([sel.meeting_row(state, i) for i in parts[current]], key=f"{key}-{current}")
    else:
        markup(f'<div class="empty" style="padding:36px 24px"><span class="empty-text">{esc(_SECTION_EMPTY[current])}</span></div>')
