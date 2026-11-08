"""GET /api/dashboard/jira · 대시보드 내 Jira 할 일 (가짜 Jira 세션 · 실제 Atlassian 호출 없음)"""
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.application import dashboard_jira
from followup.application.export_jira import JiraUnavailable
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user

ENV = {"ATLASSIAN_EMAIL": "bot@x.com", "ATLASSIAN_API_TOKEN": "t", "JIRA_CLOUD_ID": "c1", "JIRA_PROJECT_KEY": "KAN"}
SITE = "https://remeet.atlassian.net"
ROW = {"key": "KAN-7", "fields": {"summary": "견적 비교", "duedate": "2026-11-10",
                                  "status": {"name": "진행 중", "statusCategory": {"name": "진행 중"}}}}


class FakeJira:
    def __init__(self, accounts):
        self.accounts, self.jql = accounts, []

    async def find_account(self, cloud_id, email):
        return self.accounts.get(email)

    async def site_url(self, cloud_id):
        return SITE

    async def search(self, cloud_id, jql, fields, max_results):
        self.jql.append(jql)
        return [ROW]


def connect_to(fake):
    @asynccontextmanager
    async def connect(email, token):
        yield fake
    return connect


class MyIssuesTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        dashboard_jira._cache.clear()
        self.addCleanup(dashboard_jira._cache.clear)

    def issues(self, fake, user="u1", **kw):
        return dashboard_jira.my_issues(user, connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com"}, **kw)

    def test_only_my_issues(self):
        fake = FakeJira({"a@x.com": "712020:abc-1"})
        res = self.issues(fake)
        self.assertTrue(res["linked"])
        self.assertIn('assignee = "712020:abc-1"', fake.jql[0])  # 범위 = 로그인한 사람의 Jira 계정
        self.assertIn("statusCategory != Done", fake.jql[0])
        self.assertEqual(res["issues"], [{"key": "KAN-7", "title": "견적 비교", "status": "진행 중", "category": "진행 중",
                                          "due": "2026-11-10", "url": f"{SITE}/browse/KAN-7"}])
        self.assertTrue(res["all_url"].startswith(f"{SITE}/issues/?jql="))

    def test_not_linked(self):
        self.assertEqual(self.issues(FakeJira({}))["linked"], False)  # Jira 에 그 이메일 계정 없음
        dashboard_jira._cache.clear()
        self.assertEqual(self.issues(FakeJira({"a@x.com": "x"}), user="u9")["linked"], False)  # 이메일 없음

    def test_weird_account_id_never_reaches_jql(self):
        fake = FakeJira({"a@x.com": '1" OR assignee is not EMPTY OR "'})
        self.assertFalse(self.issues(fake)["linked"])
        self.assertEqual(fake.jql, [])

    def test_cache_per_user(self):
        fake, t = FakeJira({"a@x.com": "acc"}), [0.0]
        self.issues(fake, now=lambda: t[0])
        t[0] = 30
        self.issues(fake, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 1)  # 60초 안에는 다시 부르지 않음
        t[0] = 61
        self.issues(fake, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 2)

    def test_refresh_skips_cache_but_not_within_min_interval(self):
        fake, t = FakeJira({"a@x.com": "acc"}), [0.0]
        first = self.issues(fake, now=lambda: t[0])
        self.assertRegex(first["synced_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")  # 한국 시간
        t[0] = 5
        self.issues(fake, refresh=True, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 1)  # 직전 동기화 10초 안: 연타는 캐시
        t[0] = 15
        self.issues(fake, refresh=True, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 2)  # 「동기화」는 1분 캐시를 건너뜀

    def test_connection_failure(self):
        @asynccontextmanager
        async def broken(email, token):
            raise ConnectionError("401 invalid token")
            yield
        with self.assertRaises(JiraUnavailable):
            dashboard_jira.my_issues("u1", connect=broken, emails=lambda ids: {"u1": "a@x.com"})


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/dashboard/jira").status_code, 401)

    def test_scope_is_logged_in_user_and_errors_hide_internals(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u2", "permission": "member"}
        with patch.object(dashboard_jira, "my_issues", return_value={"linked": False, "issues": [], "all_url": None}) as m:
            self.assertEqual(self.client.get("/api/dashboard/jira?user_id=u1").status_code, 200)
        m.assert_called_once_with("u2", refresh=False)  # 쿼리로 다른 사람을 넘겨도 로그인한 사람만
        with patch.object(dashboard_jira, "my_issues", side_effect=JiraUnavailable("token=secret")), \
                self.assertLogs("followup.entrypoints.api.dashboard_routes", "ERROR"):
            r = self.client.get("/api/dashboard/jira")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("secret", r.text)
        with patch.dict("os.environ", {"JIRA_CLOUD_ID": ""}):
            dashboard_jira._cache.clear()
            self.assertEqual(self.client.get("/api/dashboard/jira").status_code, 503)


if __name__ == "__main__":
    unittest.main()
