"""새 회의록 작성 화면의 선택 모달: 주관자 · 참석자 · 발화자.

(회의 종류 · 프로젝트는 모달 없이 작성 화면에서 바로 고름 — views/compose.py)
- 주관자: 팀을 고르면 그 팀 사람들이 나오고, 1명 선택
- 참석자: 팀을 고르면 그 팀 사람들이 나오고, 여러 명 선택 (다른 팀으로 바꿔도 고른 사람은 유지)
「선택」을 눌러야 작성 화면에 반영되고, 취소·X 로 닫으면 그대로입니다.
"""

from __future__ import annotations

import functools

import streamlit as st

from components.org_people import org_people_picker
from state.store import me_id
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from utils.text import esc, md_escape

def _footer(kind: str, label: str, *, disabled: bool = False) -> None:
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key=f"{kind}_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button(label, key=f"{kind}_ok", variant="primary", disabled=disabled):
            actions.confirm_pick(kind)
            st.rerun()


# ================================================================ 주관자 · 참석자
def _person_option(pid: str) -> str:
    p = sel.person(pid)
    role = sel.person_role(pid)
    return f"{p.name}" + (f" · {role}" if role else "") + (" (나)" if pid == me_id() else "")


# 「선택한 참석자」 상자 스크롤 (한 번만 설치되는 브라우저 스크립트)
# - 사람을 누를 때마다 상자가 새로 그려지는데, 그 즉시(화면에 그리기 전) 이전 스크롤 위치를 되돌려 놓아 삐끗거리지 않음
# - 칩이 새 줄로 넘어갈 때(줄 수가 늘 때)만 맨 아래로 내려 방금 고른 사람을 보여 줌
# - 모달을 새로 열면 맨 아래부터

def people_picker(kind: str, *, single: bool, title: str | None = None, hint: str | None = None,
                  featured: list[str] | None = None, featured_label: str = "") -> None:
    """사람 고르기 (고른 사람 칩 + 조직도 · 검색 · 최근 · 즐겨찾기). 고른 사람은 AppState.pick_draft.

    새 회의록의 주관자 · 참석자, 액션 아이템 담당자에서 같이 씁니다.
    """
    s = get_state()

    # 지금 고른 사람: 제목은 상자 위에 고정, 사람 칩만 상자 안에서 스크롤
    title = title or ("주관자" if single
                      else f"선택한 참석자 {len(s.pick_draft)}명")
    if not single and s.pick_draft:  # 칩을 누르면 해제 (참석자 고르기의 주관자는 뺄 수 없음)
        chips = ""
    elif s.pick_draft:
        chips = "".join(
            f'<span class="pick-chip">{avatar(sel.person(pid).initial, size=22, host=single)}'
            f'{esc(sel.person(pid).name)}<span class="t">{esc(sel.person(pid).team)}</span></span>'
            for pid in s.pick_draft
        )
    else:
        chips = '<span class="pick-empty">아직 고른 사람이 없어요</span>'
    # 높이가 고정된 상자 · 넘치면 상자 안에서 스크롤. 스크롤 위치는 _CHOSEN_SCROLL_JS 가 관리
    if not single and s.pick_draft:
        markup(f'<div class="pick-chosen-head">{esc(title)}</div>')
        with st.container(key="pick-chosen-btns", horizontal=True, gap="xsmall", height=104, border=False):
            for pid in s.pick_draft:
                fixed = kind == "cp_people" and pid == s.nn_host
                button(f"{md_escape(sel.person(pid).name)} :gray[{md_escape(sel.person(pid).team)}]",
                       key=f"pick_chip_{kind}_{pid}", variant="mini", disabled=fixed,
                       icon=None if fixed else ":material/close:",
                       on_click=actions.pick_person, args=(pid, False))
    else:
        markup(f'<div class="pick-chosen-head">{esc(title)}</div>'
               f'<div class="pick-chosen" data-n="{len(s.pick_draft)}"><div class="pick-chosen-in">{chips}</div></div>')

    # 사람 고르기: 조직도(본부 › 부 › 팀 › 사람) · 검색 · 최근 · 즐겨찾기 (팀 · 조직이 많아도 찾기 쉽게)
    hint = hint or ("이름을 누르면 주관자로 골라져요" if single
                    else "이름을 누르면 선택 · 다시 누르거나 위 칩을 누르면 해제" + (" · 주관자는 늘 참석해요" if kind == "cp_people" else ""))
    markup(f'<div class="pick-section-head"><b>사람 찾기</b><span class="hint">{esc(hint)}</span></div>')
    org_people_picker(kind, chosen=list(s.pick_draft), single=single, featured=featured, featured_label=featured_label,
                      on_pick=functools.partial(actions.pick_person, single=single), height=280)


