"""결과 전달(SSE) 시뮬레이션 — 참석자에게 보내기까지.

실제 서버 대신, 시작 시각부터 흐른 시간으로 '지금까지 도착한 이벤트 수'를 계산합니다.
타이머 없이도 페이지를 다시 그릴 때마다 같은 결과가 나오므로 화면 코드가 단순해집니다.

전달 순서: 회의록 확정 저장 → 할 일·담당자 반영 → 참석자에게 결과 전송 → 위키 반영
외부 도구(MCP · Slack · Jira 등) 전송은 여기서 하지 않고, 끝난 뒤 「외부 도구 전송」 화면(state/export.py)에서
주관자가 도구를 골라 따로 보내요.

전송 실패 (목업): 외부 도구 실패 기록이 없는 회의는 3번째 참석자에게 메신저가 응답하지 않음(timeout) → 「다시 보내기」
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from config import CURRENT_USER_ID as ME
from config import DELIVERY_EVENT_INTERVAL, DELIVERY_FIRST_EVENT_DELAY
from data import repository as repo
from state.store import AppState

TIMEOUT_TEXT = "메신저 응답 없음 (timeout)"


@dataclass(frozen=True)
class Event:
    kind: str  # open | step | sendStart | sent | end
    step: str = ""
    to: str = ""  # 받는 사람 id (sent)
    ok: bool = True


def recipients(mid: str) -> list[str]:
    return [p for p in repo.meeting(mid).people if p != ME]


def now_text() -> str:
    """알림 · 실패 기록에 적는 지금 시각 (한국 시간)."""
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M")


def _timeout_person(mid: str) -> str:
    """데모: 외부 도구 실패 기록이 없는 회의는 3번째 참석자에게 메신저 전송이 끊김 (단순 이슈)."""
    rc = recipients(mid)
    return rc[2] if len(rc) > 2 and not repo.export_failures(mid) else ""


def _first_events(mid: str) -> list[Event]:
    timeout = _timeout_person(mid)
    return (
        [Event("open"), Event("step", step="save"), Event("step", step="todo"), Event("sendStart")]
        + [Event("sent", to=r, ok=r != timeout) for r in recipients(mid)]
        + [Event("step", step="wiki"), Event("end")]
    )


def _retry_events(mid: str) -> list[Event]:
    """다시 보내기: 실패한 참석자에게만 (이번엔 성공)."""
    timeout = _timeout_person(mid)
    return [Event("open")] + ([Event("sent", to=timeout, ok=True)] if timeout else []) + [Event("end")]


def events(state: AppState) -> list[Event]:
    d = state.delivery
    return _first_events(d.meeting_id) if d.attempt == 1 else _retry_events(d.meeting_id)


def arrived_count(state: AppState, now: float | None = None) -> int:
    d = state.delivery
    total = len(events(state))
    if d.started_at is None:
        return total
    elapsed = (now or time.time()) - d.started_at
    n = int((elapsed - DELIVERY_FIRST_EVENT_DELAY) // DELIVERY_EVENT_INTERVAL) + 1
    return max(0, min(total, n))


def sync(state: AppState) -> None:
    """모든 이벤트가 도착했으면 결과를 확정합니다. 매 실행 시작 때 호출."""
    d = state.delivery
    if d.finished:
        return
    if arrived_count(state) >= len(events(state)):
        p = progress(state)
        d.finished = True
        state.status_override[d.meeting_id] = "fail" if p.failed else "done"


# ---------------------------------------------------------------- 화면용 계산
@dataclass
class Progress:
    conn: str = "connecting"  # connecting | open | closed
    steps: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(("save", "todo", "send", "wiki"), "wait"))
    recipients: dict[str, str] = field(default_factory=dict)  # wait | sending | ok | fail
    log: list[dict] = field(default_factory=list)
    ended: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for v in self.recipients.values() if v == "fail")

    @property
    def ok(self) -> int:
        return sum(1 for v in self.recipients.values() if v == "ok")


def _settle(p: Progress) -> None:
    vals = p.recipients.values()
    if all(v in ("ok", "fail") for v in vals):
        p.steps["send"] = "fail" if any(v == "fail" for v in vals) else "done"


def _apply(p: Progress, e: Event, mid: str, attempt: int) -> None:
    people = repo.people()
    from state import selectors as sel  # 순환 import 방지 (selectors 가 store 를 씀)
    from state.store import get_state

    todo_n = len(sel.todo_items(get_state(), mid))  # 삭제한 액션 아이템은 빼고
    if e.kind == "open":
        p.conn, p.ended = "open", False
        p.log.append({"ev": "open", "data": f'{{"meeting":"{mid}","attempt":{attempt}}}'})
    elif e.kind == "step":
        p.steps[e.step] = "done"
        ev = {"save": "meeting.confirmed", "todo": "tasks.assigned", "wiki": "wiki.updated"}[e.step]
        data = {"save": '{"version":2}', "todo": f'{{"tasks":{todo_n}}}', "wiki": '{"folder":true}'}
        p.log.append({"ev": ev, "data": data[e.step]})
    elif e.kind == "sendStart":
        p.steps["send"] = "run"
        p.log.append({"ev": "delivery.started", "data": f'{{"recipients":{len(p.recipients)}}}'})
    elif e.kind == "sent":
        p.recipients[e.to] = "ok" if e.ok else "fail"
        _settle(p)
        name = people[e.to].name
        data = f'{{"to":"{name}","status":"ok"}}' if e.ok else f'{{"to":"{name}","error":"timeout"}}'
        p.log.append({"ev": "delivery.sent" if e.ok else "delivery.failed", "data": data, "fail": not e.ok})
    elif e.kind == "end":
        p.conn, p.ended = "closed", True
        p.log.append({"ev": "end", "data": f'{{"failed":{p.failed}}}', "fail": p.failed > 0})


def progress(state: AppState) -> Progress:
    d = state.delivery
    mid = d.meeting_id
    p = Progress(recipients=dict.fromkeys(recipients(mid), "wait"))
    idx = arrived_count(state)
    if d.attempt == 1:
        evs = _first_events(mid)
    else:
        for e in _first_events(mid):  # 첫 전송 결과(그때 실패한 것) 위에 다시 보낸 결과를 얹음
            _apply(p, e, mid, 1)
        evs = _retry_events(mid)
        if idx == 0:
            p.conn, p.ended = "connecting", False
    for e in evs[:idx]:
        _apply(p, e, mid, d.attempt)
    nxt = evs[idx] if idx < len(evs) else None
    if nxt and nxt.kind == "sent":
        p.recipients[nxt.to] = "sending"
        p.steps["send"] = "run"
    if nxt and nxt.kind == "step":
        p.steps[nxt.step] = "run"
    return p
