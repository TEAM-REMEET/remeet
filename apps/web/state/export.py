"""외부 도구(MCP) 전송 시뮬레이션 — 결과 전달(참석자에게 보내기)이 끝난 뒤 주관자가 도구를 골라 보냄.

state/delivery.py 와 같은 방식: 시작 시각부터 흐른 시간으로 '지금까지 도착한 이벤트 수'를 계산합니다.

흐름: 도구 고르기 → 보내기 → 도구별 결과 → 전달 완료
전송 실패 (목업)
  - 외부 도구 전송 실패 기록(data/mock/integrations.json 의 failures)이 있는 회의 · 도구
      · 단순 이슈 (예: Slack 네트워크 오류) → 「실패한 도구 다시 보내기」로 해결
  - 인증이 만료된 도구(목업: Jira)를 고르면
      · 기술 이슈로 실패 → 전송이 끝나는 그 자리에서 「관리자에게 문의」 모달 (다시 보내기 없음)
      · 실패 기록을 남겨 관리자 화면(MCP 관리 · 전송 실패 기록)에도 보여요
Jira · 메일 (연결됨 · on) 은 실제로 보냄
  - Jira (POST /api/exports/jira): 액션 아이템마다 이슈 하나
  - 메일 (POST /api/exports/mail): 최종 회의록(회의록 다운로드와 같은 글)을 참석자마다 한 통
  - 연결 · 설정 실패 → 기술 이슈 (관리자 문의) · 일부만 실패 → 단순 이슈 (실패한 아이템 · 사람만 다시 보내기)
  - 진행 모양(대기 → 보내는 중 → 결과)은 다른 도구처럼 시간으로 보여 주고, 결과만 실제 응답을 따라요
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from config import DELIVERY_EVENT_INTERVAL, DELIVERY_FIRST_EVENT_DELAY, YEAR
from data import api
from data import repository as repo
from data.models import ExportFailure, IntegrationTool
from state import selectors as sel
from state.store import AppState, ExportRun

DEFAULT_TOOLS = ("slack", "jira")  # 처음 화면에서 미리 골라 둘 도구 (회의 결과 공유 · 액션 아이템 이슈)


# ---------------------------------------------------------------- 고를 수 있는 도구
def available_tools() -> list[tuple[IntegrationTool, str]]:
    """관리자가 연결한 도구 [(도구, 상태 on | expired)] · 연결하지 않은(off) 도구는 빼요."""
    st = repo.tool_statuses()
    return [(t, st.get(t.id, "off")) for t in repo.integrations()["tools"] if st.get(t.id, "off") != "off"]


def default_pick(mid: str) -> tuple[str, ...]:
    """처음에 골라 둘 도구: 이 회의에 실패 기록이 있는 도구, 없으면 Slack · Jira (연결된 것만)."""
    connected = {t.id for t, _ in available_tools()}
    mock = [f.tool for f in repo.integrations()["failures"] if f.meeting == mid]
    return tuple(t for t in (dict.fromkeys(mock) or DEFAULT_TOOLS) if t in connected)


# ---------------------------------------------------------------- Jira · 메일 (실제 전송)
LIVE = ("jira", "mail")  # 실제로 보내는 도구 (나머지는 시뮬레이션)


def live(state: AppState, t: str) -> bool:
    """이 도구를 실제로 보내는 전송인지: 골랐고 보내기 시작할 때 연결(on) 상태 (인증 만료 표시면 목업처럼 실패만).
    메일은 최종 회의록이 있는 회의만 (목업 회의는 시뮬레이션)."""
    x = state.export
    return (t in LIVE and t in x.picked and t not in x.expired
            and (t != "mail" or x.meeting_id in state.final_summaries))


def failed(state: AppState, t: str) -> bool:
    x = state.export
    return live(state, t) and bool(x.errors.get(t) or any(not r["ok"] for r in x.results.get(t, {}).get("items", ())))


def _iso_due(due: str) -> str | None:
    """'11/2' → '2026-11-02' (Jira 마감일 형식).
    shortcut: 연도는 config.YEAR 로 고정, 마감일에 연도가 저장되면 그 값을 그대로 씀"""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", due or "")
    return f"{YEAR}-{int(m[1]):02d}-{int(m[2]):02d}" if m else None


def _jira_items(state: AppState, mid: str, only: set[str] | None = None) -> list[dict]:
    """보낼 액션 아이템 (담당자는 담당자 추천 · 지정에서 확정한 사람). only: 이 key 만 (다시 보내기)."""
    out = []
    for i, t in sel.todo_items(state, mid):
        key = sel.todo_key(mid, i)
        if only is None or key in only:
            out.append({"key": key, "title": t.text[:255], "owners": list(sel.todo_owner_ids(state, key, t)),
                        "due": _iso_due(t.due)})
    return out


def _request(state: AppState, t: str, only: set[str] | None) -> dict | None:
    """백엔드로 보냄 (only: 이 key 만 · 다시 보내기). 보낼 게 없으면 None."""
    from state.minutes_text import render_minutes

    mid = state.export.meeting_id
    m = repo.meeting(mid)
    if t == "jira":
        items = _jira_items(state, mid, only)
        return repo.export_jira(m.title, items) if items else None
    to = [p for p in mail_recipients(mid) if only is None or p in only]
    return repo.export_mail(m.title, render_minutes(state.final_summaries[mid]), to) if to else None


def mail_recipients(mid: str) -> list[str]:
    """요약본 메일을 받을 사람: 주관자(보내는 나 · 기록용 사본) + 참석자."""
    m = repo.meeting(mid)
    return list(dict.fromkeys((m.host, *m.people)))


def send(state: AppState) -> None:
    """Jira · 메일을 실제로 보냄 · 첫 전송은 전부, 다시 보내기는 실패한 것만 (이미 만든 이슈 · 받은 사람에게 또 보내지 않게).
    연결 · 설정 실패(errors)는 다시 보내도 안 되므로 건너뜀 (관리자 문의)."""
    x = state.export
    for t in LIVE:
        if not live(state, t) or x.errors.get(t):
            continue
        done = x.results.get(t)
        only = {r["key"] for r in done["items"] if not r["ok"]} if done else None
        try:
            res = _request(state, t, only) or {"items": []}
        except api.ApiError as e:
            x.errors[t] = str(e)
            continue
        merged = {r["key"]: r for r in (done or {}).get("items", [])} | {r["key"]: r for r in res["items"]}
        x.results[t] = {**(done or {}), **res, "items": list(merged.values())}


def summary(x: ExportRun, t: str) -> str:
    """도구 줄의 설명: Jira 는 만든 이슈 · 담당자 미지정, 메일은 보낸 사람 · 주소 없는 사람."""
    r = x.results.get(t)
    if not r:
        return ""
    items = r["items"]
    ok = [i for i in items if i["ok"]]
    if t == "mail":
        sent = sum(1 for i in ok if i.get("sent"))
        return f"메일 {sent}/{len(items)}명 보냄" + (f" · 메일 주소 없음 {len(ok) - sent}명" if len(ok) > sent else "")
    text = f"이슈 {len(ok)}/{len(items)}건 만듦" + (f" · {r['project_key']}" if r.get("project_key") else "")
    keys = [i["issue_key"] for i in ok]
    if keys:
        text += " · " + ", ".join(keys[:3]) + (" …" if len(keys) > 3 else "")
    unassigned = sum(1 for i in ok if not i.get("assigned"))
    return text + (f" · 담당자 미지정 {unassigned}건" if unassigned else "")


# ---------------------------------------------------------------- 실패 기록
def _open(state: AppState, fid: str) -> bool:
    """아직 해결 안 된 실패 (내가 다시 보냄 · 관리자가 해결함이 아니면)."""
    return fid not in state.export_resent and not repo.is_resolved(fid)


def open_ids(state: AppState, mid: str) -> tuple[str, ...]:
    return tuple(f.id for f in repo.export_failures(mid) if _open(state, f.id))


def failed_records(state: AppState, p: "Progress") -> list[ExportFailure]:
    """이번 전송에서 실패로 끝난 도구의 실패 기록."""
    return [f for f in repo.export_failures(state.export.meeting_id) if p.exports.get(f.tool) == "fail"]


# ---------------------------------------------------------------- 이벤트
@dataclass(frozen=True)
class Event:
    kind: str  # open | export | end
    to: str = ""  # 도구 id
    ok: bool = True


def _failing(state: AppState) -> set[str]:
    """첫 전송에서 실패할 도구: 남아 있던 실패 기록의 도구 + 인증 만료 도구."""
    x = state.export
    fail = {f.tool for f in repo.export_failures(x.meeting_id) if f.id in x.fids} | set(x.expired)
    if x.attempt == 1:  # 실제로 보낸 도구는 목업 실패 기록이 아니라 응답으로
        for t in LIVE:
            if live(state, t):
                fail.discard(t)
                if failed(state, t):
                    fail.add(t)
    return fail  # 다시 보내기(2)에서는 첫 전송 결과 = 그때 남긴 실패 기록(fids)


def _first_events(state: AppState) -> list[Event]:
    fail = _failing(state)
    return [Event("open")] + [Event("export", to=t, ok=t not in fail) for t in state.export.picked] + [Event("end")]


def _retry_events(state: AppState) -> list[Event]:
    """다시 보내기: 단순 이슈로 실패한 도구만 (목업은 이번엔 성공 · Jira · 메일은 실제 응답). 기술 이슈는 다시 보내지 않아요."""
    x = state.export
    simple = dict.fromkeys(f.tool for f in repo.export_failures(x.meeting_id) if f.id in x.fids and f.kind == "simple")
    return ([Event("open")] + [Event("export", to=t, ok=not failed(state, t)) for t in simple if t in x.picked]
            + [Event("end")])


def events(state: AppState) -> list[Event]:
    return _first_events(state) if state.export.attempt == 1 else _retry_events(state)


def arrived_count(state: AppState, now: float | None = None) -> int:
    x = state.export
    total = len(events(state))
    if x.started_at is None:
        return 0
    elapsed = (now or time.time()) - x.started_at
    n = int((elapsed - DELIVERY_FIRST_EVENT_DELAY) // DELIVERY_EVENT_INTERVAL) + 1
    return max(0, min(total, n))


# ---------------------------------------------------------------- 화면용 계산
@dataclass
class Progress:
    conn: str = "connecting"  # connecting | open | closed
    exports: dict[str, str] = field(default_factory=dict)  # 도구 id → wait | sending | ok | fail
    ended: bool = False

    @property
    def failed(self) -> int:
        return sum(1 for v in self.exports.values() if v == "fail")

    @property
    def ok(self) -> int:
        return sum(1 for v in self.exports.values() if v == "ok")


def _apply(p: Progress, e: Event) -> None:
    if e.kind == "open":
        p.conn, p.ended = "open", False
    elif e.kind == "export":
        p.exports[e.to] = "ok" if e.ok else "fail"
    elif e.kind == "end":
        p.conn, p.ended = "closed", True


def progress(state: AppState) -> Progress:
    x = state.export
    p = Progress(exports=dict.fromkeys(x.picked, "wait"))
    idx = arrived_count(state)
    if x.attempt == 1:
        evs = _first_events(state)
    else:
        for e in _first_events(state):  # 첫 전송 결과(그때 실패한 것) 위에 다시 보낸 결과를 얹음
            _apply(p, e)
        evs = _retry_events(state)
        if idx == 0:
            p.conn, p.ended = "connecting", False
    for e in evs[:idx]:
        _apply(p, e)
    nxt = evs[idx] if idx < len(evs) else None
    if nxt and nxt.kind == "export":
        p.exports[nxt.to] = "sending"
    return p


def _save_failure(state: AppState, mid: str, t: str, kind: str, reason: str, code: str) -> None:
    """실패 기록을 DB 에 (POST /api/integrations/failures · tech 면 관리자 알림은 서버가 보냄)."""
    try:
        f = repo.record_failure(mid, t, kind, reason, code)
    except api.ApiError as e:
        state.toast = f"전송 실패 기록을 서버에 저장하지 못했어요. ({e})"
        return
    state.export_resent.discard(f.id)


def _record_failure(state: AppState, mid: str, t: str, when: str) -> None:
    """실제로 보낸 도구(Jira · 메일)의 실패 기록."""
    x = state.export
    name = repo.tool(t).name
    if x.errors.get(t):  # 연결 · 설정 문제: 사용자가 다시 보내도 안 됨 → 관리자
        _save_failure(state, mid, t, "tech", f"{x.errors[t]} · {name} 연동(토큰 · 비밀번호 · 권한) 확인이 필요해요.",
                      f"{name} 연결 실패")
    else:
        items = x.results[t]["items"]
        bad = [r for r in items if not r["ok"]]
        n, done = len(bad), len(items) - len(bad)
        reason = (f"{n}명에게 보내지 못했어요 (받은 {done}명에게는 다시 보내지 않아요)" if t == "mail"
                  else f"이슈 {n}건을 만들지 못했어요 (만든 {done}건은 그대로)")
        _save_failure(state, mid, t, "simple", reason, bad[0]["error"])


def sync(state: AppState) -> None:
    """모든 이벤트가 도착했으면 결과를 확정합니다 (실패 기록 · 관리자 알림 · 회의 상태)."""
    from state.delivery import now_text  # delivery 와 같은 시각 표기

    x = state.export
    if x.started_at is None or x.finished or arrived_count(state) < len(events(state)):
        return
    x.finished = True
    mid = x.meeting_id
    if x.attempt == 2:  # 다시 보내 단순 이슈가 해결됨 (Jira · 메일은 실제로 다 보냈을 때만)
        for f in repo.export_failures(mid):
            if f.id in x.fids and f.kind == "simple" and not failed(state, f.tool):
                state.export_resent.add(f.id)
    for t in LIVE:  # 실제로 보낸 도구의 실패 → 실패 기록 (다시 보내도 남으면 사유만 새로)
        if failed(state, t) and (x.attempt == 1 or not x.errors.get(t)):
            _record_failure(state, mid, t, now_text())
    if x.attempt == 1:
        for t in [t for t in x.picked if t in x.expired]:  # 인증 만료로 실패 → 실패 기록 (관리자 화면 · 알림은 서버가)
            tool = repo.tool(t)
            _save_failure(state, mid, t, "tech", f"{tool.name} 인증이 만료되어 보내지 못했어요.",
                          "401 Unauthorized · 토큰 만료")
    p = progress(state)
    for t, v in p.exports.items():  # 보낸 도구 → 마지막 전송 시각 (관리자 · MCP 관리)
        if v == "ok":
            try:
                repo.record_export(mid, t)
            except api.ApiError:
                pass  # 마지막 전송 시각만 늦게 보일 뿐이라 전송 흐름은 이어가요
    state.status_override[mid] = "fail" if p.failed else "done"
    try:  # 회의 상태도 DB 에 (남은 실패가 있으면 fail, 없으면 done · 서버가 정함)
        repo.finish_record_delivery(mid)
    except api.ApiError as e:
        state.toast = f"전달 결과를 서버에 저장하지 못했어요. ({e})"
