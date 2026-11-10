"""화면 확인용 시험 모드 — LLM이 호출되면 실패하는 가짜 LLM으로 확인"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup import settings as cfg
from followup.adapters.storage.local_files import LocalFileStore
from followup.analysis.summary import ui_test
from followup.application import meeting_jobs
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user
from followup.entrypoints.api.summary_routes import get_llm, get_store

REQUEST = {
    "project_id": "P_X", "directory_id": "monthly", "title": "화면 확인 회의", "date": "2026-11-05", "host_user_id": "u1",
    "attendees": [{"user_id": "u1", "name": "바트", "job": "프로젝트 매니저"}, {"user_id": "u20", "name": "김철수", "job": "개발자"}],
    "transcript_format": "plain", "transcript": " TEST\n",
}


class ForbiddenLLM:
    def __init__(self):
        self.calls = 0

    def parse(self, system, user, schema, effort):
        self.calls += 1
        raise AssertionError("시험 모드에서 LLM이 호출됨")


class Base(unittest.TestCase):
    mode = "true"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"UI_TEST_MODE": self.mode, "OPENAI_MODEL": "fake-model", "LANGSMITH_TRACING": "false"})
        env.start()
        self.addCleanup(env.stop)
        delay = patch.object(cfg, "UI_TEST_DELAY_SECONDS", 0)
        delay.start()
        self.addCleanup(delay.stop)
        self.addCleanup(app.dependency_overrides.clear)
        self.store, self.llm = LocalFileStore(self.root), ForbiddenLLM()
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[get_llm] = lambda: self.llm
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}
        notify = patch("followup.adapters.postgres.notifications.create_notification")  # DB 없이
        notify.start()
        self.addCleanup(notify.stop)
        self.client = TestClient(app, raise_server_exceptions=False)

    def start(self, body=None):
        return self.client.post("/api/meeting-jobs", json={"request": body or REQUEST, "draft": {}})

    def finished(self):
        for _ in range(200):
            job = self.client.get("/api/meeting-jobs/current").json()
            if job["status"] != "running":
                return job
            time.sleep(0.02)
        self.fail("작업이 끝나지 않음")


class EnabledTests(Base):
    def test_full_flow_without_llm(self):
        self.assertEqual(self.start().status_code, 202)
        job = self.finished()
        self.assertEqual(job["status"], "done")
        result = job["result"]
        self.assertTrue(result["config"]["ui_test"])
        self.assertRegex(result["meeting_id"], r"^M_")
        self.assertNotIn("UITEST", json.dumps(result["agendas"], ensure_ascii=False))
        self.assertEqual((result["project_id"], result["directory_id"], result["host_user_id"]), ("P_X", "monthly", "u1"))
        self.assertEqual((result["meeting_info"]["title"], result["meeting_info"]["date"]), ("화면 확인 회의", "2026-11-05"))
        self.assertIn("u20", [p["user_id"] for p in result["meeting_info"]["participants"]])

        action = next(d for a in result["agendas"] for d in a["major_discussions"] if d["discussion_tag"] == "액션" and d["status"] == "ACTIVE")
        edits = {"edits": [{"op": "update", "item_id": action["item_id"], "text": "리모컨 거치대 비용 조사", "assignee_user_ids": ["u20"]},
                           {"op": "create", "agenda_id": "기타", "text": "리모컨 배터리 충전 확인"}]}
        response = self.client.post(f'/api/meetings/{result["meeting_id"]}/review', json=edits)
        self.assertEqual(response.status_code, 200)
        final = response.json()
        self.assertEqual(final["hitl"]["status"], "COMPLETED")
        self.assertEqual(final["agendas"][-1]["agenda_title"], "기타" if final["agendas"][-1]["agenda_number"] else final["agendas"][-1]["agenda_title"])
        self.assertTrue(any(a["agenda_title"] == "기타" and a["agenda_number"] for a in final["agendas"]))
        self.assertEqual(self.llm.calls, 0)
        created = next(d for a in final["agendas"] for d in a["major_discussions"] if d["text"] == "리모컨 배터리 충전 확인")
        self.assertTrue(created["evidence_utterance_indices"])

    def test_new_project_gets_id_and_any_format_is_accepted(self):
        body = {k: v for k, v in REQUEST.items() if k != "project_id"}
        self.start({**body, "transcript_format": "colon"})
        self.assertRegex(self.finished()["result"]["project_id"], r"^P_")

    def test_other_text_does_not_use_fixture(self):
        self.start({**REQUEST, "transcript": "바트가 시안을 보냅니다."})
        self.assertEqual(self.finished()["status"], "failed")     # 일반 내용은 실제 LLM 경로 (여기서는 가짜 LLM이 막음)
        self.assertEqual(self.llm.calls, 1)


class RecommendTests(Base):
    def test_recommendations_use_neither_llm_nor_neo4j(self):
        self.start()
        final = self.client.post(f'/api/meetings/{self.finished()["result"]["meeting_id"]}/review', json={"edits": []}).json()
        with patch("followup.entrypoints.api.recommend_routes.recommend_for_meeting", side_effect=AssertionError("추천 호출됨")) as real, \
                patch("followup.adapters.neo4j.driver.get_driver", side_effect=AssertionError("Neo4j 접근")):
            response = self.client.post(f'/api/meetings/{final["meeting_id"]}/recommendations', json=final)
        self.assertEqual(response.status_code, 200)
        real.assert_not_called()
        items = response.json()["items"]
        self.assertTrue(items)
        active = {d["item_id"] for a in final["agendas"] for d in a["major_discussions"] if d["discussion_tag"] == "액션" and d["status"] == "ACTIVE"}
        self.assertEqual({i["item_id"] for i in items}, active)
        unassigned = [i for i in items if not i["assignees"]]
        self.assertTrue(all(i["candidates"] for i in unassigned))
        self.assertEqual(self.llm.calls, 0)

    def test_real_meeting_still_uses_real_recommendation(self):
        self.start()
        final = self.finished()["result"]
        final["config"].pop("ui_test")
        with patch("followup.entrypoints.api.recommend_routes.recommend_for_meeting", return_value=[]) as real:
            self.assertEqual(self.client.post(f'/api/meetings/{final["meeting_id"]}/recommendations', json=final).status_code, 200)
        real.assert_called_once()


class DisabledTests(Base):
    mode = "false"

    def test_test_transcript_is_rejected_without_llm_or_job(self):
        response = self.start()
        self.assertEqual(response.status_code, 422)
        self.assertIn("UI_TEST_MODE", response.json()["detail"]["errors"][0])
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(self.client.get("/api/meeting-jobs/current").json()["status"], "none")


class FixtureTests(unittest.TestCase):
    def test_fixture_is_clean(self):
        text = ui_test.FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("Users", text)
        self.assertNotIn("TS3003", text)
        self.assertIn("UITEST", text)

    def test_off_by_default(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(cfg, "load_env"):
            os.environ.pop("UI_TEST_MODE", None)
            self.assertFalse(cfg.ui_test_enabled())


if __name__ == "__main__":
    unittest.main()
