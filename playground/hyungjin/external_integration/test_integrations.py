import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from integrations import create_jira_issue, jira_arguments, mail_message, send_mail
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

    def test_mail_message(self):
        result = {"action_item": {**self.result["action_item"], "title": "작업\nBcc: x@evil.com"}}
        message = mail_message(result, "from@example.com", "to@example.com", "https://jira/PROJ-1")
        self.assertEqual(message["Subject"], "[RE:MEET] 후속 업무: 작업 Bcc: x@evil.com")
        self.assertIsNone(message["Bcc"], "제목 줄바꿈으로 헤더를 추가할 수 없다")
        body = message.get_content()
        self.assertIn("기한: 2026-11-20", body)
        self.assertIn("Jira: https://jira/PROJ-1", body)
        with self.assertRaises(ValueError):
            mail_message(self.result, "from@example.com", "")

    def test_send_mail_starttls_and_ssl(self):
        config = {"SMTP_HOST": "smtp.example.com", "SMTP_USERNAME": "user", "SMTP_PASSWORD": "pw"}
        message = mail_message(self.result, "from@example.com", "to@example.com")
        with patch("integrations.smtplib.SMTP") as smtp:
            send_mail({**config, "SMTP_PORT": "587"}, message)
        server = smtp.return_value.__enter__.return_value
        smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user", "pw")
        server.send_message.assert_called_once_with(message)
        with patch("integrations.smtplib.SMTP_SSL") as ssl:
            send_mail({**config, "SMTP_PORT": "465"}, message)
        ssl.return_value.__enter__.return_value.starttls.assert_not_called()

    def test_mail_missing_configuration_does_not_print_password(self):
        from mail import settings as mail_settings
        with patch("mail.dotenv_values", return_value={"SMTP_PASSWORD": "secret"}):
            with self.assertRaises(ValueError) as error:
                mail_settings()
        self.assertNotIn("secret", str(error.exception))

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
