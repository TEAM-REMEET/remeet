"""결과 전달 화면 (SSE 진행 상황 시뮬레이션) — 참석자에게 보내기까지.

진행 중일 때만 st.fragment(run_every=...) 로 이 영역만 주기적으로 다시 그립니다.
외부 도구(MCP) 전송은 여기서 하지 않고, 끝난 뒤 「외부 도구 전송」 버튼으로 넘어가 도구를 골라 보내요 (views/export.py).
"""

from __future__ import annotations

import streamlit as st

from state.store import me_id
from config import DELIVERY_POLL_INTERVAL
from data import repository as repo
from state import actions, delivery
from state import selectors as sel
from state.store import get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.layout import page, page_header
from ui.tags import tag
from utils.text import esc

_STEP_VIEW = {"wait": ("", "대기", "wait"), "run": ("", "진행 중", "run"), "done": ("✓", "완료", "ok"), "fail": ("!", "일부 실패", "fail")}
_RC_VIEW = {"wait": ("대기", "wait"), "sending": ("보내는 중", "run"), "ok": ("전달됨", "ok"), "fail": ("전송 실패", "fail")}


def _app_button(label: str, key: str, variant: str, fn, *args, icon: str | None = None) -> None:
    """이 영역(fragment) 안의 버튼이 화면 이동 · 모달처럼 전체 화면을 바꿀 때: 누른 뒤 전체를 다시 그림."""
    def _cb() -> None:
        fn(*args)
        st.session_state["_dv_need_app"] = True

    button(label, key=key, variant=variant, on_click=_cb, icon=icon)


def _banner(p: delivery.Progress, total: int, mid: str) -> None:
    if p.ended:
        tone = "fail" if p.failed else "ok"
        head = f"전송 실패 · {p.failed}건" if p.failed else "참석자에게 모두 전달했어요"
        sub = ("메신저가 잠깐 응답하지 않았어요. 다시 보내면 실패한 사람에게만 보내요." if p.failed
               else f"참석자 {total}명에게 요약·결정·할 일을 보내고 위키에 반영했어요. "
                    "Jira · Slack 같은 외부 도구로도 보내려면 「외부 도구 전송」을 눌러 주세요.")
        dot = "#B3261E" if p.failed else "#2E9E62"
    else:
        tone, dot = "wait", "#F37321"
        head = "연결하는 중…" if p.conn == "connecting" else "실시간으로 전달하고 있어요"
        sub = "참석자에게 보내는 진행 상황을 실시간으로 보여주고 있어요."
    with st.container(key=f"dvb-{tone}", horizontal=True, vertical_alignment="center", gap="small"):
        markup(f'<div class="dv-banner" role="status" aria-live="polite">'
               f'<span class="dot{"" if p.ended else " pulse"}" style="background:{dot}"></span>'
               f'<span class="tx"><b>{esc(head)}</b><span>{esc(sub)}</span></span></div>')
        if not p.ended:
            return
        host = repo.meeting(mid).host == me_id()
        _app_button("회의 상세로 돌아가기", "dv_back", "secondary", actions.open_meeting, mid)
        if p.failed and host:  # 다시 보내기는 주관자만: 실패한 참석자에게만 (다시 보내기 버튼은 여기 하나만)
            _app_button(f"실패한 {p.failed}건 다시 보내기", "dv_retry", "danger", actions.start_delivery, mid, 2,
                        icon=":material/refresh:")
        if host:  # 결과 전달이 끝나면 외부 도구(MCP)로 따로 보내기
            _app_button("외부 도구 전송", "dv_export", "primary", actions.open_export, mid, icon=":material/send:")


def _steps(p: delivery.Progress, rc_total: int, todo_n: int) -> None:
    rows = [
        ("save", "회의록 확정 저장", "편집한 요약·안건을 새 버전으로 저장"),
        ("todo", "할 일·담당자 반영", f"할 일 {todo_n}건을 담당자에게 배정"),
        ("send", "참석자에게 결과 전송", f"{p.ok} / {rc_total}명 전달됨"),
        ("wiki", "위키 반영", "폴더 위키 업데이트"),
    ]
    html = ""
    for sid, label, detail in rows:
        mark, badge, tone = _STEP_VIEW[p.steps[sid]]
        html += (f'<div class="dv-step"><span class="ic {p.steps[sid]}">{mark}</span>'
                 f'<span class="tx"><b>{label}</b><span>{esc(detail)}</span></span>{tag(badge, tone)}</div>')
    with st.container(key="panel-steps"):
        markup(f'<h2 class="panel-h">진행 단계</h2>{html}')


def _recipients(p: delivery.Progress, mid: str) -> None:
    m = repo.meeting(mid)
    with st.container(key="panel-rc", gap=None):
        markup(f'<h2 class="panel-h">참석자별 전달 ({p.ok}/{len(p.recipients)})</h2>')
        for pid, st_ in p.recipients.items():
            person = sel.person(pid)
            n = sum(1 for _, t in sel.todo_items(get_state(), mid) if pid in t.owners)
            sub = delivery.TIMEOUT_TEXT if st_ == "fail" else "요약 · 결정" + (f" · 할 일 {n}건" if n else "")
            label, tone = _RC_VIEW[st_]
            markup(f'<div class="dv-rc">{avatar(person.initial, size=32)}'
                   f'<span class="tx"><b>{esc(person.name)} <span class="team">{esc(person.team)}</span></b>'
                   f'<span class="sub{" fail" if st_ == "fail" else ""}">{esc(sub)}</span></span>'
                   f'{tag(label, tone, pulse=st_ == "sending")}</div>')


def _live() -> None:
    s = get_state()
    if st.session_state.pop("_dv_need_app", False):  # 이 영역의 버튼이 화면 이동 · 모달 · 다시 보내기를 시작함
        st.rerun(scope="app")
    delivery.sync(s)
    mid = s.delivery.meeting_id
    p = delivery.progress(s)
    _banner(p, len(p.recipients), mid)
    left, right = st.columns([1, 1.25], gap="medium")
    with left:
        _steps(p, len(p.recipients), len(sel.todo_items(get_state(), mid)))
    with right:
        _recipients(p, mid)
    if p.ended and not s.delivery.finished:
        s.delivery.finished = True
    if p.ended and st.session_state.get("_dv_polling"):
        st.session_state["_dv_polling"] = False
        st.rerun(scope="app")  # 폴링을 멈추기 위해 전체를 한 번 다시 그림


def render() -> None:
    s = get_state()
    mid = s.delivery.meeting_id
    with page("deliver"):
        page_header("결과 전달", f"{repo.meeting(mid).title} · 확정한 내용을 참석자에게 보내고 위키에 반영해요")
        running = not delivery.progress(s).ended
        st.session_state["_dv_polling"] = running
        st.fragment(_live, run_every=DELIVERY_POLL_INTERVAL if running else None)()