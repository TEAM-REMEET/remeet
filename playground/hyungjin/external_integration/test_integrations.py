import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from integrations import create_jira_issue, jira_arguments, send_teams_message
from jira import connect, list_issue_types, list_tool_names, settings


CREATE_SCHEMA = {  # 2026-11 실제 서버 createJiraIssue 스키마 일부
    "required": ["cloudId", "projectKey", "summary", "issueType"],
    "properties": {name: {} for name in ("cloudId", "projectKey", "summary", "issueType", "description",
                                         "assignee", "additional_fields", "labels", "priority")},
}


class CreateSession:
    def __init__(self, schema=CREATE_SCHEMA, error=False):
        self.schema, self.error, self.calls = schema, error, []

    async def list_tools(self, params=None):
        return SimpleNamespace(tools=[SimpleNamespace(name="createJiraIssue", input_schema=self.schema)], next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(is_error=self.error, content="denied")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.result = {"action_item": {"action_item_id": "a1", "title": "작업", "due": "2026-11-20"}}

    def test_jira_arguments_and_call(self):
        args = jira_arguments(self.result, "cloud", "PROJ", "Task", "account")
        self.assertEqual(args["additional_fields"], {"duedate": "2026-11-20"})
        self.assertEqual(args["assignee"], "account")
        session = CreateSession()
        asyncio.run(create_jira_issue(session, self.result, "cloud", "PROJ", "Task", "account"))
        self.assertEqual(session.calls, [("createJiraIssue", args)])
        with self.assertRaises(ValueError):
            jira_arguments(self.result, "cloud", "PROJ", "Task", "")

    def test_jira_error(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(create_jira_issue(CreateSession(error=True), self.result, "cloud", "P", "Task", "user"))

    def test_schema_drift_blocks_create(self):
        renamed = {"required": ["issueTypeName"], "properties": {k: {} for k in ("cloudId", "projectKey", "summary",
                                                                                "issueTypeName", "description")}}
        session = CreateSession(schema=renamed)
        with self.assertRaises(RuntimeError) as error:
            asyncio.run(create_jira_issue(session, self.result, "cloud", "P", "Task", "user"))
        self.assertIn("assignee", str(error.exception))
        self.assertEqual(session.calls, [])

    def test_teams_request(self):
        from io import BytesIO
        with patch("integrations.urlopen", return_value=BytesIO(b'{"id":"message-id"}')) as send:
            self.assertEqual(send_teams_message("token", "team", "channel/1", "안녕하세요"), {"id": "message-id"})
            request = send.call_args.args[0]
            self.assertIn("channel%2F1/messages", request.full_url)
            self.assertEqual(json.loads(request.data)["body"], {"contentType": "text", "content": "안녕하세요"})
            self.assertEqual(request.get_header("Authorization"), "Bearer token")

    def test_types_paginated_and_schema(self):
        class Session:
            async def list_tools(self, params=None):
                tool = SimpleNamespace(name="listJiraProjectIssueTypesMetadata",
                                       input_schema={"properties": {"cloudId": {}, "projectIdOrKey": {}}})
                return SimpleNamespace(tools=[tool] if params else [], next_cursor=None if params else "next")
            async def call_tool(self, name, arguments):
                assert arguments == {"cloudId": "cloud", "projectIdOrKey": "PROJ"}
                return SimpleNamespace(is_error=False, content=[])
        self.assertFalse(asyncio.run(list_issue_types(Session(), "cloud", "PROJ")).is_error)

    def test_connect_uses_two_streams(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def transport(url, http_client):
            yield "read", "write"

        class Session:
            def __init__(self, read, write):
                assert (read, write) == ("read", "write")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def initialize(self):
                pass
            async def call_tool(self, name, args):
                raise AssertionError("연결 시 사이트 조회 도구를 호출하지 않아야 합니다")

        async def check():
            with patch("mcp.client.streamable_http.streamable_http_client", transport), patch("mcp.ClientSession", Session):
                async with connect({"ATLASSIAN_EMAIL": "test@example.com", "ATLASSIAN_API_TOKEN": "test"}) as session:
                    self.assertIsInstance(session, Session)
        asyncio.run(check())

    def test_tool_names_across_pages(self):
        class Session:
            async def list_tools(self, params=None):
                if params:
                    return SimpleNamespace(tools=[SimpleNamespace(name="createJiraIssue")], next_cursor=None)
                return SimpleNamespace(tools=[SimpleNamespace(name="getJiraIssue")], next_cursor="next")
        self.assertEqual(asyncio.run(list_tool_names(Session())), ["getJiraIssue", "createJiraIssue"])

    def test_missing_configuration_does_not_print_token(self):
        with patch("jira.dotenv_values", return_value={"ATLASSIAN_API_TOKEN": "secret"}):
            with self.assertRaises(ValueError) as error:
                settings()
        self.assertNotIn("secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
