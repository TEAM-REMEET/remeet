"""결과 전달 화면 (SSE 진행 상황 시뮬레이션).

진행 중일 때만 st.fragment(run_every=...) 로 이 영역만 주기적으로 다시 그립니다.
"""

from __future__ import annotations

import streamlit as st

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


def _banner(p: delivery.Progress, total: int, mid: str) -> None:
    if p.ended:
        tone = "fail" if p.failed else "ok"
        head = f"전송 실패 · {p.failed}건" if p.failed else "모두 전달했어요"
        sub = ("일부 참석자에게 결과를 보내지 못했어요. 재시도하면 실패한 사람에게만 다시 보내요." if p.failed
               else f"참석자 {total}명에게 요약·결정·할 일을 보내고 위키에 반영했어요.")
        dot = "#B3261E" if p.failed else "#2E9E62"
    else:
        tone, dot = "wait", "#F37321"
        head = "연결하는 중…" if p.conn == "connecting" else "실시간으로 전달하고 있어요"
        sub = "참석자에게 보내는 진행 상황을 실시간으로 보여주고 있어요."
    with st.container(key=f"dvb-{tone}", horizontal=True, vertical_alignment="center", gap="medium"):
        markup(f'<div class="dv-banner" role="status" aria-live="polite">'
               f'<span class="dot{"" if p.ended else " pulse"}" style="background:{dot}"></span>'
               f'<span class="tx"><b>{esc(head)}</b><span>{esc(sub)}</span></span></div>')
        if p.ended and p.failed:
            button(f"실패한 {p.failed}건 재시도", key="dv_retry", variant="danger", on_click=actions.start_delivery, args=(mid, 2))
        elif p.ended:
            button("회의 상세로 돌아가기", key="dv_back2", variant="dark", on_click=actions.open_meeting, args=(mid,))


def _steps(p: delivery.Progress, rc_total: int, todo_n: int) -> None:
    rows = [
        ("save", "회의록 확정 저장", "편집한 요약·안건을 새 버전으로 저장"),
        ("todo", "할 일·담당자 반영", f"할 일 {todo_n}건을 담당자에게 배정"),
        ("send", "참석자에게 결과 전송", f"{p.ok} / {rc_total}명 전달됨"),
        ("wiki", "위키 반영", "폴더 위키 · 개인 위키 업데이트"),
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
            n = sum(1 for t in m.todos if t.owner == pid)
            sub = "메신저 응답 없음 (timeout)" if st_ == "fail" else "요약 · 결정" + (f" · 할 일 {n}건" if n else "")
            label, tone = _RC_VIEW[st_]
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                markup(f'<div class="dv-rc">{avatar(person.initial, size=32)}'
                       f'<span class="tx"><b>{esc(person.name)} <span class="team">{esc(person.team)}</span></b>'
                       f'<span class="sub{" fail" if st_ == "fail" else ""}">{esc(sub)}</span></span>'
                       f'{tag(label, tone, pulse=st_ == "sending")}</div>')
                if st_ == "fail" and p.ended:
                    button("재시도", key=f"rc_retry_{pid}", variant="dangerline", on_click=actions.start_delivery, args=(mid, 2))


def _live() -> None:
    s = get_state()
    delivery.sync(s)
    mid = s.delivery.meeting_id
    p = delivery.progress(s)
    _banner(p, len(p.recipients), mid)
    left, right = st.columns([1, 1.25], gap="medium")
    with left:
        _steps(p, len(p.recipients), len(repo.meeting(mid).todos))
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
