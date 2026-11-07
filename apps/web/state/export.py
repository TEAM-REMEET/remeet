"""외부 도구(MCP) 전송 시뮬레이션 — 결과 전달(참석자에게 보내기)이 끝난 뒤 주관자가 도구를 골라 보냄.

state/delivery.py 와 같은 방식: 시작 시각부터 흐른 시간으로 '지금까지 도착한 이벤트 수'를 계산합니다.

흐름: 도구 고르기 → 보내기 → 도구별 결과 → 전달 완료
전송 실패 (목업)
  - 외부 도구 전송 실패 기록(data/mock/integrations.json 의 failures)이 있는 회의 · 도구
      · 단순 이슈 (예: Slack 네트워크 오류) → 「실패한 도구 다시 보내기」로 해결
  - 인증이 만료된 도구(목업: Jira)를 고르면
      · 기술 이슈로 실패 → 전송이 끝나는 그 자리에서 「관리자에게 문의」 모달 (다시 보내기 없음)
      · 실패 기록을 남겨 관리자 화면(MCP 관리 · 전송 실패 기록)에도 보여요
Jira (연결됨 · on) 는 실제로 보냄 (POST /api/exports/jira): 액션 아이템마다 이슈 하나
  - 연결 · 설정 실패 → 기술 이슈 (관리자 문의) · 일부 아이템만 실패 → 단순 이슈 (실패한 아이템만 다시 보내기)
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


# ---------------------------------------------------------------- Jira (실제 전송)
def jira_live(x: ExportRun) -> bool:
    """Jira 를 실제로 보내는 전송인지: 골랐고 보내기 시작할 때 연결(on) 상태 (인증 만료 표시면 목업처럼 실패만)."""
    return "jira" in x.picked and "jira" not in x.expired


def jira_failed(x: ExportRun) -> bool:
    return jira_live(x) and bool(x.jira_error or (x.jira and x.jira["created"] < x.jira["total"]))


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


def send_jira(state: AppState) -> None:
    """Jira 로 보냄 · 첫 전송은 전부, 다시 보내기는 실패한 아이템만 (이미 만든 이슈를 또 만들지 않게)."""
    x = state.export
    if not jira_live(x):
        return
    done = x.jira or {"project_key": "", "items": []}
    failed = {r["key"] for r in done["items"] if not r["ok"]} if x.jira else None
    items = _jira_items(state, x.meeting_id, failed)
    if not items:
        x.jira = done if x.jira else {"project_key": "", "created": 0, "total": 0, "items": []}
        return
    try:
        res = repo.export_jira(repo.meeting(x.meeting_id).title, items)
    except api.ApiError as e:
        x.jira_error = str(e)
        return
    x.jira_error = ""
    merged = {r["key"]: r for r in done["items"]} | {r["key"]: r for r in res["items"]}
    rows = list(merged.values())
    x.jira = {"project_key": res["project_key"], "created": sum(r["ok"] for r in rows), "total": len(rows),
              "items": rows}


def jira_summary(x: ExportRun) -> str:
    """Jira 줄의 설명: 이슈 몇 건을 만들었는지 · 담당자를 못 찾은 건."""
    j = x.jira
    if not j:
        return ""
    text = f"이슈 {j['created']}/{j['total']}건 만듦" + (f" · {j['project_key']}" if j["project_key"] else "")
    keys = [r["issue_key"] for r in j["items"] if r["ok"]]
    if keys:
        text += " · " + ", ".join(keys[:3]) + (" …" if len(keys) > 3 else "")
    unassigned = sum(1 for r in j["items"] if r["ok"] and not r.get("assigned"))
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
    if jira_live(x) and x.attempt == 1:  # 실제로 보낸 Jira 는 목업 실패 기록이 아니라 응답으로
        fail.discard("jira")
        if jira_failed(x):
            fail.add("jira")
    return fail  # 다시 보내기(2)에서는 첫 전송 결과 = 그때 남긴 실패 기록(fids)


def _first_events(state: AppState) -> list[Event]:
    fail = _failing(state)
    return [Event("open")] + [Event("export", to=t, ok=t not in fail) for t in state.export.picked] + [Event("end")]


def _retry_events(state: AppState) -> list[Event]:
    """다시 보내기: 단순 이슈로 실패한 도구만 (목업은 이번엔 성공 · Jira 는 실제 응답). 기술 이슈는 다시 보내지 않아요."""
    x = state.export
    simple = dict.fromkeys(f.tool for f in repo.export_failures(x.meeting_id) if f.id in x.fids and f.kind == "simple")
    return ([Event("open")] + [Event("export", to=t, ok=not (t == "jira" and jira_failed(x))) for t in simple
                               if t in x.picked] + [Event("end")])


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


def _record_jira_failure(state: AppState, mid: str, when: str) -> None:
    x = state.export
    fid = f"rt-{mid}-jira"
    if x.jira_error:  # 연결 · 설정 문제: 사용자가 다시 보내도 안 됨 → 관리자
        repo.add_export_failure(ExportFailure(fid, "jira", mid, "tech", x.jira_error, "Jira 연결 실패", when))
        repo.push_notification("admin", "fail", f"「{repo.meeting(mid).title}」 액션 아이템을 Jira 로 보내지 못했어요. "
                               "Jira 연동(토큰 · 권한) 확인이 필요해요.", when)
    else:
        j = x.jira
        first = next(r["error"] for r in j["items"] if not r["ok"])
        repo.add_export_failure(ExportFailure(fid, "jira", mid, "simple",
                                              f"이슈 {j['total'] - j['created']}건을 만들지 못했어요 (만든 {j['created']}건은 그대로)",
                                              first, when))
    state.export_resent.discard(fid)


def sync(state: AppState) -> None:
    """모든 이벤트가 도착했으면 결과를 확정합니다 (실패 기록 · 관리자 알림 · 회의 상태)."""
    from state.delivery import now_text  # delivery 와 같은 시각 표기

    x = state.export
    if x.started_at is None or x.finished or arrived_count(state) < len(events(state)):
        return
    x.finished = True
    mid = x.meeting_id
    if x.attempt == 2:  # 다시 보내 단순 이슈가 해결됨 (Jira 는 실제로 다 만들었을 때만)
        for f in repo.export_failures(mid):
            if f.id in x.fids and f.kind == "simple" and not (f.tool == "jira" and jira_failed(x)):
                state.export_resent.add(f.id)
    if jira_failed(x) and (x.attempt == 1 or not x.jira_error):  # 실제 Jira 실패 → 실패 기록 (다시 보내도 남으면 사유만 새로)
        _record_jira_failure(state, mid, now_text())
    if x.attempt == 1:
        for t in [t for t in x.picked if t in x.expired]:  # 인증 만료로 실패 → 실패 기록 (관리자 화면에도 보임)
            tool = repo.tool(t)
            fid = f"rt-{mid}-{t}"
            when = now_text()
            repo.add_export_failure(ExportFailure(
                fid, t, mid, "tech", f"{tool.name} 인증이 만료되어 보내지 못했어요.",
                "401 Unauthorized · 토큰 만료", when))
            state.export_resent.discard(fid)
            title = repo.meeting(mid).title  # 관리자에게 알림: 기술 이슈라 사용자가 해결할 수 없음
            repo.push_notification("admin", "fail", f"「{title}」 결과를 {tool.name}(으)로 보내지 못했어요. "
                                   f"{tool.name} 인증 만료 · 확인이 필요해요.", when)
    state.status_override[mid] = "fail" if progress(state).failed else "done"
