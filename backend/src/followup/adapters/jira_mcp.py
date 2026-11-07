"""Jira (Atlassian MCP): 이메일 → Jira 계정(accountId) 찾기 · 이슈 만들기.

playground/hyungjin/external_integration 에서 확인한 방식 그대로 (API 토큰 Basic 인증 → MCP 세션).
Jira REST API 는 이 토큰(MCP V2 앱)으로 401 이라 쓰지 않는다.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from typing import Any

from followup import settings

ENDPOINT = "https://mcp.atlassian.com/v2/mcp?tools=all"
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


@asynccontextmanager
async def connect(email: str, token: str):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(auth=(email, token), timeout=httpx2.Timeout(settings.JIRA_CALL_TIMEOUT),
                                  follow_redirects=False) as http:
        async with streamable_http_client(ENDPOINT, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield Jira(session)


def _payload(response) -> Any:
    """도구 응답 → JSON (structured_content, 없으면 글 부분을 JSON 으로 읽음)."""
    if response.structured_content is not None:
        return response.structured_content
    text = "".join(getattr(p, "text", "") for p in response.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


def _walk(value: Any):
    """중첩된 dict · list 안의 dict 를 모두."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


def pick_account(payload: Any, email: str) -> str | None:
    """lookupJiraAccountId 결과에서 이 이메일의 accountId. 이메일이 보이면 같은 사람, 안 보이면 결과가 한 명일 때만."""
    users = [d for d in _walk(payload) if d.get("accountId")]
    same = [u for u in users if (u.get("emailAddress") or "").lower() == email.lower()]
    if same:
        return same[0]["accountId"]
    return users[0]["accountId"] if len(users) == 1 and not users[0].get("emailAddress") else None


def pick_issue_key(payload: Any) -> str | None:
    if isinstance(payload, str):
        m = re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", payload)
        return m.group(0) if m else None
    return next((d["key"] for d in _walk(payload) if isinstance(d.get("key"), str) and ISSUE_KEY.match(d["key"])), None)


class Jira:
    def __init__(self, session):
        self.session = session
        self._schemas: dict[str, dict] | None = None

    async def _call(self, name: str, args: dict) -> Any:
        if self._schemas is None:
            self._schemas = await self._list_schemas()
        schema = self._schemas.get(name)
        if schema is None:
            raise RuntimeError(f"{name} 도구가 없어요. 토큰 스코프와 MCP 쓰기 권한을 확인해 주세요.")
        # 서버는 모르는 키를 오류 없이 버림 (예: 담당자 누락) → 보내기 전에 입력 형식과 대조
        unknown = set(args) - set(schema.get("properties", {}))
        missing = set(schema.get("required", [])) - set(args)
        if unknown or missing:
            raise RuntimeError(f"{name} 입력 형식이 바뀌었어요. 모르는 키: {sorted(unknown)}, 빠진 키: {sorted(missing)}")
        response = await self.session.call_tool(name, args)
        if response.is_error:
            raise RuntimeError(f"{name} 실패: " + "".join(getattr(p, "text", "") for p in response.content)[:300])
        return _payload(response)

    async def _list_schemas(self) -> dict[str, dict]:
        from mcp.types import PaginatedRequestParams

        out, cursor, seen = {}, None, set()
        while True:
            page = await self.session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
            out.update({t.name: t.input_schema for t in page.tools})
            cursor = page.next_cursor
            if not cursor:
                return out
            if cursor in seen:
                raise RuntimeError("MCP 서버가 같은 도구 목록 페이지를 반복해서 돌려줬어요.")
            seen.add(cursor)

    async def find_account(self, cloud_id: str, email: str) -> str | None:
        return pick_account(await self._call("lookupJiraAccountId", {"cloudId": cloud_id, "query": email}), email)

    async def create_issue(self, args: dict) -> str:
        key = pick_issue_key(await self._call("createJiraIssue", args))
        if not key:
            raise RuntimeError("Jira 가 이슈 키를 돌려주지 않았어요.")
        return key
