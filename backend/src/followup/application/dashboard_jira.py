"""대시보드 · 내 Jira 할 일: 로그인한 사람이 담당자인 미완료 이슈 (관리자 토큰으로 읽기만).

범위는 백엔드가 정한다: 로그인 토큰의 user_id → 이메일(PostgreSQL accounts) → Jira 계정 → JQL assignee.
요청으로 다른 사람을 고를 수 없다. 상태 변경은 하지 않는다 (각자 Jira 에서 바꿔야 이력에 본인 이름이 남음).
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from followup import settings
from followup.adapters import jira_mcp
from followup.application.export_jira import JiraNotConfigured, JiraUnavailable, emails_of

FIELDS = ["summary", "status", "duedate"]
_ACCOUNT = re.compile(r"^[\w:-]+$")  # Jira accountId (예: 712020:uuid) · JQL 에 넣기 전에 형식 확인
# shortcut: 서버 프로세스 메모리 캐시 (서버 한 대 가정), 여러 대로 늘리면 공용 캐시(Redis 등)로
_cache: dict[str, tuple[float, dict]] = {}


def _issue(row: dict, site: str | None) -> dict:
    f = row["fields"]
    status = f.get("status") or {}
    return {"key": row["key"], "title": f.get("summary") or "", "status": status.get("name") or "",
            "category": (status.get("statusCategory") or {}).get("name") or "", "due": f.get("duedate"),
            "url": f"{site}/browse/{row['key']}" if site else None}


def my_issues(user_id: str, *, refresh: bool = False, connect: Callable | None = None,
              emails: Callable[[set[str]], dict[str, str]] = emails_of, now: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """→ {linked, issues: [{key, title, status, category, due, url}], all_url, synced_at}
    linked=False: 이메일이 없거나 Jira 에서 그 이메일의 계정을 못 찾음.
    refresh=True: 「동기화」 · 캐시를 건너뜀 (직전 동기화 뒤 DASHBOARD_REFRESH_MIN_SECONDS 안이면 캐시 그대로).
    synced_at: Jira 에서 실제로 가져온 시각 (한국 시간 ISO)."""
    hit = _cache.get(user_id)
    if hit:
        age = now() - hit[0]
        fresh = settings.DASHBOARD_REFRESH_MIN_SECONDS if refresh else settings.DASHBOARD_CACHE_SECONDS
        if age < fresh:
            return hit[1]
    cfg = settings.jira_config()
    if cfg is None:
        raise JiraNotConfigured("Jira 연동 설정이 없어요.")
    cloud = cfg["JIRA_CLOUD_ID"]
    email = emails({user_id}).get(user_id)

    async def run() -> dict[str, Any]:
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            account = await jira.find_account(cloud, email) if email else None
            if not account or not _ACCOUNT.match(account):
                return {"linked": False, "issues": [], "all_url": None}
            site = await jira.site_url(cloud)
            jql = f'assignee = "{account}" AND statusCategory != Done ORDER BY duedate ASC, updated DESC'
            rows = await jira.search(cloud, jql, FIELDS, settings.DASHBOARD_MAX_ISSUES)
            return {"linked": True, "issues": [_issue(r, site) for r in rows],
                    "all_url": f"{site}/issues/?jql={quote(jql)}" if site else None}

    try:
        out = asyncio.run(run())
    except Exception as e:  # 접속 · 인증 · 권한 · 네트워크 (원인은 라우트가 서버 로그에만 남김)
        raise JiraUnavailable(str(e)) from e
    out["synced_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    _cache[user_id] = (now(), out)
    return out
