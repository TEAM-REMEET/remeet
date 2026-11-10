"""외부 도구 전송 · Jira: 회의 액션 아이템 → (담당자 이메일 → Jira 계정) → 아이템마다 이슈 하나.

한 건이 실패해도 나머지는 계속 만들고 아이템별 결과를 돌려준다.
화면은 실패한 아이템(key)만 다시 보내므로 이미 만든 이슈가 두 번 생기지 않는다.
담당자를 Jira 에서 못 찾으면 담당자 없이 만든다 (assigned=False).
후속 업무 연결: 새로 만든 이슈가 이전 회의 액션의 후속 업무(Neo4j FOLLOWS_UP)이고 그 이전 업무도 Jira 로 보낸 적이 있으면
「Relates」로 잇는다 (linked_to). 연결은 덤이라 실패해도 로그만 · 이슈 결과는 그대로.
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
    """사람 id → 이메일 (= Jira 계정 이메일이라고 봄) · PostgreSQL accounts (Neo4j 구성원이 복사된 명부)."""
    from followup.adapters.postgres import people

    return people.emails_of(user_ids)


LINK_TYPE = "Relates"  # 후속 업무 ↔ 이전 업무 (Jira 기본 링크 종류 · 사이트마다 다르면 listJiraIssueLinkTypes 로 확인)


def follow_ups(todo_ids: list[str]) -> dict[str, list[str]]:
    """할 일 id → 그 일이 이어받은 이전 회의 할 일 id 들 (Neo4j FOLLOWS_UP · 적재할 때 LLM 이 판단해 둠).
    할 일 id(PG) ↔ 액션 id(Neo4j) 는 meeting_todos.item_id 로 잇는다. 직접 추가한 일(item_id 없음)은 빠짐."""
    from followup.adapters.neo4j.driver import get_driver
    from followup.adapters.postgres.db import query

    ids = [int(t) for t in todo_ids if str(t).isdigit()]
    if not ids:
        return {}
    mine = {r["item_id"]: str(r["id"]) for r in query(
        "SELECT id, item_id FROM meeting_todos WHERE id = ANY(%s) AND item_id IS NOT NULL", (ids,))}
    if not mine:
        return {}
    records, _, _ = get_driver().execute_query(
        "MATCH (a:ActionItem)-[:FOLLOWS_UP]->(p:ActionItem) WHERE a.action_item_id IN $ids "
        "RETURN a.action_item_id AS id, p.action_item_id AS prev", ids=list(mine), routing_="r")
    pairs = [(r["id"], r["prev"]) for r in records]
    prev = {r["item_id"]: str(r["id"]) for r in query(
        "SELECT id, item_id FROM meeting_todos WHERE item_id = ANY(%s) AND deleted_at IS NULL", ([p for _, p in pairs],))}
    out: dict[str, list[str]] = {}
    for cur, p in pairs:
        if p in prev:
            out.setdefault(mine[cur], []).append(prev[p])
    return out


async def _link_follow_ups(jira, cfg: dict[str, str], results: list[dict], prevs: dict[str, list[str]],
                           deadline: float) -> None:
    """새로 만든 이슈 ↔ 이전 업무 이슈 (Jira 로 보낸 적 있는 것만) 를 Relates 로 · 결과에 linked_to 를 적음.
    다시 보내기로 이미 있던 이슈는 건너뜀 (처음 보낼 때 이었음 · 같은 링크를 또 만들지 않게)."""
    new = {r["key"]: r for r in results if r["ok"] and not r.get("existing") and prevs.get(r["key"])}
    if not new or time.monotonic() > deadline:
        return
    want = sorted({p for k in new for p in prevs[k]})
    try:
        keys = await _existing(jira, cfg, [{"key": p} for p in want])  # 이전 업무 할 일 id → 그 Jira 이슈 (라벨)
    except Exception as e:  # noqa: BLE001 - 연결은 덤
        logger.warning("후속 업무 이전 이슈 찾기 실패: %s", e)
        return
    for k, r in new.items():
        for p in prevs[k]:
            if p not in keys or time.monotonic() > deadline:
                continue
            try:
                await jira.link(cfg["JIRA_CLOUD_ID"], r["issue_key"], keys[p]["issue_key"], LINK_TYPE)
                r.setdefault("linked_to", []).append(keys[p]["issue_key"])
            except Exception as e:  # noqa: BLE001
                logger.warning("후속 업무 연결 실패 (%s → %s): %s", r["issue_key"], keys[p]["issue_key"], e)


def project_key(cfg: dict[str, str]) -> str:
    # shortcut: 조직 기본 Jira 프로젝트 하나, 프로젝트(폴더)별 Jira 키 칸이 생기면 project_id 로 찾고 없을 때만 이 값
    return cfg["JIRA_PROJECT_KEY"]


def _description(meeting_title: str, item: dict, meeting_date: str | None = None) -> str:
    """이슈 설명 (마크다운 목록 → Jira 가 ADF 로 바꿈) · 회의 · 날짜 · 안건만 짧게."""
    lines = [f"- **회의**: {meeting_title}" + (f" ({meeting_date})" if meeting_date else "")]
    if item.get("agenda"):
        lines.append(f"- **안건**: {item['agenda']}")
    lines.append(f"- RE:MEET 액션 아이템 #{item['key']}")
    return "\n".join(lines)


def label(key: str) -> str:
    """할 일 하나 = 라벨 하나 (key = 할 일 id). 만들기 전에 이 라벨을 찾아서 같은 할 일로 이슈가 두 번 생기지 않게."""
    return f"remeet-todo-{key}"


async def _existing(jira, cfg: dict[str, str], items: list[dict]) -> dict[str, dict]:
    """이미 만든 이슈 {item key: {issue_key, assigned}}. 검색이 실패하면 예외 → 아무것도 만들지 않음 (중복보다 안전).
    shortcut: Jira 검색은 이슈를 만든 직후 몇 초 늦게 반영됨, 그 사이 다시 보내면 드물게 중복 (실패한 것만 다시 보내서 좁힘)"""
    if not items:  # 연결 확인만 하는 0건 전송 · labels in () 는 잘못된 JQL
        return {}
    by_label = {label(i["key"]): i["key"] for i in items}
    jql = "labels in (" + ", ".join(f'"{lb}"' for lb in by_label) + ")"  # key 는 라우트가 [\w-] 만 허용
    rows = await jira.search(cfg["JIRA_CLOUD_ID"], jql, ["labels", "assignee"], min(100, 2 * len(items)))
    found: dict[str, dict] = {}
    for r in rows:
        for lb in r["fields"].get("labels") or []:
            if lb in by_label:
                found.setdefault(by_label[lb], {"issue_key": r["key"], "assigned": bool(r["fields"].get("assignee"))})
    return found


async def _send(jira, cfg: dict[str, str], meeting_title: str, items: list[dict], emails: dict[str, str],
                meeting_date: str | None = None, prevs: dict[str, list[str]] | None = None) -> list[dict]:
    accounts: dict[str, str | None] = {}  # 이메일 → accountId (한 번만 찾음)
    out = []
    deadline = time.monotonic() + settings.EXPORT_BUDGET_SECONDS
    done = await _existing(jira, cfg, items)
    for item in items:
        if item["key"] in done:  # 이미 만든 이슈 (다시 보내기 · 시간 초과 뒤 · 새로고침 뒤) → 새로 만들지 않음
            out.append({"key": item["key"], "ok": True, **done[item["key"]], "existing": True})
            continue
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
                    "summary": item["title"], "description": _description(meeting_title, item, meeting_date),
                    "labels": [label(item["key"])]}
            if account:
                args["assignee"] = account
            if item.get("due"):
                args["additional_fields"] = {"duedate": item["due"]}
            out.append({"key": item["key"], "ok": True, "issue_key": await jira.create_issue(args),
                        "assigned": bool(account)})
        except Exception as e:  # 이 아이템만 실패로 두고 다음 아이템 계속
            logger.warning("Jira 이슈 생성 실패 (%s): %s", item["key"], e)
            out.append({"key": item["key"], "ok": False, "error": str(e)[:200]})
    await _link_follow_ups(jira, cfg, out, prevs or {}, deadline)
    return out


def export(meeting_title: str, items: list[dict], *, meeting_date: str | None = None, connect: Callable | None = None,
           emails: Callable[[set[str]], dict[str, str]] = emails_of,
           follows: Callable[[list[str]], dict[str, list[str]]] | None = None) -> dict[str, Any]:
    """items: [{key, title, owners: [user_id], due: "YYYY-MM-DD" | None}] → {project_key, created, total, items}
    items[i] 결과에 linked_to: [이전 업무 이슈 키] (후속 업무로 이은 것 · 없으면 빠짐)."""
    cfg = settings.jira_config()
    if cfg is None:
        raise JiraNotConfigured("Jira 연동 설정이 없어요.")
    found = emails({u for item in items for u in item["owners"]})
    try:
        prevs = (follows or follow_ups)([item["key"] for item in items])
    except Exception as e:  # noqa: BLE001 - Neo4j · DB 조회 실패 → 연결 없이 이슈만
        logger.warning("후속 업무 조회 실패: %s", e)
        prevs = {}

    async def run():
        async with (connect or jira_mcp.connect)(cfg["ATLASSIAN_EMAIL"], cfg["ATLASSIAN_API_TOKEN"]) as jira:
            return await _send(jira, cfg, meeting_title, items, found, meeting_date, prevs)

    try:
        results = asyncio.run(run())
    except Exception as e:  # 세션을 열지 못함 (아이템별 실패는 _send 가 결과에 담음)
        raise JiraUnavailable(str(e)) from e
    return {"project_key": project_key(cfg), "created": sum(r["ok"] for r in results), "total": len(results),
            "items": results}
