"""Atlassian MCP 연결 및 확정된 액션 아이템 전송."""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import dotenv_values

from integrations import create_jira_issue, list_all_tools

ENDPOINT = "https://mcp.atlassian.com/v2/mcp?tools=all"
ENV_FILE = Path(__file__).with_name(".env")


def settings():
    values = dotenv_values(ENV_FILE)
    required = ("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "JIRA_CLOUD_ID", "JIRA_PROJECT_KEY")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError(".env 설정이 필요합니다: " + ", ".join(missing))
    return values


@asynccontextmanager
async def connect(config):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(auth=(config["ATLASSIAN_EMAIL"], config["ATLASSIAN_API_TOKEN"]),
                                  timeout=httpx2.Timeout(30, read=120), follow_redirects=False) as http:
        async with streamable_http_client(ENDPOINT, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def list_issue_types(session, cloud_id, project_key):
    # The server exposes the complete, paginated catalog with ?tools=all.
    tool = next((t for t in await list_all_tools(session) if t.name == "listJiraProjectIssueTypesMetadata"), None)
    if not tool:
        raise RuntimeError("이슈 유형 조회 도구가 없습니다. MCP 읽기 권한을 확인하세요.")
    properties = tool.input_schema.get("properties", {})
    project_field = next((name for name in ("projectIdOrKey", "projectKey", "projectId") if name in properties), None)
    if not project_field or "cloudId" not in properties:
        raise RuntimeError("Jira 이슈 유형 조회 입력 형식이 변경되었습니다.")
    response = await session.call_tool(tool.name, {"cloudId": cloud_id, project_field: project_key})
    if response.is_error:
        raise RuntimeError(f"이슈 유형 조회 실패: {response.content}")
    return response


async def list_tool_names(session):
    return [tool.name for tool in await list_all_tools(session)]


def show(response):
    if response.structured_content is not None:
        print(json.dumps(response.structured_content, ensure_ascii=False, indent=2))
    else:
        for part in response.content:
            if getattr(part, "text", None):
                print(part.text)


async def run(args):
    config = settings()
    async with connect(config) as session:
        if args.command == "tools":
            names = await list_tool_names(session)
            print("\n".join(names) if names else "서버가 사용 가능한 도구를 반환하지 않았습니다.")
        elif args.command == "types":
            show(await list_issue_types(session, config["JIRA_CLOUD_ID"], config["JIRA_PROJECT_KEY"]))
        else:
            with open(args.input, encoding="utf-8") as file:
                result = json.load(file)
            issue_type = args.issue_type or config.get("JIRA_ISSUE_TYPE")
            assignee = args.assignee or config.get("JIRA_ASSIGNEE_ACCOUNT_ID")
            show(await create_jira_issue(session, result, config["JIRA_CLOUD_ID"],
                                         config["JIRA_PROJECT_KEY"], issue_type, assignee))


def main():
    parser = argparse.ArgumentParser(description="Jira MCP 이슈 유형 조회 및 액션 아이템 생성")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("types", help="프로젝트에서 사용 가능한 이슈 유형 조회")
    commands.add_parser("tools", help="서버가 제공하는 도구 이름 조회 (읽기 전용)")
    create = commands.add_parser("create", help="확정된 추천 결과 JSON으로 Jira 이슈 생성")
    create.add_argument("input", help="액션 아이템 추천 결과 JSON 경로")
    create.add_argument("--issue-type", help="Jira 이슈 유형 (.env 설정보다 우선)")
    create.add_argument("--assignee", help="확정 담당자의 Atlassian accountId (.env 설정보다 우선)")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