@st.dialog("주관자 선택", width="medium", on_dismiss=actions.close_modal)
def host_picker_dialog() -> None:
    people_picker("cp_host", single=True)
    _footer("cp_host", "선택", disabled=not get_state().pick_draft)


@st.dialog("참석자 선택", width="medium", on_dismiss=actions.close_modal)
def people_picker_dialog() -> None:
    n = len(get_state().pick_draft)
    people_picker("cp_people", single=False)
    _footer("cp_people", f"{n}명 선택" if n else "선택 안 함")


@st.dialog("입력 내용을 확인해 주세요", width="small", on_dismiss=actions.close_modal)
def format_error_dialog() -> None:
    """1단계 「다음 단계」: 형식에 맞지 않거나 발화자가 참석자 안에 없으면 경고 창 (다음 단계로 넘어가지 않음)."""
    s = get_state()
    markup('<div class="cp-warn-dlg"><span class="cp-warn-ico">!</span>'
           f'<p>{esc(s.nn_error)}</p></div>'
           '<p class="hint" style="font-size:13px;margin-top:10px">고친 뒤 다시 「다음 단계」를 눌러 주세요.</p>')
    with st.container(horizontal=True, horizontal_alignment="right"):
        if button("확인", key="cp_warn_ok", variant="primary"):
            actions.close_modal()
            st.rerun()


def _compose_confirm_body() -> None:
    """2단계 「확정하기」 · 「검토 요청하기」를 한 번 더 확인 → 로딩 화면."""
    s = get_state()
    junior = sel.needs_reviewer(s)  # 신입이 주관자 → 고른 윗사람에게 검토 요청
    host = s.nn_host == me_id() and not junior
    who = f"검토자 {sel.name_of(s.nn_reviewer)}님" if junior else f"주관자 {sel.name_of(s.nn_host)}님"
    # 묻는 말 다음 줄에 설명
    ask, lead = (("확정하시겠어요?", "확정하면 회의록이 만들어지고 담당자 추천 → 최종 회의록 확인으로 이어져요.") if host else
                 (f"{who}에게 검토를 요청할까요?",
                  f"회의록이 만들어지고 {who}에게 검토 요청 알림이 가요. 확정은 그 사람이 해요."))
    markup(f'<div class="assign-target"><span class="assign-target-label">'
           f'{"확정할" if host else "검토를 요청할"} 회의록</span><h3>{esc(s.nn_title)}</h3></div>'
           f'<p class="confirm-lead">{esc(ask)}<br>{esc(lead)}</p>')
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="cp_confirm_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button("확정하기" if host else "검토 요청하기", key="cp_confirm_ok", variant="primary"):
            actions.compose_confirm()
            st.rerun()


@st.dialog("회의록 확정", width="small", on_dismiss=actions.close_modal)
def compose_confirm_dialog() -> None:
    _compose_confirm_body()


@st.dialog("검토 요청", width="small", on_dismiss=actions.close_modal)
def compose_review_dialog() -> None:
    _compose_confirm_body()
