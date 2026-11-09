"""회의 캐시 (data/repository): 회의 하나를 고치면 그 회의 상세만 다시 받고, 모를 때는 전부 다시 받음.
가짜 백엔드(api.call)로 요청 수를 세요 (서버 없이).

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402

IDS = [f"M_{i}" for i in range(1, 11)]  # 회의 10개


class MeetingCacheTests(unittest.TestCase):
    def setUp(self):
        self.calls = []        # 백엔드로 간 요청 (method, path)
        self.gone = set()      # 지워진 회의 (상세가 404)
        self.on_detail = None  # 상세를 받는 도중에 끼워 넣을 일 (동시에 고친 경우 흉내)
        self.projects_cleared = 0
        clear_projects = repo._projects_changed

        def projects_changed():
            self.projects_cleared += 1
            clear_projects()

        for target, name, fn in [
            (repo.api, "call", self.fake_call),
            (repo, "_token", lambda: "tok"),
            (repo, "_is_admin", lambda: False),
            (repo, "_meeting", lambda d: SimpleNamespace(id=d["id"], status=d.get("status"))),
            (repo, "_projects_changed", projects_changed),
        ]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)
        repo.meetings_changed()  # 다른 시험이 남긴 캐시 비우기
        self.calls.clear()
        self.projects_cleared = 0

    def fake_call(self, method, path, body=None, token="", timeout=15):
        self.calls.append((method, path.split("?")[0]))
        if method == "GET" and path.startswith("/api/meetings?"):
            return [{"id": i, "date_label": "2026.11.10"} for i in IDS if i not in self.gone]
        if method == "GET" and path.startswith("/api/meetings/"):
            mid = path.rsplit("/", 1)[1]
            if self.on_detail:
                self.on_detail(mid)
            if mid in self.gone:
                raise repo.api.ApiError("없음", status=404)
            return {"id": mid, "date": "11/10", "status": "review"}
        return {"status": "done", "meeting_id": "M_3"}

    def gets(self):
        return [p for m, p in self.calls if m == "GET"]

    def load(self):
        self.calls.clear()
        return repo._base_meetings()

    def test_first_load_gets_list_and_every_detail(self):
        self.assertEqual(list(self.load()), IDS)
        self.assertEqual(self.gets().count("/api/meetings"), 1)
        self.assertEqual(len(self.gets()), 1 + len(IDS))

    def test_second_load_uses_cache(self):
        self.load()
        self.load()
        self.assertEqual(self.calls, [])

    def test_confirm_refetches_only_that_meeting(self):
        self.load()
        self.calls.clear()
        repo.confirm_record("M_3")
        self.load()
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_3"])
        self.assertEqual(self.projects_cleared, 0)  # 회의 안 내용만 바뀜 → 프로젝트는 그대로

    def test_todo_with_mid_refetches_only_that_meeting(self):
        self.load()
        repo.update_record_todo(7, {"text": "x"}, mid="M_5")
        repo.delete_record_todo(8, mid="M_5")
        self.load()
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_5"])

    def test_todo_without_mid_refetches_everything(self):
        self.load()
        repo.update_record_todo(7, {"text": "x"})  # 어느 회의인지 모름 → 전부 (예전과 같음)
        self.load()
        self.assertEqual(len(self.gets()), 1 + len(IDS))
        self.assertEqual(self.projects_cleared, 1)

    def test_submit_and_delete_also_refresh_projects(self):
        self.load()
        repo.submit_record("M_2")
        self.assertEqual(self.projects_cleared, 1)
        self.gone.add("M_4")
        repo.delete_record("M_4")
        self.assertEqual(self.projects_cleared, 2)
        meetings = self.load()
        self.assertNotIn("M_4", meetings)
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_2"])  # M_4 는 목록에서 빠져 상세도 안 받음

    def test_create_project_keeps_meeting_cache(self):
        self.load()
        repo.create_project("새 프로젝트", ["u1"])
        self.assertEqual(self.projects_cleared, 1)
        self.load()
        self.assertEqual(self.calls, [])

    def test_branch_request_refetches_everything(self):
        self.load()
        repo.request_record_branch("M_1")  # 새 회의가 생길 수 있음
        self.load()
        self.assertEqual(len(self.gets()), 1 + len(IDS))

    def test_details_expire_after_ttl(self):
        self.load()
        later = repo.time.monotonic() + repo._MEETINGS_TTL + 1
        with patch.object(repo.time, "monotonic", lambda: later):
            self.load()
        self.assertEqual(len(self.gets()), 1 + len(IDS))  # 다른 사람이 고친 내용을 받으려고 전부 다시

    def test_change_during_fetch_is_not_cached(self):
        """M_6 을 받는 도중에 누가 M_6 을 고치면, 받은(고치기 전) 내용을 기억하지 않음."""
        def change_once(mid):
            if mid == "M_6":
                self.on_detail = None
                repo.meetings_changed("M_6", projects=False)
        self.on_detail = change_once
        self.load()
        self.load()  # 첫 묶음은 기억하지 않았으니 다시 만듦 · 상세는 M_6 만 다시
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_6"])
        self.load()
        self.assertEqual(self.calls, [])  # 이번엔 아무도 안 고쳐서 기억함

    def test_other_users_cache_drops_the_same_meeting(self):
        """내가 고친 회의는 같은 회의를 본 다른 사람의 캐시에서도 버림."""
        self.load()
        with patch.object(repo, "_token", lambda: "other"):
            self.load()
        repo.confirm_record("M_3")
        with patch.object(repo, "_token", lambda: "other"):
            self.load()
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_3"])

    def test_resolve_failure_refetches_its_meeting(self):
        self.load()
        with patch.object(repo, "_remember", lambda rows: rows):
            repo.resolve_failure("f-1")
        self.load()
        self.assertEqual(self.gets(), ["/api/meetings", "/api/meetings/M_3"])


if __name__ == "__main__":
    unittest.main()
