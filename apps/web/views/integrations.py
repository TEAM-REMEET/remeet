"""외부 연동: 외부 도구 연결 + AI(MCP)가 읽을 수 있는 범위."""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state import selectors as sel
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
                + "</span></div>"
            )
            button(btn, key=f"integ_{t.id}", variant=variant, on_click=actions.set_integration, args=(t.id,))


def _ai_scope() -> None:
    s = get_state()
    cfg = repo.integrations()
    folders = sel.folders(s)
    types = repo.meeting_types()
    with st.container(key="ai-scope"):
        markup('<div style="display:flex;align-items:center;gap:8px"><h2 class="sec-h">AI가 볼 수 있는 범위</h2>'
               '<span class="pill-label">MCP</span></div>'
               '<p class="hint" style="font-size:13px;color:#5F646D">Claude·Cursor 같은 AI 도구는 내 계정과 같은 권한으로만 읽어요. '
               "내가 멤버인 폴더 중에서 고를 수 있고, 공유 받은 회의는 연동되지 않아요.</p>")
        ids = sel.all_my_folder_ids(s)
        for start in range(0, len(ids), 4):
            cols = st.columns(4, gap="small")
            for col, fid in zip(cols, ids[start:start + 4]):
                f = folders[fid]
                on = s.ai_folders.get(fid, False)
                with col, st.container(key=f"aif-{fid}{'-on' if on else ''}"):
                    st.checkbox(f.name, value=on, key=f"aif_cb_{fid}", on_change=actions.toggle_ai_folder, args=(fid,),
                                help=None)
                    markup(f'<span class="hint" style="margin-left:28px">{esc(types[f.type].name)} · 회의록 {len(f.notes)}개</span>')
        with st.container(gap=None):
            for t in cfg["ai_toggles"]:
                with st.container(key=f"aitg-{t.key}", horizontal=True, vertical_alignment="center"):
                    markup(f'<span style="display:flex;flex-direction:column;line-height:1.4"><b style="font-weight:600">{esc(t.label)}</b>'
                           f'<span style="font-size:12px;color:#80858E">{esc(t.desc)}</span></span>')
                    st.toggle(t.label, value=s.ai_toggles.get(t.key, False), key=f"aitg_{t.key}", label_visibility="collapsed",
                              on_change=actions.toggle_ai_option, args=(t.key,), width="content")


def render() -> None:
    with page("mcp"):
        page_header("외부 연동", "회의 결과를 팀이 쓰는 도구로 보내고, AI 도구가 읽을 수 있는 범위를 정해요.")
        _tools()
        _ai_scope()
