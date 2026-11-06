"""회의 공유하기 (주관자만).

안내 문구 바로 아래 「받는 사람」: 조직도 · 검색 · 최근 · 즐겨찾기에서 이름을 누르면 선택 · 다시 누르면 해제.
「공유하기」를 누르면 선택된 사람 모두에게 공유합니다.
"""

from __future__ import annotations

import streamlit as st

from components.org_people import org_people_picker
from state.store import me_id
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button
from ui.html import markup

_SCOPES = {"summary": "요약·결정만", "full": "원문 포함"}
_CAPTIONS = ["핵심 요약·결정·액션 아이템만 보내요. 원문은 잠겨요.", "정리된 내용과 회의록 원문 전체를 보내요."]


def _pick_people() -> None:
    """받는 사람: 조직도(본부 › 부 › 팀 › 사람) · 검색 · 최근 · 즐겨찾기에서 이름을 눌러 고르기 · 다시 누르면 해제."""
    s = get_state()
    with st.container(key="share-add", gap="small"):
        markup('<b style="font-size:13px">받는 사람</b> <span class="hint">이름 · 팀으로 찾거나 조직도를 펼쳐 이름을 누르세요 · 다시 누르면 해제</span>')
        org_people_picker("share", chosen=list(s.share_recipients), on_pick=actions.toggle_share_recipient, height=240,
                          locked={me_id(): "나"})


def _grouped_recipients() -> dict[str, list[str]]:
    """선택된 사람 → {팀 이름: [이름, ...]} (공유 기록·안내 문구용)."""
    s = get_state()
    team_of = {mb.id: t.name for t in repo.teams().values() for mb in t.members}
    grouped: dict[str, list[str]] = {}
    for pid in s.share_recipients:
        grouped.setdefault(team_of.get(pid, sel.person(pid).team), []).append(sel.person(pid).name)
    return grouped


@st.dialog("회의 공유하기", width="medium", on_dismiss=actions.close_modal)
def share_dialog() -> None:
    s = get_state()
    markup('<p class="hint" style="font-size:13px;color:#5F646D">주관자만 공유할 수 있어요. 이 회의록 한 개만 읽기 전용으로 보내고, '
           "폴더 멤버가 되는 건 아니에요.</p>")
    _pick_people()

    st.radio("공유 범위", list(_SCOPES), format_func=_SCOPES.get, captions=_CAPTIONS,
             index=list(_SCOPES).index(s.share_scope), key=f"share_scope_radio_{s.share_nonce}",
             on_change=lambda: actions.update(share_scope=st.session_state[f"share_scope_radio_{s.share_nonce}"]))

    total = len(s.share_recipients)
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="share_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        if button(f"{total}명에게 공유하기" if total else "받는 사람을 골라 주세요", key="share_ok",
                  variant="primary", disabled=total == 0):
            actions.confirm_share(_grouped_recipients())
            st.rerun()