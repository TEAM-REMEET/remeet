"""담당자 추천 결과를 Jira와 Teams에 전달한다."""

import json
from urllib.parse import quote
from urllib.request import Request, urlopen


def jira_arguments(result, cloud_id, project_key, issue_type, assignee_account_id):
    item = result["action_item"]
    if not all((item.get("title"), cloud_id, project_key, issue_type, assignee_account_id)):
        raise ValueError("제목, Jira Cloud ID, 프로젝트, 이슈 유형, 확정 담당자 계정 ID가 필요합니다.")
    args = {
        "cloudId": cloud_id, "projectKey": project_key, "issueType": issue_type,
        "summary": item["title"], "assignee": assignee_account_id,
        "description": f"회의: {item.get('meeting_title') or '-'}\n안건: {item.get('agenda_title') or '-'}\n액션 아이템 ID: {item['action_item_id']}",
    }
    if item.get("due"):
        args["additional_fields"] = {"duedate": item["due"]}
    return args


async def list_all_tools(session):
    """서버의 페이지별 도구 목록을 모두 가져온다."""
    from mcp.types import PaginatedRequestParams

    tools, cursor, seen = [], None, set()
    while True:
        page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor:
            return tools
        if cursor in seen:
            raise RuntimeError("서버가 같은 페이지를 반복해서 반환했습니다.")
        seen.add(cursor)


async def create_jira_issue(session, result, cloud_id, project_key, issue_type, assignee_account_id):
    """인증 및 초기화된 MCP ClientSession으로 Jira 이슈를 생성한다."""
    return await send_jira_issue(session, jira_arguments(result, cloud_id, project_key, issue_type, assignee_account_id))


async def send_jira_issue(session, args):
    """createJiraIssue 인자를 서버 스키마와 대조한 뒤 전송한다."""
    tool = next((t for t in await list_all_tools(session) if t.name == "createJiraIssue"), None)
    if not tool:
        raise RuntimeError("createJiraIssue 도구가 없습니다. 토큰 스코프와 MCP 쓰기 권한을 확인하세요.")
    # 서버는 모르는 키를 오류 없이 버린다(예: 담당자 누락). 보내기 전에 스키마와 대조한다.
    schema = tool.input_schema
    unknown = set(args) - set(schema.get("properties", {}))
    missing = set(schema.get("required", [])) - set(args)
    if unknown or missing:
        raise RuntimeError(f"createJiraIssue 입력 형식이 변경되었습니다. 알 수 없는 키: {sorted(unknown)}, 누락: {sorted(missing)}")
    response = await session.call_tool(tool.name, args)
    if response.is_error:
        raise RuntimeError(f"Jira 이슈 생성 실패: {response.content}")
    return response


def send_teams_message(access_token, team_id, channel_id, message):
    """사용자 위임 Graph 토큰으로 Teams 채널에 메시지를 보낸다."""
    if not all((access_token, team_id, channel_id, message)):
        raise ValueError("Graph 토큰, 팀 ID, 채널 ID, 메시지가 필요합니다.")
    url = ("https://graph.microsoft.com/v1.0/teams/" + quote(team_id, safe="")
           + "/channels/" + quote(channel_id, safe="") + "/messages")
    body = json.dumps({"body": {"contentType": "text", "content": message}}, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={"Authorization": f"Bearer {access_token}",
                      "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.load(response)
