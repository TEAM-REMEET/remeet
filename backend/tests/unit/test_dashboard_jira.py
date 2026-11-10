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
ROW = {"key": "KAN-7", "fields": {"summary": "견적 비교", "duedate": "2026-11-10", "labels": ["remeet-todo-41", "기타"],
                                  "status": {"name": "진행 중", "statusCategory": {"name": "진행 중"}}}}


TRANSITIONS = [{"id": "2", "to": {"name": "해야 할 일", "statusCategory": {"key": "new"}}},
               {"id": "31", "to": {"name": "진행 중", "statusCategory": {"key": "indeterminate"}}},
               {"id": "51", "to": {"name": "완료", "statusCategory": {"key": "done"}}}]


class FakeJira:
    def __init__(self, accounts, transitions=TRANSITIONS, comment_error=None):
        self.accounts, self.jql = accounts, []
        self.ts, self.comment_error, self.moved, self.comments = transitions, comment_error, [], []

    async def transitions(self, cloud_id, key):
        return self.ts

    async def transition(self, cloud_id, key, transition_id):
        self.moved.append((key, transition_id))

    async def comment(self, cloud_id, key, text):
        if self.comment_error:
            raise self.comment_error
        self.comments.append(text)

    async def find_account(self, cloud_id, email):
        return self.accounts.get(email)

    async def site_url(self, cloud_id):
        return SITE

    async def search(self, cloud_id, jql, fields, max_results):
        self.jql.append(jql)
        if 'key = "' in jql:  # 완료 처리 전 「내 일인지」 확인 · 내 범위엔 KAN-7 만 있음
            return [ROW] if 'key = "KAN-7"' in jql else []
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
        dashboard_jira._changes.clear()
        dashboard_jira._refreshing.clear()
        self.addCleanup(dashboard_jira._cache.clear)

    def issues(self, fake, user="u1", todo_ids=(), **kw):
        return dashboard_jira.my_issues(user, connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com"},
                                        todo_ids=lambda uid: list(todo_ids), **kw)

    def test_co_owner_sees_issue_by_todo_label(self):
        """Jira 담당자는 한 명 → 공동 담당자 · Jira 계정을 못 찾은 사람은 RE:MEET 할 일 라벨로 찾음."""
        fake = FakeJira({})  # Jira 계정 없음
        res = self.issues(fake, todo_ids=[57, 41])
        self.assertFalse(res["linked"])
        self.assertEqual([i["key"] for i in res["issues"]], ["KAN-7"])  # 그래도 보임
        self.assertIn('labels in ("remeet-todo-57", "remeet-todo-41")', fake.jql[0])
        self.assertNotIn("assignee", fake.jql[0])

    def test_assignee_or_my_todo_labels(self):
        fake = FakeJira({"a@x.com": "712020:abc-1"})
        self.issues(fake, todo_ids=[41])
        self.assertTrue(fake.jql[0].startswith('(assignee = "712020:abc-1" OR labels in ("remeet-todo-41")) AND '))

    def test_only_my_issues(self):
        fake = FakeJira({"a@x.com": "712020:abc-1"})
        res = self.issues(fake)
        self.assertTrue(res["linked"])
        self.assertIn('assignee = "712020:abc-1"', fake.jql[0])  # 범위 = 로그인한 사람의 Jira 계정
        self.assertIn("statusCategory != Done", fake.jql[0])
        self.assertEqual(res["issues"], [{"key": "KAN-7", "title": "견적 비교", "status": "진행 중", "category": "진행 중",
                                          "due": "2026-11-10", "url": f"{SITE}/browse/KAN-7",
                                          "todo_id": 41, "category_key": "indeterminate"}])  # 라벨 → 상세 창 · 범주 → 색
        self.assertEqual(res["statuses"], [{"name": "해야 할 일", "category": "new", "id": "2"}, {"name": "진행 중", "category": "indeterminate", "id": "31"},
                                           {"name": "완료", "category": "done", "id": "51"}])  # 드롭다운 목록 (워크플로에서)
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
        self.assertEqual(len(fake.jql), 2)  # 60초 안에는 다시 부르지 않음 (미완료 · 완료 2번이 한 묶음)
        t[0] = 61
        self.issues(fake, now=lambda: t[0], spawn=lambda work: work())
        self.assertEqual(len(fake.jql), 4)

    def test_stale_list_returns_right_away_and_refreshes_behind(self):
        """캐시가 지나면 지난 목록을 바로 주고 Jira 는 뒤에서 (한 사람에 하나만) · 10분 넘게 지났으면 기다려서."""
        fake, t, jobs = FakeJira({"a@x.com": "acc"}), [0.0], []
        first = self.issues(fake, now=lambda: t[0])
        t[0] = 100
        self.assertIs(self.issues(fake, now=lambda: t[0], spawn=jobs.append), first)  # 기다리지 않고 지난 목록
        self.issues(fake, now=lambda: t[0], spawn=jobs.append)
        self.assertEqual((len(jobs), len(fake.jql)), (1, 2))  # 뒤에서 받기는 하나만 · 아직 Jira 안 부름
        jobs[0]()
        self.assertEqual(len(fake.jql), 4)
        self.assertEqual(dashboard_jira._cache["u1"][0], 100)  # 새 목록으로 바뀜
        self.assertNotIn("u1", dashboard_jira._refreshing)
        t[0] = 100 + 601
        self.issues(fake, now=lambda: t[0], spawn=jobs.append)
        self.assertEqual((len(jobs), len(fake.jql)), (1, 6))  # 너무 오래됨 → 그 자리에서 새로

    def test_refresh_started_before_status_change_does_not_overwrite(self):
        """뒤에서 받는 중에 상태를 바꾸면, 늦게 끝난 (바꾸기 전) 조회가 캐시를 덮지 않음 → 바꾼 상태가 되돌아가 보이지 않게."""
        fake, t, jobs = FakeJira({"a@x.com": "acc"}), [0.0], []
        self.issues(fake, now=lambda: t[0])
        t[0] = 100
        self.issues(fake, now=lambda: t[0], spawn=jobs.append)
        real_search = fake.search

        async def change_midway(cloud_id, jql, fields, max_results):
            if "statusCategory = Done" in jql:
                dashboard_jira._move_in_cache("u1", "KAN-7", True, "완료", "done")
            return await real_search(cloud_id, jql, fields, max_results)
        fake.search = change_midway
        jobs[0]()
        self.assertEqual(dashboard_jira._cache["u1"][0], 0.0)  # 덮지 않음
        self.assertIn("KAN-7", [i["key"] for i in dashboard_jira._cache["u1"][1]["done"]])

    def test_failed_refresh_behind_keeps_old_list(self):
        fake, t, jobs = FakeJira({"a@x.com": "acc"}), [0.0], []
        first = self.issues(fake, now=lambda: t[0])

        @asynccontextmanager
        async def broken(email, token):
            raise ConnectionError("Jira 다운")
            yield
        t[0] = 100
        dashboard_jira.my_issues("u1", connect=broken, emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [],
                                 now=lambda: t[0], spawn=jobs.append)
        with self.assertLogs(dashboard_jira.logger, level="WARNING"):
            jobs[0]()
        self.assertIs(dashboard_jira._cache["u1"][1], first)
        self.assertNotIn("u1", dashboard_jira._refreshing)

    def test_refresh_skips_cache_but_not_within_min_interval(self):
        fake, t = FakeJira({"a@x.com": "acc"}), [0.0]
        first = self.issues(fake, now=lambda: t[0])
        self.assertRegex(first["synced_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")  # 한국 시간
        t[0] = 5
        self.issues(fake, refresh=True, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 2)  # 직전 동기화 10초 안: 연타는 캐시
        t[0] = 15
        self.issues(fake, refresh=True, now=lambda: t[0])
        self.assertEqual(len(fake.jql), 4)  # 「동기화」는 1분 캐시를 건너뜀

    def test_connection_failure(self):
        @asynccontextmanager
        async def broken(email, token):
            raise ConnectionError("401 invalid token")
            yield
        with self.assertRaises(JiraUnavailable):
            dashboard_jira.my_issues("u1", connect=broken, emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [])


class DoneListAndSetStatusTests(unittest.TestCase):
    setUp = MyIssuesTests.setUp

    def done(self, fake, key="KAN-7", done=True):
        """드롭다운에서 「완료」 · 「진행 중」 고르기."""
        return dashboard_jira.set_status("u1", "리처드", key, "완료" if done else "진행 중", connect=connect_to(fake),
                                         emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [41])

    def test_list_has_recent_done(self):
        fake = FakeJira({"a@x.com": "acc"})
        res = dashboard_jira.my_issues("u1", connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com"},
                                       todo_ids=lambda uid: [])
        self.assertEqual([i["key"] for i in res["done"]], ["KAN-7"])
        self.assertIn("statusCategory = Done AND statusCategoryChangedDate >= -30d", fake.jql[1])

    def test_done_moves_to_done_category_and_comments_who(self):
        fake = FakeJira({"a@x.com": "acc"})
        dashboard_jira._cache["u1"] = (0.0, {"stale": True})
        self.assertEqual(self.done(fake), {"key": "KAN-7", "status": "완료", "category": "done"})
        self.assertEqual(fake.moved, [("KAN-7", "51")])
        self.assertEqual(fake.comments, [])  # 댓글은 응답 뒤에 따로 (comment_change)
        self.assertNotIn("u1", dashboard_jira._cache)  # 바로 다시 불러오게

    def test_changed_issue_moves_in_cache_right_away(self):
        """Jira 검색은 몇 초 늦게 반영 → 캐시 안에서 바로 남은 일 ↔ 완료로 옮겨 둠 (양쪽에 다 보이지 않게)."""
        item = {"key": "KAN-7", "title": "견적 비교", "status": "진행 중", "category": "", "due": None, "url": None}
        dashboard_jira._cache["u1"] = (0.0, {"issues": [item], "done": []})
        self.done(FakeJira({"a@x.com": "acc"}))
        data = dashboard_jira._cache["u1"][1]
        self.assertEqual((data["issues"], [(i["key"], i["status"]) for i in data["done"]]), ([], [("KAN-7", "완료")]))
        self.done(FakeJira({"a@x.com": "acc"}), done=False)
        self.assertEqual(([i["key"] for i in data["issues"]], data["done"]), (["KAN-7"], []))

    def test_missing_issue_is_not_mine(self):
        fake = FakeJira({"a@x.com": "acc"})

        async def refuse(cloud_id, jql, fields, max_results):
            raise RuntimeError("searchJiraIssuesUsingJql 실패: 이슈가 없어요")  # 지운 이슈 키 → Jira 가 검색을 거절
        fake.search = refuse
        with self.assertRaises(dashboard_jira.NotMine):
            self.done(fake, key="KAN-1")
        self.assertEqual(fake.moved, [])

    def test_not_mine_survives_real_session_wrapping(self):
        """실제 MCP 세션(anyio)은 안에서 난 예외를 ExceptionGroup 으로 감쌈 → 그래도 403(NotMine)이어야 (502 아님)."""
        fake = FakeJira({"a@x.com": "acc"})

        @asynccontextmanager
        async def grouping(email, token):
            try:
                yield fake
            except Exception as e:
                raise ExceptionGroup("unhandled errors in a TaskGroup", [e]) from None
        with self.assertRaises(dashboard_jira.NotMine):
            dashboard_jira.set_status("u1", "리처드", "KAN-99", "완료", connect=grouping,
                                      emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [41])

    def test_set_status_by_name(self):
        fake = FakeJira({"a@x.com": "acc"})
        out = dashboard_jira.set_status("u1", "리처드", "KAN-7", "해야 할 일", connect=connect_to(fake),
                                        emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [41])
        self.assertEqual((out, fake.moved), ({"key": "KAN-7", "status": "해야 할 일", "category": "new"}, [("KAN-7", "2")]))
        with self.assertRaises(dashboard_jira.NoTransition):  # 워크플로에 없는 상태
            dashboard_jira.set_status("u1", "리처드", "KAN-7", "보류", connect=connect_to(fake),
                                      emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [41])

    def test_not_mine_is_refused_before_changing(self):
        fake = FakeJira({"a@x.com": "acc"})
        with self.assertRaises(dashboard_jira.NotMine):
            self.done(fake, key="KAN-99")  # 남의 이슈 키를 보내도
        with self.assertRaises(dashboard_jira.NotMine):
            self.done(fake, key='KAN-7" OR key != "X')  # JQL 에 넣기 전에 형식으로 막음
        self.assertEqual(fake.moved, [])

    def test_no_transition(self):
        with self.assertRaises(dashboard_jira.NoTransition):
            self.done(FakeJira({"a@x.com": "acc"}, transitions=TRANSITIONS[:2]))  # 완료로 가는 길이 없음

    def test_fresh_list_skips_ownership_check_and_reuses_transition_ids(self):
        """1분 안에 불러온 내 목록에 있는 이슈 → 계정 찾기 · 검색 · 전환 목록 없이 바로 바꿈."""
        fake = FakeJira({"a@x.com": "acc"})
        dashboard_jira.my_issues("u1", connect=connect_to(fake), emails=lambda ids: {"u1": "a@x.com"},
                                 todo_ids=lambda uid: [], now=lambda: 100.0)
        self.assertIn({"name": "완료", "category": "done", "id": "51"}, dashboard_jira._cache["u1"][1]["statuses"])
        fake.jql.clear()

        def boom(*a):
            raise AssertionError("확인 단계를 건너뛰어야 함")
        fake.transitions = boom
        out = dashboard_jira.set_status("u1", "리처드", "KAN-7", "완료", connect=connect_to(fake), emails=boom,
                                        todo_ids=boom, now=lambda: 110.0)
        self.assertEqual((out["category"], fake.moved, fake.jql), ("done", [("KAN-7", "51")], []))
        with self.assertRaises(dashboard_jira.NotMine):  # 목록에 없는 남의 이슈는 지금처럼 확인 → 거절
            dashboard_jira.set_status("u1", "리처드", "KAN-99", "완료", connect=connect_to(fake),
                                      emails=lambda ids: {"u1": "a@x.com"}, todo_ids=lambda uid: [], now=lambda: 110.0)

    def test_stale_transition_id_falls_back_to_fresh_list(self):
        """받아 둔 전환 번호가 이 이슈 워크플로에 안 맞으면 → 전환 목록을 새로 받아 한 번 더."""
        fake = FakeJira({"a@x.com": "acc"})
        item = {"key": "KAN-7", "status": "진행 중"}
        dashboard_jira._cache["u1"] = (100.0, {"issues": [item], "done": [],
                                               "statuses": [{"name": "완료", "category": "done", "id": "999"}]})
        real = fake.transition

        async def picky(cloud_id, key, tid):
            if tid == "999":
                raise RuntimeError("transitionJiraIssue 실패: 없는 전환")
            await real(cloud_id, key, tid)
        fake.transition = picky
        out = dashboard_jira.set_status("u1", "리처드", "KAN-7", "완료", connect=connect_to(fake), now=lambda: 110.0)
        self.assertEqual((out["status"], fake.moved), ("완료", [("KAN-7", "51")]))

    def test_comment_change_records_who_and_never_raises(self):
        fake = FakeJira({})
        with patch.dict("os.environ", ENV):
            dashboard_jira.comment_change("리처드", "KAN-7", "완료", connect=connect_to(fake))
            self.assertEqual(fake.comments, ["RE:MEET 에서 리처드님이 상태를 「완료」(으)로 변경했어요."])
            with self.assertLogs(dashboard_jira.logger, level="WARNING"):  # 실패해도 로그만 (상태 변경은 이미 끝남)
                dashboard_jira.comment_change("리처드", "KAN-7", "완료",
                                              connect=connect_to(FakeJira({}, comment_error=RuntimeError("x"))))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/dashboard/jira").status_code, 401)
        self.assertEqual(self.client.post("/api/dashboard/jira/KAN-7/status", json={"status": "완료"}).status_code, 401)

    def test_set_status_uses_logged_in_user_and_maps_errors(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u2", "name": "리처드", "permission": "member"}
        with patch.object(dashboard_jira, "set_status", return_value={"key": "KAN-7", "status": "완료", "category": "done"}) as m,                 patch.object(dashboard_jira, "comment_change") as c:
            self.assertEqual(self.client.post("/api/dashboard/jira/KAN-7/status", json={"status": "완료"}).status_code, 200)
        m.assert_called_once_with("u2", "리처드", "KAN-7", "완료")
        c.assert_called_once_with("리처드", "KAN-7", "완료")  # 댓글은 응답 뒤 백그라운드로
        for exc, code in ((dashboard_jira.NotMine("x"), 403), (dashboard_jira.NoTransition("x"), 409),
                          (JiraUnavailable("token=secret"), 502)):
            with patch.object(dashboard_jira, "set_status", side_effect=exc), self.assertLogs(level="DEBUG"):
                dashboard_jira.logger.debug("x")  # 502 만 로그를 남기므로 assertLogs 가 비지 않게
                r = self.client.post("/api/dashboard/jira/KAN-7/status", json={"status": "완료"})
            self.assertEqual(r.status_code, code)
            self.assertNotIn("secret", r.text)

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
