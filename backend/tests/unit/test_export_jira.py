"""POST /api/exports/jira · 외부 도구 전송 Jira (가짜 Jira 세션 · 실제 Atlassian 호출 없음)"""
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.adapters import jira_mcp
from followup.application import export_jira
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user

ENV = {"ATLASSIAN_EMAIL": "bot@x.com", "ATLASSIAN_API_TOKEN": "t", "JIRA_CLOUD_ID": "c1", "JIRA_PROJECT_KEY": "KAN",
       "JIRA_ISSUE_TYPE": ""}


class FakeJira:
    def __init__(self, accounts=None, fail_titles=(), existing=None, search_error=None):
        self.accounts, self.fail_titles = accounts or {}, set(fail_titles)
        self.existing, self.search_error = existing or {}, search_error  # existing: 라벨 → 이미 있는 이슈 키
        self.lookups, self.created, self.searched = [], [], []

    async def search(self, cloud_id, jql, fields, max_results):
        if self.search_error:
            raise self.search_error
        self.searched.append(jql)
        return [{"key": k, "fields": {"labels": [lb], "assignee": None}} for lb, k in self.existing.items() if f'"{lb}"' in jql]

    async def find_account(self, cloud_id, email):
        self.lookups.append(email)
        return self.accounts.get(email)

    async def create_issue(self, args):
        if args["summary"] in self.fail_titles:
            raise RuntimeError("createJiraIssue 실패")
        self.created.append(args)
        return f"KAN-{len(self.created)}"


def connect_to(fake):
    @asynccontextmanager
    async def connect(email, token):
        yield fake
    return connect


def item(key, title, owners=(), due=None):
    return {"key": key, "title": title, "owners": list(owners), "due": due}


class ExportTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_configured(self):
        with patch.dict("os.environ", {"JIRA_CLOUD_ID": ""}):
            with self.assertRaises(export_jira.JiraNotConfigured):
                export_jira.export("회의", [item("40", "할 일")], emails=lambda ids: {})

    def test_empty_items_checks_connection_only(self):
        fake = FakeJira()
        res = export_jira.export("회의", [], connect=connect_to(fake), emails=lambda ids: {})
        self.assertEqual((res["created"], res["total"], res["items"]), (0, 0, []))
        self.assertEqual(fake.searched, [])  # 빈 JQL 검색을 보내지 않음

    def test_partial_failure_and_assignee(self):
        fake = FakeJira(accounts={"a@x.com": "acc-a"}, fail_titles={"실패할 일"})
        res = export_jira.export(
            "주간 회의",
            [item("40", "PG 비교", ["u1"], "2026-11-10"), item("41", "실패할 일", ["u1"]),
             item("42", "담당자 Jira 없음", ["u2"]), item("43", "담당자 미정")],
            connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com", "u2": "b@x.com"})
        self.assertEqual((res["project_key"], res["created"], res["total"]), ("KAN", 3, 4))
        by_key = {r["key"]: r for r in res["items"]}
        self.assertEqual(by_key["40"], {"key": "40", "ok": True, "issue_key": "KAN-1", "assigned": True})
        self.assertFalse(by_key["41"]["ok"])
        self.assertEqual((by_key["42"]["ok"], by_key["42"]["assigned"]), (True, False))
        self.assertFalse(by_key["43"]["assigned"])
        self.assertEqual(fake.lookups, ["a@x.com", "b@x.com"])  # 같은 이메일은 한 번만 찾음
        first = fake.created[0]
        self.assertEqual((first["assignee"], first["issueType"], first["additional_fields"]),
                         ("acc-a", "작업", {"duedate": "2026-11-10"}))
        self.assertNotIn("assignee", fake.created[1])

    def test_assignee_falls_back_to_next_owner_in_jira(self):
        fake = FakeJira(accounts={"b@x.com": "acc-b"})
        res = export_jira.export("회의", [item("40", "같이 할 일", ["u1", "u2"])],
                                 connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com", "u2": "b@x.com"})
        self.assertTrue(res["items"][0]["assigned"])
        self.assertEqual(fake.created[0]["assignee"], "acc-b")  # 첫 담당자가 Jira 에 없으면 다음 담당자

    def test_lookup_error_still_creates_issue(self):
        fake = FakeJira()

        async def broken(cloud_id, email):
            raise RuntimeError("lookupJiraAccountId 입력 형식이 바뀌었어요")
        fake.find_account = broken
        res = export_jira.export("회의", [item("40", "할 일", ["u1"])],
                                 connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com"})
        self.assertEqual(res["items"][0], {"key": "40", "ok": True, "issue_key": "KAN-1", "assigned": False})

    def test_description_has_meeting_date_and_agenda(self):
        fake = FakeJira()
        export_jira.export("주간 회의", [{**item("40", "견적 비교"), "agenda": "커버 디자인"}, item("41", "안건 없음")],
                           meeting_date="2026-11-10", connect=connect_to(fake), emails=lambda ids: {})
        self.assertEqual(fake.created[0]["description"],
                         "- **회의**: 주간 회의 (2026-11-10)\n- **안건**: 커버 디자인\n- RE:MEET 액션 아이템 #40")
        self.assertNotIn("안건", fake.created[1]["description"])  # 안건을 모르면 줄을 빼요

    def test_never_creates_same_todo_twice(self):
        fake = FakeJira(existing={"remeet-todo-40": "KAN-9"})  # 40 은 예전에 이미 만듦 (시간 초과 · 새로고침 뒤 다시 보내기)
        res = export_jira.export("회의", [item("40", "이미 있음"), item("41", "새 일")],
                                 connect=connect_to(fake), emails=lambda ids: {})
        by_key = {r["key"]: r for r in res["items"]}
        self.assertEqual(by_key["40"], {"key": "40", "ok": True, "issue_key": "KAN-9", "assigned": False, "existing": True})
        self.assertEqual([c["summary"] for c in fake.created], ["새 일"])  # 40 은 다시 만들지 않음
        self.assertEqual(fake.created[0]["labels"], ["remeet-todo-41"])
        self.assertEqual(fake.searched, ['labels in ("remeet-todo-40", "remeet-todo-41")'])

    def test_search_failure_creates_nothing(self):
        fake = FakeJira(search_error=RuntimeError("search 실패"))
        with self.assertRaises(export_jira.JiraUnavailable):  # 이미 있는지 모르면 만들지 않음 (중복보다 안전)
            export_jira.export("회의", [item("40", "할 일")], connect=connect_to(fake), emails=lambda ids: {})
        self.assertEqual(fake.created, [])

    def test_over_budget_leaves_rest_for_retry(self):
        fake = FakeJira()
        with patch("followup.settings.EXPORT_BUDGET_SECONDS", -1):
            res = export_jira.export("회의", [item("40", "할 일"), item("41", "할 일 2")],
                                     connect=connect_to(fake), emails=lambda ids: {})
        self.assertEqual((res["created"], fake.created), (0, []))
        self.assertTrue(all(not r["ok"] and "시간 초과" in r["error"] for r in res["items"]))

    def test_connection_failure(self):
        @asynccontextmanager
        async def broken(email, token):
            raise ConnectionError("401 invalid_token")
            yield
        with self.assertRaises(export_jira.JiraUnavailable):
            export_jira.export("회의", [item("40", "할 일")], connect=broken, emails=lambda ids: {})
        with self.assertRaises(export_jira.JiraUnavailable):  # 만들 이슈가 없어도 연결은 확인
            export_jira.export("회의", [], connect=broken, emails=lambda ids: {})


class ParseTests(unittest.TestCase):
    def test_pick_account(self):
        users = {"users": [{"accountId": "a1", "emailAddress": "A@x.com"}, {"accountId": "a2", "emailAddress": "b@x.com"}]}
        self.assertEqual(jira_mcp.pick_account(users, "a@x.com"), "a1")
        self.assertEqual(jira_mcp.pick_account([{"accountId": "only"}], "z@x.com"), "only")  # 이메일 비공개 · 한 명
        self.assertIsNone(jira_mcp.pick_account([{"accountId": "1"}, {"accountId": "2"}], "z@x.com"))  # 누군지 모름
        self.assertIsNone(jira_mcp.pick_account([{"accountId": "1", "emailAddress": "other@x.com"}], "z@x.com"))

    def test_pick_issue_key(self):
        self.assertEqual(jira_mcp.pick_issue_key({"issue": {"id": "100", "key": "KAN-12"}}), "KAN-12")
        self.assertEqual(jira_mcp.pick_issue_key("Created issue KAN-7 successfully"), "KAN-7")
        self.assertIsNone(jira_mcp.pick_issue_key({"key": "not-a-key"}))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)
        self.body = {"meeting_title": "주간 회의", "items": [item("40", "할 일", ["u1"], "2026-11-10")]}

    def login(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1"}

    def test_requires_login(self):
        with patch.object(export_jira, "export") as run:
            self.assertEqual(self.client.post("/api/exports/jira", json=self.body).status_code, 401)
        run.assert_not_called()

    def test_bad_input(self):
        self.login()
        bad_due = {**self.body, "items": [item("40", "할 일", due="11/10")]}
        with patch.object(export_jira, "export") as run:
            self.assertEqual(self.client.post("/api/exports/jira", json=bad_due).status_code, 422)
            jql_injection = {**self.body, "items": [item('40") OR project = KAN OR ("', "할 일")]}  # key 는 라벨 · JQL 에 들어감
            self.assertEqual(self.client.post("/api/exports/jira", json=jql_injection).status_code, 422)
        run.assert_not_called()

    def test_ok(self):
        self.login()
        result = {"project_key": "KAN", "created": 1, "total": 1, "items": []}
        with patch.object(export_jira, "export", return_value=result) as run:
            res = self.client.post("/api/exports/jira", json=self.body)
        self.assertEqual((res.status_code, res.json()), (200, result))
        run.assert_called_once_with("주간 회의", self.body["items"], meeting_date=None)

    def test_empty_items_still_checks_connection(self):
        self.login()  # 만들 이슈가 없어도 연결 실패면 502 (성공으로 보이면 안 됨)
        with patch.object(export_jira, "export", side_effect=export_jira.JiraUnavailable("401")) as run:
            res = self.client.post("/api/exports/jira", json={**self.body, "items": []})
        self.assertEqual(res.status_code, 502)
        run.assert_called_once_with("주간 회의", [], meeting_date=None)

    def test_errors_hide_internals(self):
        self.login()
        for exc, code in ((export_jira.JiraNotConfigured("x"), 503), (export_jira.JiraUnavailable("비밀-토큰"), 502)):
            with patch.object(export_jira, "export", side_effect=exc):
                res = self.client.post("/api/exports/jira", json=self.body)
            self.assertEqual(res.status_code, code)
            self.assertNotIn("비밀", res.text)


if __name__ == "__main__":
    unittest.main()
