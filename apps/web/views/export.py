"""외부 도구(MCP) 전송 화면 — 결과 전달(참석자에게 보내기)이 끝난 뒤 주관자가 도구를 골라 보냄.

1) 도구 고르기: 관리자가 연결한 도구(Slack · Jira · Notion …) 중에서 고름 (인증 만료 도구는 표시)
2) 보내기: 도구별 진행 상황 (결과 전달 화면과 같은 실시간 모양) · 진행 중에만 이 영역을 주기적으로 다시 그림
3) 끝: 단순 이슈로 실패한 도구만 다시 보내기 · 기술 이슈(인증 만료)는 「관리자에게 문의」 모달 · 「전달 완료」
"""

from __future__ import annotations

import streamlit as st

from config import DELIVERY_POLL_INTERVAL
from data import repository as repo
from state import actions, export
from state import selectors as sel
from state.store import get_state, me_id
from ui.buttons import button
from ui.html import markup
from ui.icons import tool_mark
from ui.layout import page, page_header
from ui.tags import tag
from utils.text import esc, md_escape

_VIEW = {"wait": ("대기", "wait"), "sending": ("보내는 중", "run"), "ok": ("전송됨", "ok"), "fail": ("전송 실패", "fail")}


def _tool_sub(tool, mid: str) -> str:
    n = len(sel.todo_items(get_state(), mid))
    base = f"액션 아이템 {n}건 → 이슈" if tool.id == "jira" else tool.desc
    return base + (f" · {tool.target}" if tool.target else "")


# ---------------------------------------------------------------- 1) 도구 고르기
def _pick(mid: str) -> None:
    s = get_state()
    x = s.export
    tools = export.available_tools()
    markup('<div class="prev-title"><b>보낼 도구 고르기</b>'
           '<span class="hint">관리자가 연결한 도구만 보여요 · 여러 개 고를 수 있어요</span></div>')
    with st.container(key="exp-tools", gap="small"):
        if not tools:
            markup('<p class="hint" style="font-size:13px">연결된 외부 도구가 없어요. 관리자에게 연결을 요청해 주세요.</p>')
        for tool, status in tools:
            on = tool.id in x.picked
            with st.container(key=f"exptool-{'on' if on else 'off'}-{tool.id}", horizontal=True,
                              vertical_alignment="center", gap="small"):
                st.html(tool_mark(tool.id, esc(tool.mark), "dv-mark"), width="content")
                label = f"**{md_escape(tool.name)}**  \n:gray[{md_escape(_tool_sub(tool, mid))}]"
                if status == "expired":
                    label += "  \n:red[인증이 만료됐어요 · 보내면 실패하고 관리자 확인이 필요해요]"
                st.checkbox(label, value=on, key=f"exp_pick_{mid}_{tool.id}",
                            on_change=actions.toggle_export_tool, args=(tool.id,), width="stretch")
    n = len(x.picked)
    with st.container(key="exp-foot", horizontal=True, vertical_alignment="center", gap="small"):
        st.space("stretch")
        button("회의 상세로 돌아가기", key="exp_back", variant="secondary", on_click=actions.open_meeting, args=(mid,))
        button(f"선택한 {n}개 도구로 보내기" if n else "도구를 골라 주세요", key="exp_send", variant="primary",
               icon=":material/send:", disabled=not n, on_click=actions.start_export)


# ---------------------------------------------------------------- 2) 보내기 · 3) 끝
def _app_button(label: str, key: str, variant: str, fn, *args, icon: str | None = None) -> None:
    """이 영역(fragment) 안의 버튼이 화면 이동 · 다시 보내기를 시작할 때: 누른 뒤 전체를 다시 그림."""
    def _cb() -> None:
        fn(*args)
        st.session_state["_exp_need_app"] = True

    button(label, key=key, variant=variant, on_click=_cb, icon=icon)


