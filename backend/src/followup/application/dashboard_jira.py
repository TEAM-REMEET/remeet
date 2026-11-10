"""대시보드 · 내 Jira 할 일: 로그인한 사람이 담당자인 미완료 이슈 (관리자 토큰으로 읽기만).

범위는 백엔드가 정한다 (요청으로 다른 사람을 고를 수 없음). 두 갈래를 OR 로 합친다:
  ① Jira 담당자가 나:  user_id → 이메일(accounts) → Jira 계정 → assignee
  ② RE:MEET 에서 내가 담당자(공동 포함)인 할 일: user_id → 할 일 id(meeting_todo_owners) → 라벨 remeet-todo-{id}
     Jira 담당자는 한 명뿐이라 ① 만으로는 공동 담당자 · Jira 계정을 못 찾은 사람에게 안 보임 (표는 그대로 읽기만)
상태 변경은 하지 않는다 (각자 Jira 에서 바꿔야 이력에 본인 이름이 남음).
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from followup import settings
from followup.adapters import jira_mcp
from followup.application.export_jira import JiraNotConfigured, JiraUnavailable, emails_of, follow_ups, label

FIELDS = ["summary", "status", "duedate", "labels"]
_ACCOUNT = re.compile(r"^[\w:-]+$")  # Jira accountId (예: 712020:uuid) · JQL 에 넣기 전에 형식 확인
# shortcut: 서버 프로세스 메모리 캐시 (서버 한 대 가정), 여러 대로 늘리면 공용 캐시(Redis 등)로
_cache: dict[str, tuple[float, dict]] = {}
_changes: dict[str, int] = {}  # 사람마다 상태를 바꾼 횟수 · 바꾸기 전에 시작한 조회가 늦게 끝나 캐시를 덮지 않게
_refreshing: set[str] = set()  # 뒤에서 새로 받는 중인 사람 (한 사람에 하나만)
_lock = threading.Lock()
logger = logging.getLogger(__name__)


_TODO_LABEL = re.compile(r"^remeet-todo-(\d+)$")  # export_jira.label 의 반대 · 상세 창에서 RE:MEET 할 일 · 회의를 찾음


def _issue(row: dict, site: str | None) -> dict:
    f = row["fields"]
    status = f.get("status") or {}
    todo = next((int(m[1]) for lb in f.get("labels") or () if (m := _TODO_LABEL.match(lb))), None)
    return {"key": row["key"], "title": f.get("summary") or "", "status": status.get("name") or "",
            "category": (status.get("statusCategory") or {}).get("name") or "", "due": f.get("duedate"),
            "url": f"{site}/browse/{row['key']}" if site else None, "todo_id": todo}


def _my_todo_ids(user_id: str) -> list[int]:
    from followup.adapters.postgres import detail

    return detail.my_todo_ids(user_id, settings.DASHBOARD_MAX_ISSUES)


class NotMine(RuntimeError):
    """대시보드 범위(내가 Jira 담당자 · RE:MEET 에서 내가 담당자인 할 일) 밖의 이슈 → 바꿀 수 없음."""


class NoTransition(RuntimeError):
    """그 상태(완료 · 진행 중)로 바꾸는 전환이 Jira 워크플로에 없음."""


_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")  # JQL 에 넣기 전에 형식 확인


def _context(user_id: str, emails, todo_ids) -> tuple[dict[str, str], str | None, list[str]]:
    cfg = settings.jira_config()
    if cfg is None:
        raise JiraNotConfigured("Jira 연동 설정이 없어요.")
    labels = [label(str(i)) for i in todo_ids(user_id)]  # 할 일 id 는 DB 정수 → 라벨 · JQL 에 그대로 넣어도 안전
    return cfg, emails({user_id}).get(user_id), labels


async def _mine(jira, cloud: str, email: str | None, labels: list[str]) -> tuple[bool, str | None]:
    """→ (Jira 계정을 찾았나, 「내 일」 JQL 조건 (없으면 None)) · 목록과 완료 처리가 같은 범위를 씀."""
    account = await jira.find_account(cloud, email) if email else None
    linked = bool(account and _ACCOUNT.match(account))
    mine = ([f'assignee = "{account}"'] if linked else []) + (
        ["labels in (" + ", ".join(f'"{lb}"' for lb in labels) + ")"] if labels else [])
    return linked, f"({' OR '.join(mine)})" if mine else None


def my_issues(user_id: str, *, refresh: bool = False, connect: Callable | None = None,
              emails: Callable[[set[str]], dict[str, str]] = emails_of,
              todo_ids: Callable[[str], list[int]] = _my_todo_ids, now: Callable[[], float] = time.monotonic,
              spawn: Callable[[Callable[[], None]], None] | None = None) -> dict[str, Any]:
    """→ {linked, issues: [{key, title, status, category, due, url}], done: [같은 모양], all_url, synced_at}
    issues = 미완료 · done = 최근 DASHBOARD_DONE_DAYS 일 안에 완료된 일 (최대 DASHBOARD_MAX_DONE).
    linked=False: 이메일이 없거나 Jira 에서 그 이메일의 계정을 못 찾음 (그래도 ② 할 일 라벨로 찾은 이슈는 보여 줌).
    refresh=True: 「동기화」 · 캐시를 건너뜀 (직전 동기화 뒤 DASHBOARD_REFRESH_MIN_SECONDS 안이면 캐시 그대로).
    synced_at: Jira 에서 실제로 가져온 시각 (한국 시간 ISO).
    캐시가 지났어도 DASHBOARD_STALE_SECONDS 안이면 지난 목록을 바로 주고 Jira 는 뒤에서 새로 받음
    (Jira 조회가 10초 넘게 걸려 홈을 다시 그릴 때마다 기다리지 않게 · 「동기화」는 늘 기다려서 새로)."""
    hit = _cache.get(user_id)
    if hit:
        age = now() - hit[0]
        fresh = settings.DASHBOARD_REFRESH_MIN_SECONDS if refresh else settings.DASHBOARD_CACHE_SECONDS
        if age < fresh:
            return hit[1]
        if not refresh and age < settings.DASHBOARD_STALE_SECONDS:
            with _lock:
                start = user_id not in _refreshing
                _refreshing.add(user_id)
            if start:
                (spawn or _spawn)(lambda: _refresh_quietly(user_id, connect, emails, todo_ids, now))
            return hit[1]
    return _fetch(user_id, connect, emails, todo_ids, now)


def _follow_info(todo_ids: list[int]) -> dict[int, list[dict]]:
    """할 일 id → 그 일이 이어받은 이전 회의 업무 [{todo_id, text, meeting_id, meeting, date}] (Neo4j FOLLOWS_UP · PG)."""
    from followup.adapters.postgres import detail

    prevs = follow_ups([str(t) for t in todo_ids])
    briefs = detail.todo_briefs(sorted({int(p) for ps in prevs.values() for p in ps}))
    return {int(k): [briefs[int(p)] for p in ps if int(p) in briefs] for k, ps in prevs.items()}


def _attach_follows(out: dict[str, Any]) -> None:
    """목록의 RE:MEET 할 일마다 follows (상세 모달 「선행 업무」) · 목록 전체에 DB 조회 한 번씩 (Jira 호출 없음).
    조회가 실패해도 목록은 그대로 (로그만)."""
    items = [i for lst in (out.get("issues") or [], out.get("done") or []) for i in lst if i.get("todo_id")]
    if not items:
        return
    try:
        info = _follow_info([i["todo_id"] for i in items])
    except Exception as e:  # noqa: BLE001 - 덤 정보
        logger.warning("대시보드 선행 업무 조회 실패: %s", e)
        return
    for i in items:
        if info.get(i["todo_id"]):
            i["follows"] = info[i["todo_id"]]


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True, name="dashboard-refresh").start()


def _refresh_quietly(user_id: str, connect, emails, todo_ids, now) -> None:
    """뒤에서 새로 받기 · 실패하면 지난 목록을 그대로 두고 로그만 (화면엔 마지막 동기화 시각이 그대로 보임)."""
    try:
        _fetch(user_id, connect, emails, todo_ids, now)
    except Exception as e:  # noqa: BLE001
        logger.warning("대시보드 Jira 뒤에서 새로 받기 실패 (%s): %s", user_id, e)
    finally:
        with _lock:
            _refreshing.discard(user_id)


def _fetch(user_id: str, connect, emails, todo_ids, now) -> dict[str, Any]:
    """Jira 에서 내 목록을 받아 캐시에 넣음 (받는 동안 상태를 바꿨으면 캐시는 덮지 않음 · 바꾼 값이 되돌아가 보이지 않게)."""
    changes = _changes.get(user_id, 0)
    cfg, email, labels = _context(user_id, emails, todo_ids)
    cloud = cfg["JIRA_CLOUD_ID"]

    async def run() -> dict[str, Any]:
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            linked, mine = await _mine(jira, cloud, email, labels)
            if not mine:
                return {"linked": linked, "issues": [], "done": [], "statuses": [], "all_url": None}
            site = await jira.site_url(cloud)
            jql = f"{mine} AND statusCategory != Done ORDER BY duedate ASC, updated DESC"
            rows = await jira.search(cloud, jql, FIELDS, settings.DASHBOARD_MAX_ISSUES)
            done = await jira.search(
                cloud, f"{mine} AND statusCategory = Done AND statusCategoryChangedDate >= -{settings.DASHBOARD_DONE_DAYS}d "
                       "ORDER BY statusCategoryChangedDate DESC", FIELDS, settings.DASHBOARD_MAX_DONE)
            # 상태 드롭다운 · 색: 프로젝트 워크플로의 상태 목록을 이슈 하나에서 한 번만 물어봄 (이슈마다 묻지 않게)
            # shortcut: 이슈마다 워크플로가 같다고 봄 (한 프로젝트 · 한 이슈 유형), 유형별로 달라지면 이슈별로 물어보기
            first = (rows or done)[:1]
            statuses = list({t["to"].get("name"): {"name": t["to"].get("name"), "category": _category(t), "id": t["id"]}
                             for t in (await jira.transitions(cloud, first[0]["key"]) if first else [])}.values())
            cats = {x["name"]: x["category"] for x in statuses}
            issues = [_issue(r, site) for r in rows]
            finished = [_issue(r, site) for r in done]
            for i in issues:
                i["category_key"] = cats.get(i["status"], "indeterminate")
            for i in finished:
                i["category_key"] = "done"
            return {"linked": linked, "issues": issues, "done": finished, "statuses": statuses,
                    "all_url": f"{site}/issues/?jql={quote(jql)}" if site else None}

    try:
        out = asyncio.run(run())
    except Exception as e:  # 접속 · 인증 · 권한 · 네트워크 (원인은 라우트가 서버 로그에만 남김)
        raise JiraUnavailable(str(e)) from e
    out["synced_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    _attach_follows(out)
    with _lock:
        if _changes.get(user_id, 0) == changes:
            _cache[user_id] = (now(), out)
    return out


def _category(t: dict) -> str:
    return (t["to"].get("statusCategory") or {}).get("key") or "new"


def set_status(user_id: str, user_name: str, key: str, status: str, **deps) -> dict[str, str]:
    """대시보드 상태 드롭다운: 그 이름의 상태로 (Jira 워크플로에 그 상태로 가는 전환이 있어야 함)."""
    return _change(user_id, user_name, key, lambda ts: next((t for t in ts if t["to"].get("name") == status), None),
                   **deps)


def _cached(user_id: str, now: Callable[[], float]) -> dict:
    """1분 안에 불러온 이 사람의 대시보드 (없거나 오래됐으면 빈 dict)."""
    hit = _cache.get(user_id)
    return hit[1] if hit and now() - hit[0] < settings.DASHBOARD_CACHE_SECONDS else {}


def _change(user_id: str, user_name: str, key: str, pick: Callable[[list[dict]], dict | None], *,
            connect: Callable | None = None, emails: Callable[[set[str]], dict[str, str]] = emails_of,
            todo_ids: Callable[[str], list[int]] = _my_todo_ids, now: Callable[[], float] = time.monotonic) -> dict[str, str]:
    """내 일인지 확인하고 Jira 상태를 바꿈 (상태 드롭다운). 누른 사람 댓글은 응답 뒤에 따로 (comment_change).
    빠르게: 1분 안에 불러온 내 목록에 있는 이슈면 「내 일」 확인(계정 찾기 · 검색)을 건너뛰고,
    그때 받아 둔 전환 번호를 씀 (이 이슈에 안 맞으면 그때만 새로 받아 한 번 더).
    → {key, status, category} · 내 일이 아니면 NotMine · 갈 상태가 없으면 NoTransition · 연결 실패는 JiraUnavailable."""
    if not _ISSUE_KEY.match(key):
        raise NotMine(key)
    cached = _cached(user_id, now)
    known = any(i["key"] == key for lst in (cached.get("issues") or [], cached.get("done") or []) for i in lst)
    ready = [{"id": x["id"], "to": {"name": x["name"], "statusCategory": {"key": x["category"]}}}
             for x in cached.get("statuses") or [] if x.get("id")]
    cfg, email, labels = _context(user_id, emails, todo_ids) if not known else (_require_cfg(), None, [])
    cloud = cfg["JIRA_CLOUD_ID"]

    async def run() -> dict[str, str] | NotMine | NoTransition:
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            if not known:  # 목록에 없던 이슈 (직접 API 를 부른 경우 등) → 지금처럼 확인
                _, mine = await _mine(jira, cloud, email, labels)
                try:  # 없는 이슈 키면 Jira 가 검색 자체를 거절함 → 그것도 「내 일 아님」
                    found = bool(mine) and bool(await jira.search(cloud, f'{mine} AND key = "{key}"', ["status"], 1))
                except RuntimeError:  # MCP 도구가 거절 (연결 · 인증 실패는 다른 예외라 아래에서 JiraUnavailable)
                    found = False
                if not found:
                    return NotMine(key)  # 남의 이슈 키를 보내도 여기서 막힘 (예외는 세션을 닫은 뒤에 · 아래 참고)
            t = pick(ready)
            try:
                if t is None:
                    raise LookupError
                await jira.transition(cloud, key, t["id"])
            except (LookupError, RuntimeError):  # 받아 둔 전환 번호가 이 이슈 워크플로에 안 맞음 → 새로 받아 한 번 더
                t = pick(await jira.transitions(cloud, key))
                if t is None:
                    return NoTransition(key)
                await jira.transition(cloud, key, t["id"])
            return {"key": key, "status": t["to"].get("name") or "", "category": _category(t)}

    try:
        out = asyncio.run(run())
    except Exception as e:  # 접속 · 인증 · 권한 · 네트워크
        raise JiraUnavailable(str(e)) from e
    # MCP 세션 안에서 일으킨 예외는 anyio 가 ExceptionGroup 으로 감싸서 종류를 잃음 → 결과로 받아 여기서 일으킴
    if isinstance(out, (NotMine, NoTransition)):
        raise out
    _move_in_cache(user_id, key, out["category"] == "done", out["status"], out["category"])
    return out


def _require_cfg() -> dict[str, str]:
    cfg = settings.jira_config()
    if cfg is None:
        raise JiraNotConfigured("Jira 연동 설정이 없어요.")
    return cfg


def comment_change(user_name: str, key: str, status: str, *, connect: Callable | None = None) -> None:
    """상태를 바꾼 뒤 Jira 에 「누가 바꿨는지」 댓글 (응답을 돌려준 뒤 따로 · 기다리지 않게).
    관리자 토큰으로 바꾸므로 Jira 이력엔 관리자 계정이 남음 → 실제로 누른 사람을 적어 둠. 실패해도 로그만."""
    cfg = settings.jira_config()
    if cfg is None or not _ISSUE_KEY.match(key):
        return

    async def run() -> None:
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            await jira.comment(cfg["JIRA_CLOUD_ID"], key, f"RE:MEET 에서 {user_name}님이 상태를 「{status}」(으)로 변경했어요.")

    try:
        asyncio.run(run())
    except Exception as e:  # noqa: BLE001 - 기록용 댓글 · 상태 변경은 이미 끝남
        logger.warning("Jira 댓글 실패 (%s): %s", key, e)


def _move_in_cache(user_id: str, key: str, done: bool, status: str, category: str = "") -> None:
    """방금 바꾼 일을 캐시 안에서 바로 옮김 (남은 일 ↔ 완료). Jira 검색은 상태 변경이 몇 초 늦게 반영돼서
    바로 다시 불러오면 양쪽에 다 보임 → 캐시를 비우지 않고 고쳐 둠 (다음 동기화 땐 Jira 에도 반영돼 있음)."""
    with _lock:
        _changes[user_id] = _changes.get(user_id, 0) + 1
    hit = _cache.get(user_id)
    data = hit and hit[1]
    if not data or "issues" not in data:
        _cache.pop(user_id, None)
        return
    lists = {"issues": data["issues"], "done": data.get("done") or []}
    item = next((i for lst in lists.values() for i in lst if i["key"] == key), None)
    if item is None:
        _cache.pop(user_id, None)  # 목록에 없던 일 → 다음에 새로 불러옴
        return
    moved = {**item, "status": status, "category_key": category or ("done" if done else item.get("category_key", "new"))}
    src, dst = ("issues", "done") if done else ("done", "issues")
    data[src] = [i for i in lists[src] if i["key"] != key]
    data[dst] = [moved] + [i for i in lists[dst] if i["key"] != key]
