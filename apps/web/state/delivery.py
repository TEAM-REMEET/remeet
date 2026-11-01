"""결과 전달(SSE) 시뮬레이션.

실제 서버 대신, 시작 시각부터 흐른 시간으로 '지금까지 도착한 이벤트 수'를 계산합니다.
타이머 없이도 페이지를 다시 그릴 때마다 같은 결과가 나오므로 화면 코드가 단순해집니다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import CURRENT_USER_ID as ME
from config import DELIVERY_EVENT_INTERVAL, DELIVERY_FIRST_EVENT_DELAY
from data import repository as repo
from state.store import AppState


@dataclass(frozen=True)
class Event:
    kind: str  # open | step | sendStart | sent | end
    step: str = ""
    to: str = ""
    ok: bool = True


def recipients(mid: str) -> list[str]:
    return [p for p in repo.meeting(mid).people if p != ME]


def events(mid: str, attempt: int) -> list[Event]:
    rc = recipients(mid)
    if attempt == 2:  # 재시도: 첫 시도에서 실패한 3번째 참석자에게만 다시 보냄
        if len(rc) > 2:
            return [Event("open"), Event("sent", to=rc[2], ok=True), Event("end")]
        return [Event("open"), Event("end")]
    return (
        [Event("open"), Event("step", step="save"), Event("step", step="todo"), Event("sendStart")]
        + [Event("sent", to=r, ok=(i != 2)) for i, r in enumerate(rc)]  # 데모: 3번째 사람은 timeout
        + [Event("step", step="wiki"), Event("end")]
    )


def outcome(mid: str, attempt: int) -> str:
    return "fail" if attempt == 1 and len(recipients(mid)) > 2 else "done"


def arrived_count(state: AppState, now: float | None = None) -> int:
    d = state.delivery
    total = len(events(d.meeting_id, d.attempt))
    if d.started_at is None:
        return total
    elapsed = (now or time.time()) - d.started_at
    n = int((elapsed - DELIVERY_FIRST_EVENT_DELAY) // DELIVERY_EVENT_INTERVAL) + 1
    return max(0, min(total, n))


def sync(state: AppState) -> None:
    """모든 이벤트가 도착했으면 회의 상태를 결과(done/fail)로 확정합니다. 매 실행 시작 때 호출."""
    d = state.delivery
    if d.finished:
        return
    if arrived_count(state) >= len(events(d.meeting_id, d.attempt)):
        d.finished = True
        state.status_override[d.meeting_id] = outcome(d.meeting_id, d.attempt)


# ---------------------------------------------------------------- 화면용 계산
@dataclass
class Progress:
    conn: str = "connecting"  # connecting | open | closed
    steps: dict[str, str] = field(default_factory=lambda: dict.fromkeys(("save", "todo", "send", "wiki"), "wait"))
    recipients: dict[str, str] = field(default_factory=dict)  # wait | sending | ok | fail
    log: list[dict] = field(default_factory=list)
    ended: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for v in self.recipients.values() if v == "fail")

    @property
    def ok(self) -> int:
        return sum(1 for v in self.recipients.values() if v == "ok")


def _apply(p: Progress, e: Event, mid: str, attempt: int) -> None:
    people = repo.people()
    todo_n = len(repo.meeting(mid).todos)
    if e.kind == "open":
        p.conn, p.ended = "open", False
        if attempt == 2:
            p.steps["send"] = "run"
        p.log.append({"ev": "open", "data": f'{{"meeting":"{mid}","attempt":{attempt}}}'})
    elif e.kind == "step":
        p.steps[e.step] = "done"
        ev = {"save": "meeting.confirmed", "todo": "tasks.assigned", "wiki": "wiki.updated"}[e.step]
        data = {"save": '{"version":2}', "todo": f'{{"tasks":{todo_n}}}', "wiki": '{"folder":true,"personal":true}'}
        p.log.append({"ev": ev, "data": data[e.step]})
    elif e.kind == "sendStart":
        p.steps["send"] = "run"
        p.log.append({"ev": "delivery.started", "data": f'{{"recipients":{len(p.recipients)}}}'})
    elif e.kind == "sent":
        p.recipients[e.to] = "ok" if e.ok else "fail"
        name = people[e.to].name
        data = f'{{"to":"{name}","status":"ok"}}' if e.ok else f'{{"to":"{name}","error":"timeout"}}'
        p.log.append({"ev": "delivery.sent" if e.ok else "delivery.failed", "data": data, "fail": not e.ok})
    elif e.kind == "end":
        p.conn, p.ended = "closed", True
        p.steps["send"] = "fail" if p.failed else "done"
        p.log.append({"ev": "end", "data": f'{{"failed":{p.failed}}}', "fail": p.failed > 0})


def progress(state: AppState) -> Progress:
    d = state.delivery
    mid = d.meeting_id
    p = Progress(recipients=dict.fromkeys(recipients(mid), "wait"))
    idx = arrived_count(state)
    first = events(mid, 1)
    if d.attempt == 1:
        for e in first[:idx]:
            _apply(p, e, mid, 1)
        nxt = first[idx] if idx < len(first) else None
    else:
        for e in first:
            _apply(p, e, mid, 1)
        retry = events(mid, 2)
        if idx == 0:
            p.conn, p.ended, p.steps["send"] = "connecting", False, "run"
        for e in retry[:idx]:
            _apply(p, e, mid, 2)
        nxt = retry[idx] if idx < len(retry) else None
    if nxt and nxt.kind == "sent":
        p.recipients[nxt.to] = "sending"
    if nxt and nxt.kind == "step":
        p.steps[nxt.step] = "run"
    return p
