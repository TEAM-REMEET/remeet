"""외부 도구 전송 · Jira: 회의 액션 아이템 → (담당자 이메일 → Jira 계정) → 아이템마다 이슈 하나.

한 건이 실패해도 나머지는 계속 만들고 아이템별 결과를 돌려준다.
화면은 실패한 아이템(key)만 다시 보내므로 이미 만든 이슈가 두 번 생기지 않는다.
담당자를 Jira 에서 못 찾으면 담당자 없이 만든다 (assigned=False).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from followup import settings
from followup.adapters import jira_mcp

logger = logging.getLogger(__name__)


class JiraNotConfigured(RuntimeError):
    """Jira 연동 설정(.env)이 비어 있음."""


class JiraUnavailable(RuntimeError):
    """Jira(MCP)에 연결하지 못함 (토큰 만료 · 권한 · 네트워크)."""


def emails_of(user_ids: set[str]) -> dict[str, str]:
    """사람 id → 이메일 (= Jira 계정 이메일이라고 봄).
    shortcut: Neo4j Person.email 에서 읽음, PostgreSQL 이 백엔드에 붙으면 accounts(user_id, email)만 읽는 조회로 교체
    """
    from followup.adapters.neo4j import people

    return {p["user_id"]: p["email"] for p in people.list_people() if p["user_id"] in user_ids and p.get("email")}


def project_key(cfg: dict[str, str]) -> str:
    # shortcut: 조직 기본 Jira 프로젝트 하나, 프로젝트(폴더)별 Jira 키 칸이 생기면 project_id 로 찾고 없을 때만 이 값
    return cfg["JIRA_PROJECT_KEY"]


def _description(meeting_title: str, item: dict) -> str:
    return f"회의: {meeting_title}\nRE:MEET 액션 아이템: {item['key']}"


async def _send(jira, cfg: dict[str, str], meeting_title: str, items: list[dict], emails: dict[str, str]) -> list[dict]:
    accounts: dict[str, str | None] = {}  # 이메일 → accountId (한 번만 찾음)
    out = []
    deadline = time.monotonic() + settings.JIRA_EXPORT_BUDGET_SECONDS
    for item in items:
        if time.monotonic() > deadline:  # 화면이 기다리다 포기하기 전에 끝냄 · 남은 건 다시 보내기로
            out.append({"key": item["key"], "ok": False, "error": "시간 초과 (다시 보내 주세요)"})
            continue
        try:
            account = None
            for email in (emails[u] for u in item["owners"] if u in emails):  # Jira 담당자는 한 명: Jira 에 있는 첫 담당자
                if email not in accounts:
                    try:
                        accounts[email] = await jira.find_account(cfg["JIRA_CLOUD_ID"], email)
                    except Exception as e:  # 담당자 조회만 실패 → 담당자 없이 이슈는 만듦
                        logger.warning("Jira 담당자 조회 실패 (%s): %s", item["key"], e)
                        accounts[email] = None
                if account := accounts[email]:
                    break
            args = {"cloudId": cfg["JIRA_CLOUD_ID"], "projectKey": project_key(cfg), "issueType": cfg["JIRA_ISSUE_TYPE"],
                    "summary": item["title"], "description": _description(meeting_title, item)}
            if account:
                args["assignee"] = account
            if item.get("due"):
                args["additional_fields"] = {"duedate": item["due"]}
            out.append({"key": item["key"], "ok": True, "issue_key": await jira.create_issue(args),
                        "assigned": bool(account)})
        except Exception as e:  # 이 아이템만 실패로 두고 다음 아이템 계속
            logger.warning("Jira 이슈 생성 실패 (%s): %s", item["key"], e)
            out.append({"key": item["key"], "ok": False, "error": str(e)[:200]})
    return out


def export(meeting_title: str, items: list[dict], *, connect: Callable | None = None,
           emails: Callable[[set[str]], dict[str, str]] = emails_of) -> dict[str, Any]:
    """items: [{key, title, owners: [user_id], due: "YYYY-MM-DD" | None}] → {project_key, created, total, items}"""
    cfg = settings.jira_config()
    if cfg is None:
        raise JiraNotConfigured("Jira 연동 설정이 없어요.")
    found = emails({u for item in items for u in item["owners"]})

    async def run():
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            return await _send(jira, cfg, meeting_title, items, found)

    try:
        results = asyncio.run(run())
    except Exception as e:  # 세션을 열지 못함 (아이템별 실패는 _send 가 결과에 담음)
        raise JiraUnavailable(str(e)) from e
    return {"project_key": project_key(cfg), "created": sum(r["ok"] for r in results), "total": len(results),
            "items": results}
