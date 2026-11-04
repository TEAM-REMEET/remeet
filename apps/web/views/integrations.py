"""외부 연동: 회의 결과를 보낼 외부 도구 연결."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import page, page_header
from ui.tags import tag
from utils.text import esc

_STATUS = {"on": ("연결됨", "on", "연결 해제", "secondary"),
           "expired": ("인증 만료", "expired", "다시 연결", "accentline"),
           "off": ("미연결", "off", "연결하기", "primary")}


def _tools() -> None:
    s = get_state()
    cfg = repo.integrations()
    with st.container(key="integ", gap=None):
        markup(f'<div class="integ-head"><h2>외부 도구</h2><span>조직 · {esc(cfg["organization"])}</span></div>')
        with st.container(key="integ-rows", gap=None):  # 도구 목록만 스크롤
            _tool_rows(s, cfg)


def _tool_rows(s: AppState, cfg: dict) -> None:
    for t in cfg["tools"]:
        st_ = s.integrations.get(t.id, "off")
        label, tone, btn, variant = _STATUS[st_]
        desc = t.desc + (f" · {t.target}" if st_ != "off" and t.target else "")
        with st.container(key=f"irow-{t.id}", horizontal=True, vertical_alignment="center", gap="medium"):
            markup(
                f'<div style="display:flex;gap:16px;align-items:center"><span class="integ-mark">{esc(t.mark)}</span>'
                f'<span class="integ-info"><span class="nm"><b>{esc(t.name)}</b>'
                + ('<span class="pill-label">Phase 2</span>' if t.phase2 else "")
                + f'{tag(label, tone)}</span><span class="d">{esc(desc)}</span>'
                + ('<span class="warn">인증이 만료되어 전송이 실패할 수 있어요. 다시 연결해 주세요.</span>' if st_ == "expired" else "")
                + _resend_hint(s, cfg, t.id, st_)
                + "</span></div>"
            )
            button(btn, key=f"integ_{t.id}", variant=variant, on_click=actions.set_integration, args=(t.id,))


def _resend_hint(s: AppState, cfg: dict, tool_id: str, state: str) -> str:
    """갱신한 뒤: 아직 다시 보내지 않은 회의록이 있으면 그 회의록 안에서 보낼 수 있다고 알려 줌."""
    pending = [mid for mid in cfg["failed_exports"].get(tool_id, []) if f"{tool_id}:{mid}" not in s.export_resent]
    if state != "on" or not pending:
        return ""
    titles = ", ".join(f"「{repo.meeting(mid).title}」" for mid in pending)
    return f'<span class="d" style="color:#1D6B43">전송에 실패했던 회의록 {esc(titles)}에서 다시 보낼 수 있어요.</span>'


def render() -> None:
    with page("mcp"):
        page_header("외부 연동", "확정한 회의 결과를 팀이 쓰는 도구로 보내요.")
        _tools()