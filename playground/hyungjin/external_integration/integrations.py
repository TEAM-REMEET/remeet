"""담당자 추천 결과를 Jira와 메일로 전달한다."""

import smtplib
from email.message import EmailMessage


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


def mail_message(result, sender, to, issue_url=None):
    """확정된 액션 아이템으로 담당자 알림 메일을 만든다."""
    item = result["action_item"]
    if not all((item.get("title"), sender, to)):
        raise ValueError("제목, 보내는 주소, 받는 주소가 필요합니다.")
    title = " ".join(str(item["title"]).split())  # 제목 줄바꿈은 헤더 주입이 되므로 공백으로 바꾼다
    message = EmailMessage()
    message["Subject"] = f"[RE:MEET] 후속 업무: {title}"
    message["From"], message["To"] = sender, to
    lines = ["회의에서 담당자로 확정된 업무가 있습니다.", "",
             f"업무: {title}",
             f"기한: {item.get('due') or '-'}",
             f"회의: {item.get('meeting_title') or '-'}",
             f"안건: {item.get('agenda_title') or '-'}",
             f"액션 아이템 ID: {item.get('action_item_id') or '-'}"]
    if issue_url:
        lines.append(f"Jira: {issue_url}")
    message.set_content("\n".join(lines))
    return message


def send_mail(config, message):
    """SMTP 서버로 메일을 보낸다. 465는 SSL, 그 밖의 포트는 STARTTLS로 암호화한다."""
    host, port = config.get("SMTP_HOST"), int(config.get("SMTP_PORT") or 587)
    if not all((host, config.get("SMTP_USERNAME"), config.get("SMTP_PASSWORD"))):
        raise ValueError("SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD 설정이 필요합니다.")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server as smtp:
        if port != 465:
            smtp.starttls()
        smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        smtp.send_message(message)
