"""결과 전달(SSE) 시뮬레이션.

실제 서버 대신, 시작 시각부터 흐른 시간으로 '지금까지 도착한 이벤트 수'를 계산합니다.
타이머 없이도 페이지를 다시 그릴 때마다 같은 결과가 나오므로 화면 코드가 단순해집니다.

전달 순서: 회의록 확정 저장 → 할 일·담당자 반영 → 참석자에게 결과 전송 → 외부 도구(MCP) 전송 → 위키 반영

전송 실패 (목업)
  - 외부 도구 전송 실패 기록(data/mock/integrations.json 의 failures)이 있는 회의
      · 단순 이슈 (예: Slack 네트워크 오류)  → 주관자가 「다시 보내기」로 해결
  - 그 밖의 회의는 Slack · Jira 로 보내요. 인증이 만료된 도구(목업: Jira)가 있으면
      · 기술 이슈로 실패 → 전송이 끝나는 그 자리에서 「관리자에게 문의」 모달 (다시 보내기 없음)
      · 실패 기록을 남겨 관리자 화면(MCP 관리 · 전송 실패 기록)에도 보여요
  - 기술 이슈가 없으면: 3번째 참석자에게 메신저가 응답하지 않음(timeout, 단순 이슈) → 「다시 보내기」
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field

from config import CURRENT_USER_ID as ME
from config import DELIVERY_EVENT_INTERVAL, DELIVERY_FIRST_EVENT_DELAY
from data import repository as repo
from data.models import ExportFailure
from state.store import AppState

TIMEOUT_TEXT = "메신저 응답 없음 (timeout)"
DEFAULT_TOOLS = ("slack", "jira")  # 외부 도구 실패 기록이 없는 회의에서 보내는 도구 (회의 결과 공유 · 액션 아이템 이슈)


@dataclass(frozen=True)
class Event:
    kind: str  # open | step | sendStart | sent | export | end
    step: str = ""
    to: str = ""  # 받는 사람 id (sent) · 도구 id (export)
    ok: bool = True


def recipients(mid: str) -> list[str]:
    return [p for p in repo.meeting(mid).people if p != ME]


def tools(mid: str) -> list[str]:
    """이 회의 결과를 보내는 외부 도구."""
    mock = [f.tool for f in repo.integrations()["failures"] if f.meeting == mid]  # 목업 실패 기록이 있는 회의
    return list(dict.fromkeys(mock)) or list(DEFAULT_TOOLS)


def open_failure(state: AppState, mid: str, tool_id: str | None = None) -> ExportFailure | None:
    """아직 해결되지 않은 외부 도구 전송 실패 (없으면 None)."""
    return next((f for f in repo.export_failures(mid)
                 if _open(state, f.id) and (tool_id is None or f.tool == tool_id)), None)


def kind(state: AppState, mid: str) -> str:
    """지금 남은 실패의 종류: tech(관리자 문의) | simple(다시 보내기)."""
    f = open_failure(state, mid)
    return f.kind if f else "simple"


def open_ids(state: AppState, mid: str) -> tuple[str, ...]:
    return tuple(f.id for f in repo.export_failures(mid) if _open(state, f.id))


def _open(state: AppState, fid: str) -> bool:
    """아직 해결 안 된 실패 (내가 다시 보냄 · 관리자가 해결함이 아니면)."""
    return fid not in state.export_resent and not repo.is_resolved(fid)


def now_text() -> str:
    """알림 · 실패 기록에 적는 지금 시각 (한국 시간)."""
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%m/%d %H:%M")


def _snapshot(state: AppState) -> tuple[str, ...]:
    """보내기 시작할 때 남아 있던 외부 도구 실패 (보내는 중·끝난 뒤에도 결과가 바뀌지 않게 고정)."""
    d = state.delivery
    return d.fids if d.started_at is not None else open_ids(state, d.meeting_id)


def _expired_tools(state: AppState, mid: str) -> list[str]:
    """이번 전송에서 인증 만료로 실패할 도구 (보내기 시작할 때의 연결 상태 기준)."""
    d = state.delivery
    return [t for t in tools(mid) if t in d.expired] if d.started_at is not None and d.attempt == 1 else []


def _timeout_person(mid: str, expired: list[str]) -> str:
    """외부 도구 실패가 없는 전송: 데모로 3번째 참석자에게 메신저 전송이 끊김 (단순 이슈)."""
    rc = recipients(mid)
    return rc[2] if len(rc) > 2 and not repo.export_failures(mid) and not expired else ""


def _first_events(mid: str, fids: tuple[str, ...], expired: list[str]) -> list[Event]:
    timeout = _timeout_person(mid, expired)
    failed_tools = {f.tool for f in repo.export_failures(mid) if f.id in fids} | set(expired)
    return (
        [Event("open"), Event("step", step="save"), Event("step", step="todo"), Event("sendStart")]
        + [Event("sent", to=r, ok=r != timeout) for r in recipients(mid)]
        + [Event("export", to=t, ok=t not in failed_tools) for t in tools(mid)]
        + [Event("step", step="wiki"), Event("end")]
    )


def _retry_events(mid: str, fids: tuple[str, ...]) -> list[Event]:
    """다시 보내기: 실패한 것만. 단순 이슈는 이번엔 성공, 기술 이슈는 또 실패."""
    timeout = _timeout_person(mid, [])
    evs = [Event("open")] + ([Event("sent", to=timeout, ok=True)] if timeout else [])
    evs += [Event("export", to=f.tool, ok=f.kind == "simple") for f in repo.export_failures(mid) if f.id in fids]
    return evs + [Event("end")]


def events(state: AppState) -> list[Event]:
    d = state.delivery
    fids = _snapshot(state)
    if d.attempt == 1:
        return _first_events(d.meeting_id, fids, _expired_tools(state, d.meeting_id))
    return _retry_events(d.meeting_id, fids)


def arrived_count(state: AppState, now: float | None = None) -> int:
    d = state.delivery
    total = len(events(state))
    if d.started_at is None:
        return total
    elapsed = (now or time.time()) - d.started_at
    n = int((elapsed - DELIVERY_FIRST_EVENT_DELAY) // DELIVERY_EVENT_INTERVAL) + 1
    return max(0, min(total, n))


def sync(state: AppState) -> None:
    """모든 이벤트가 도착했으면 결과를 확정합니다. 매 실행 시작 때 호출.

    다시 보내기로 단순 이슈가 해결되면 그 실패 기록을 「다시 보냄」으로 바꿔
    회의 상세의 실패 안내도 함께 사라지게 합니다.
    """
    d = state.delivery
    if d.finished:
        return
    if arrived_count(state) >= len(events(state)):
        p = progress(state)
        d.finished = True
        if d.attempt == 2:
            for f in repo.export_failures(d.meeting_id):
                if f.id in d.fids and f.kind == "simple":
                    state.export_resent.add(f.id)
        for t in _expired_tools(state, d.meeting_id):  # 인증 만료로 실패 → 실패 기록 (관리자 화면에도 보임)
            tool = repo.tool(t)
            fid = f"rt-{d.meeting_id}-{t}"
            when = now_text()
            repo.add_export_failure(ExportFailure(
                fid, t, d.meeting_id, "tech", f"{tool.name} 인증이 만료되어 보내지 못했어요.",
                "401 Unauthorized · 토큰 만료", when))
            state.export_resent.discard(fid)
            # 관리자에게 알림: 기술 이슈라 사용자가 해결할 수 없음
            title = repo.meeting(d.meeting_id).title
            repo.push_notification("admin", "fail", f"「{title}」 결과를 {tool.name}(으)로 보내지 못했어요. "
                                   f"{tool.name} 인증 만료 · 확인이 필요해요.", when)
        state.status_override[d.meeting_id] = "fail" if p.failed else "done"


# ---------------------------------------------------------------- 화면용 계산
@dataclass
class Progress:
    conn: str = "connecting"  # connecting | open | closed
    steps: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(("save", "todo", "send", "export", "wiki"), "wait"))
    recipients: dict[str, str] = field(default_factory=dict)  # wait | sending | ok | fail
    exports: dict[str, str] = field(default_factory=dict)  # 도구 id → wait | sending | ok | fail
    log: list[dict] = field(default_factory=list)
    ended: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for v in [*self.recipients.values(), *self.exports.values()] if v == "fail")

    @property
    def ok(self) -> int:
        return sum(1 for v in self.recipients.values() if v == "ok")

    @property
    def export_ok(self) -> int:
        return sum(1 for v in self.exports.values() if v == "ok")


def _settle(p: Progress, which: str) -> None:
    vals = (p.recipients if which == "send" else p.exports).values()
    if all(v in ("ok", "fail") for v in vals):
        p.steps[which] = "fail" if any(v == "fail" for v in vals) else "done"


def _apply(p: Progress, e: Event, mid: str, attempt: int) -> None:
    people = repo.people()
    todo_n = len(repo.meeting(mid).todos)
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
        _settle(p, "send")
        name = people[e.to].name
        data = f'{{"to":"{name}","status":"ok"}}' if e.ok else f'{{"to":"{name}","error":"timeout"}}'
        p.log.append({"ev": "delivery.sent" if e.ok else "delivery.failed", "data": data, "fail": not e.ok})
    elif e.kind == "export":
        p.exports[e.to] = "ok" if e.ok else "fail"
        _settle(p, "export")
        p.log.append({"ev": "export.sent" if e.ok else "export.failed", "data": f'{{"tool":"{e.to}"}}', "fail": not e.ok})
    elif e.kind == "end":
        p.conn, p.ended = "closed", True
        p.log.append({"ev": "end", "data": f'{{"failed":{p.failed}}}', "fail": p.failed > 0})


def progress(state: AppState) -> Progress:
    d = state.delivery
    mid = d.meeting_id
    fids = _snapshot(state)
    p = Progress(recipients=dict.fromkeys(recipients(mid), "wait"), exports=dict.fromkeys(tools(mid), "wait"))
    idx = arrived_count(state)
    if d.attempt == 1:
        evs = _first_events(mid, fids, _expired_tools(state, mid))
    else:
        for e in _first_events(mid, fids, []):  # 첫 전송 결과(그때 실패한 것) 위에 다시 보낸 결과를 얹음
            _apply(p, e, mid, 1)
        evs = _retry_events(mid, fids)
        if idx == 0:
            p.conn, p.ended = "connecting", False
    for e in evs[:idx]:
        _apply(p, e, mid, d.attempt)
    nxt = evs[idx] if idx < len(evs) else None
    if nxt and nxt.kind == "sent":
        p.recipients[nxt.to] = "sending"
        p.steps["send"] = "run"
    if nxt and nxt.kind == "export":
        p.exports[nxt.to] = "sending"
        p.steps["export"] = "run"
    if nxt and nxt.kind == "step":
        p.steps[nxt.step] = "run"
    return p