def _banner(p: export.Progress, mid: str) -> None:
    s = get_state()
    fails = export.failed_records(s, p)
    tech = next((f for f in fails if f.kind == "tech"), None)
    simple_fail = any(f.kind == "simple" for f in fails)
    if p.ended:
        tone = "fail" if p.failed else "ok"
        head = f"전송 실패 · {p.failed}개 도구" if p.failed else f"{p.ok}개 도구로 모두 보냈어요"
        if not p.failed:
            sub = "외부 도구 전송을 마쳤어요. 「전달 완료」를 누르면 회의 상세로 돌아가요."
        elif tech:
            sub = (f"{repo.tool(tech.tool).name} 연동(API · 인증) 문제라 다시 보내도 실패해요. 관리자에게 문의해 주세요."
                   + (" 일시적인 오류로 실패한 도구는 다시 보낼 수 있어요." if simple_fail else ""))
        else:
            sub = "일시적인 오류로 끊겼어요. 다시 보내면 실패한 도구에만 보내요."
        dot = "#B3261E" if p.failed else "#2E9E62"
    else:
        tone, dot = "wait", "#F37321"
        head = "연결하는 중…" if p.conn == "connecting" else "외부 도구로 보내고 있어요"
        sub = "고른 도구로 보내는 진행 상황을 실시간으로 보여주고 있어요."
    with st.container(key=f"dvb-{tone}", horizontal=True, vertical_alignment="center", gap="small"):
        markup(f'<div class="dv-banner" role="status" aria-live="polite">'
               f'<span class="dot{"" if p.ended else " pulse"}" style="background:{dot}"></span>'
               f'<span class="tx"><b>{esc(head)}</b><span>{esc(sub)}</span></span></div>')
        if not p.ended:
            return
        n_simple = sum(1 for f in fails if f.kind == "simple")
        if n_simple and s.export.attempt == 1 and repo.meeting(mid).host == me_id():
            _app_button(f"실패한 {n_simple}개 다시 보내기", "exp_retry", "danger", actions.retry_export,
                        icon=":material/refresh:")
        _app_button("전달 완료", "exp_done", "primary", actions.finish_export, icon=":material/check:")


def _rows(p: export.Progress, mid: str) -> None:
    with st.container(key="panel-rc", gap=None):
        markup(f'<h2 class="panel-h">외부 도구 전송 ({p.ok}/{len(p.exports)})</h2>')
        for tool_id, st_ in p.exports.items():
            tool = repo.tool(tool_id)
            f = next((x for x in repo.export_failures(mid) if x.tool == tool_id), None)
            sub = (f.reason + (" · 관리자 문의 필요" if f.kind == "tech" else "")) if st_ == "fail" and f else _tool_sub(tool, mid)
            label, tone = _VIEW[st_]
            markup(f'<div class="dv-rc">{tool_mark(tool.id, esc(tool.mark), "dv-mark")}'
                   f'<span class="tx"><b>{esc(tool.name)}</b>'
                   f'<span class="sub{" fail" if st_ == "fail" else ""}">{esc(sub)}</span></span>'
                   f'{tag(label, tone, pulse=st_ == "sending")}</div>')


def _live() -> None:
    s = get_state()
    if st.session_state.pop("_exp_need_app", False):  # 이 영역의 버튼이 화면 이동 · 다시 보내기를 시작함
        st.rerun(scope="app")
    export.sync(s)
    mid = s.export.meeting_id
    p = export.progress(s)
    tech = next((f for f in export.failed_records(s, p) if f.kind == "tech"), None)
    if p.ended and tech and not s.export.contact_shown:
        # 기술 이슈로 실패하고 끝남: 그 자리에서 「관리자에게 문의」 모달 (이번 전송에 한 번)
        s.export.contact_shown = True
        actions.open_contact_admin(tech.id)
        st.session_state["_exp_polling"] = False
        st.rerun(scope="app")
    _banner(p, mid)
    _rows(p, mid)
    if p.ended and st.session_state.get("_exp_polling"):
        st.session_state["_exp_polling"] = False
        st.rerun(scope="app")  # 폴링을 멈추기 위해 전체를 한 번 다시 그림


def render() -> None:
    s = get_state()
    mid = s.export.meeting_id
    with page("export"):
        page_header("외부 도구 전송", f"{repo.meeting(mid).title} · 결과를 보낼 외부 도구(Jira · Slack 등)를 골라 보내요")
        if repo.meeting(mid).host != me_id():
            markup('<p class="hint" style="font-size:14px">외부 도구 전송은 회의 주관자만 할 수 있어요.</p>')
            return
        if s.export.started_at is None:
            _pick(mid)
            return
        running = not export.progress(s).ended
        st.session_state["_exp_polling"] = running
        st.fragment(_live, run_every=DELIVERY_POLL_INTERVAL if running else None)()
