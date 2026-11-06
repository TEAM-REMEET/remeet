"""관리자 · MCP 관리: 회의 결과를 보내는 외부 도구(MCP 서버) 연결 + 전송 실패 기록.

전송 실패 기록 (기술 이슈만 · 단순 이슈는 사용자가 회의록에서 직접 다시 보냄)
  1) 사용자의 결과 전달이 API · 인증 문제로 실패 → 여기 기록 + 관리자 알림
  2) 관리자가 도구를 다시 연결하고 「다시 보내기」
  3) 실패 해결 → 사용자 회의가 「전달 완료」로 바뀌고 사용자에게 알림
"""

from __future__ import annotations

import streamlit as st

from config import ADMIN_EMAIL
from data import repository as repo
from data.models import ExportFailure
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.icons import tool_mark
from ui.layout import card, card_title, empty_state, page, page_header
from ui.tags import tag
from utils.text import esc

_STATUS = {"on": ("연결됨", "on", "연결 해제", "secondary"),
           "expired": ("인증 만료", "expired", "다시 연결", "primary"),
           "off": ("미연결", "off", "연결하기", "secondary")}
_FCOLS = [2.4, 2.8, 1.6]


def _tools(s: AppState) -> None:
    cfg = repo.integrations()
    with card("adm-tools", gap=None):
        card_title("외부 도구", aside=f'조직 · {cfg["organization"]}')
        for t in cfg["tools"]:
            state = repo.tool_status(t.id)
            label, tone, btn, variant = _STATUS[state]
            meta = [t.desc]
            if state != "off" and t.target:
                meta.append(t.target)
            if state != "off" and t.last_sync:
                meta.append(f"마지막 전송 {t.last_sync}")
            with st.container(key=f"adm-tool-{t.id}", horizontal=True, vertical_alignment="center", gap="medium"):
                markup(
                    f'<div class="adm-tool">{tool_mark(t.id, esc(t.mark))}'
                    f'<span class="integ-info"><span class="nm"><b>{esc(t.name)}</b>'
                    + ('<span class="pill-label">Phase 2</span>' if t.phase2 else "")
                    + f'{tag(label, tone)}</span><span class="d">{esc(" · ".join(meta))}</span>'
                    + ('<span class="warn">인증이 만료되어 이 도구로 보내는 회의 결과가 모두 실패하고 있어요.</span>'
                       if state == "expired" else "")
                    + "</span></div>"
                )
                button(btn, key=f"adm_integ_{t.id}", variant=variant, on_click=actions.set_integration, args=(t.id,))


def _fail_state(s: AppState, f: ExportFailure) -> None:
    """처리 칸: 다시 보냄 / 다시 보내기 / 먼저 다시 연결."""
    tool = repo.tool(f.tool)
    if not sel.failure_open(s, f.id):
        markup(tag("다시 보냄", "ok"))
        return
    if repo.tool_status(f.tool) != "on":
        markup(f'<span class="adm-cell muted">위에서 {esc(tool.name)}을(를) 다시 연결하면 보낼 수 있어요</span>')
        return
    button("다시 보내기", key=f"adm_resend_{f.id}", variant="primary", icon=":material/refresh:",
           on_click=actions.admin_resend, args=(f.id,))


def _failures(s: AppState) -> None:
    items = [f for f in repo.export_failures() if f.kind == "tech"]  # 기술 이슈만 (단순 이슈는 사용자가 해결)
    open_n = sum(sel.failure_open(s, f.id) for f in items)
    with card("adm-fails", gap="small"):
        card_title("전송 실패 기록", aside=f"처리 안 됨 {open_n}건")
        markup('<p class="adm-note">API · 인증 문제(기술 이슈)로 사용자가 다시 보낼 수 없는 전송이에요. '
               f"사용자 화면에는 관리자 이메일(<b>{esc(ADMIN_EMAIL)}</b>)로 문의하라는 안내가 떠요.</p>")
        if not items:
            empty_state("보내지 못한 회의 결과가 없어요.", pad=28)
            return
        with st.container(key="adm-fthead", gap=None):
            c = st.columns(_FCOLS, gap="small")
            for col, h in zip(c, ("회의록 · 보낼 곳", "원인", "처리")):
                with col:
                    markup(f'<span class="adm-th">{h}</span>')
        for f in items:
            m = repo.meeting(f.meeting)
            tool = repo.tool(f.tool)
            with st.container(key=f"adm-frow-{f.id}", gap=None):
                c = st.columns(_FCOLS, vertical_alignment="center", gap="small")
                with c[0]:
                    markup(f'<span class="adm-cell"><b>{esc(m.title)}</b><span>{esc(tool.name)} · {esc(f.when)}</span></span>')
                with c[1]:
                    markup(f'<span class="adm-cell">{esc(f.reason)}<span class="code">{esc(f.code)}</span></span>')
                with c[2]:
                    _fail_state(s, f)


def render() -> None:
    s = get_state()
    with page("adm-mcp"):
        page_header("MCP 관리", "회의 결과를 보내는 외부 도구(MCP 서버)를 연결해요. 여기서 연결한 도구는 조직의 모든 구성원이 함께 써요.")
        expired = [repo.tool(k).name for k, v in repo.tool_statuses().items() if v == "expired"]
        if expired:
            markup(f'<div class="adm-alert" role="status"><b>확인 필요</b>{esc(", ".join(expired))} 인증이 만료됐어요. '
                   "다시 연결하고, 아래 「전송 실패 기록」에서 보내지 못한 회의 결과를 다시 보내 주세요.</div>")
        _tools(s)
        _failures(s)
