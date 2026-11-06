# Jira · Teams 연결 실험

담당자 추천 JSON의 `action_item`을 사용한다. 추천 후보는 자동으로 확정하지 않는다. 관리자가 선택한 사람의 **Atlassian accountId**를 별도로 전달해야 한다. 서비스의 `user_id`는 Jira accountId가 아니다.

## Jira: Atlassian MCP

`.env`에 `ATLASSIAN_EMAIL`, `ATLASSIAN_API_TOKEN`, `JIRA_CLOUD_ID`, `JIRA_PROJECT_KEY`, `JIRA_ASSIGNEE_ACCOUNT_ID`, `JIRA_ISSUE_TYPE`을 설정한다. `JIRA_ISSUE_TYPE`은 프로젝트에 실제로 있는 이름이어야 한다(remeet KAN 프로젝트: `작업`, `에픽`, `Feature`, `Subtask`). `.env`는 Git에 올리지 않는다.

토큰 발급: [id.atlassian.com API 토큰](https://id.atlassian.com/manage-profile/security/api-tokens?autofillToken&expiryDays=max&appId=mcp-v2&selectedScopes=all)에서 앱을 **Atlassian MCP V2**로 고른다(Jira 앱 토큰은 동작하지 않는다). Jira 스코프 3개(`read`/`write`/`search:jira:agent-interface`)만 넣으면 도구 목록은 보이지만 모든 호출이 `Failed to fetch accessible products: 401`로 실패했다. 2026-11 기준 전체 스코프 토큰으로만 동작을 확인했으며, 최소 스코프 조합은 아직 확인하지 않았다. 조직 관리자 설정(admin.atlassian.com → Connectors → Atlassian MCP 서버)에서 인증 탭의 API 토큰 인증과 권한 탭의 읽기·쓰기·검색이 허용되어 있어야 한다.

이 폴더에서 의존성을 설치하고 이슈 유형을 조회한다.

```powershell
python -m pip install -r requirements.txt
python jira.py types
```

도구를 찾지 못하는 오류가 나면 `python jira.py tools`로 서버가 현재 제공하는 도구 이름을 확인한다. 이 명령은 이슈를 만들지 않는다.

관리자가 액션 아이템과 담당자를 확정한 후, 추천 JSON으로 Jira 이슈를 만든다. 아래 명령은 Jira에 실제 이슈를 생성한다.

```powershell
python jira.py create "추천결과.json" --issue-type 작업 --assignee "확정된 Atlassian accountId"
```

옵션을 생략하면 `.env`의 이슈 유형과 담당자를 사용한다. 추천 후보를 자동으로 선택하지 않는다. 오류 없이 반환된 Jira 응답에 이슈 키가 들어간다. 저장된 기본 담당자는 개인 테스트용으로만 사용하고, 다른 사람에게 배정할 때는 반드시 `--assignee`로 확정된 ID를 전달한다.

다른 Python 코드에서 사용한다면 인증 세션을 열어 호출할 수 있다.

```python
import json
from jira import connect, settings
from integrations import create_jira_issue

with open("recommendation.json", encoding="utf-8") as file:
    result = json.load(file)

# async 함수 안에서 실행한다.
config = settings()
async with connect(config) as session:
    response = await create_jira_issue(
        session, result, config["JIRA_CLOUD_ID"], config["JIRA_PROJECT_KEY"],
        issue_type="작업", assignee_account_id="확정된 담당자의 Atlassian accountId",
    )
print(response.content)
```

요청은 `cloudId`, `projectKey`, `issueType`, `summary`, `description`, `assignee` 및 기한이 있으면 `additional_fields.duedate`를 전송한다. Jira 프로젝트의 이슈 유형과 필수 필드 설정에 따라 추가 필드가 필요할 수 있다. 기한은 Jira의 `YYYY-MM-DD` 형식이어야 한다. 반환값은 MCP 응답이며 이슈 키 추출은 실제 서버 응답을 확인해 호출 측에서 처리한다.

## Teams: Microsoft Graph

```python
from integrations import send_teams_message

message = send_teams_message(
    access_token="Graph 사용자 위임 액세스 토큰",
    team_id="팀 ID", channel_id="채널 ID",
    message="회의 후속 업무: 로그인 화면 디자인\nJira: https://example.atlassian.net/browse/PROJ-1",
)
print(message["id"])
```

Microsoft Entra 앱 등록과 로그인 후 `ChannelMessage.Send` 사용자 위임 권한이 있는 Graph 토큰이 필요하다. 채널 메시지만 지원하며 일반 앱 전용 토큰으로 메시지를 보낼 수 없다. 토큰은 소스나 로그에 기록하지 않는다.

두 함수는 외부에 즉시 생성·전송한다. Jira 성공 후 Teams 실패 시 Jira 응답을 보관하고 Teams만 재시도해야 중복 이슈를 막을 수 있다. 네트워크 시간 초과 시 성공 여부가 불명확할 수 있으므로 외부 결과를 확인한 뒤 재시도한다. 자동 재시도와 중복 방지는 구현하지 않았다.

검증: `python -m unittest discover -s remeet/playground/hyungjin/external_integration -p "test_*.py"`

참고: [Atlassian MCP 도구](https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/), [Graph 채널 메시지](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0)
