"""요약 백그라운드 실행 (가짜 LLM, 실제 API 호출 없음)"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup import settings as cfg
from followup.adapters.storage.local_files import LocalFileStore
from followup.application import meeting_jobs
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user
from followup.entrypoints.api.summary_routes import get_llm, get_store
from test_summary_routes import REQUEST, FakeLLM


class GatedLLM(FakeLLM):
    """열어 줄 때까지 응답하지 않음 (실행 중 상태 확인용)"""

    def __init__(self, fail_at=None):
        super().__init__(fail_at=fail_at)
        self.gate = threading.Event()

    def parse(self, system, user, schema, effort):
        self.gate.wait(10)
        return super().parse(system, user, schema, effort)


class JobBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"OPENAI_MODEL": "fake-model", "LANGSMITH_TRACING": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(app.dependency_overrides.clear)
        self.store = LocalFileStore(self.root)
        self.llm = GatedLLM()
        self.user = {"user_id": "u1", "permission": "member"}
        self.saved = []   # 분석이 끝나면 PostgreSQL 대신 여기에 (save_analysis)
        save = patch.object(meeting_jobs.save_analysis, "save_result",
                            lambda uid, sub, summary, draft: self.saved.append((uid, summary["meeting_id"])) or "draft")
        save.start()
        self.addCleanup(save.stop)
        self.addCleanup(self.finish_jobs)   # 패치보다 먼저 정리되도록 뒤에 등록 (LIFO)
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[get_llm] = lambda: self.llm
        app.dependency_overrides[current_user] = lambda: self.user
        self.client = TestClient(app, raise_server_exceptions=False)

    def finish_jobs(self):
        # 다음 시험에 영향이 없도록 실행 중인 작업이 끝날 때까지 기다림
        self.llm.gate.set()
        for _ in range(400):
            if not meeting_jobs._active:
                return
            time.sleep(0.01)
        self.fail("작업이 끝나지 않음")

    def start(self, body=None, draft=None):
        return self.client.post("/api/meeting-jobs", json={"request": body or REQUEST, "draft": draft or {}})

    def status(self):
        return self.client.get("/api/meeting-jobs/current").json()

    def wait_finished(self):
        for _ in range(200):
            job = self.status()
            if job["status"] != "running":
                return job
            time.sleep(0.05)
        self.fail("작업이 끝나지 않음")


class TimeLimitTests(JobBase):
    def test_summary_over_limit_fails_with_message_and_releases_user(self):
        with patch.object(cfg, "SUMMARY_TIMEOUT_SECONDS", 0.2):
            self.start()
            job = self.wait_finished()
        self.assertEqual(job["status"], "failed")
        self.assertIn("30분", job["message"])
        self.assertNotIn("user", meeting_jobs._active)
        self.llm.gate.set()
        calls = len(self.llm.calls)
        time.sleep(0.2)                                     # 멈춰 있던 호출 1건만 끝나고 새 LLM 호출은 없음
        self.assertLessEqual(len(self.llm.calls), calls + 1)

    def test_settings_default_to_30_and_10_minutes(self):
        self.assertEqual((cfg.SUMMARY_TIMEOUT_SECONDS, cfg.REVIEW_TIMEOUT_SECONDS), (1800, 600))


class JobTests(JobBase):
    def test_start_returns_immediately_while_running(self):
        started = self.start(draft={"nn_name": "새 프로젝트"})
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json()["status"], "running")
        job = self.status()
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["request"]["title"], REQUEST["title"])     # 입력값 유지
        self.assertEqual(job["request"]["transcript"], REQUEST["transcript"])
        self.assertEqual(job["draft"], {"nn_name": "새 프로젝트"})
        self.assertNotIn("result", job)

    def test_second_registration_is_blocked_while_running(self):
        self.start()
        blocked = self.start({**REQUEST, "title": "다른 회의"})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.status()["request"]["title"], REQUEST["title"])

    def test_other_user_can_register_at_the_same_time(self):
        self.start()
        self.user = {"user_id": "u2", "permission": "member"}
        self.assertEqual(self.status()["status"], "none")
        self.assertEqual(self.start({**REQUEST, "title": "다른 사람 회의"}).status_code, 202)
        self.assertEqual(self.status()["request"]["title"], "다른 사람 회의")

    def test_finished_job_has_result_and_summary_is_saved(self):
        self.start()
        self.llm.gate.set()
        job = self.wait_finished()
        self.assertEqual(job["status"], "done")
        self.assertRegex(job["meeting_id"], r"^M_")
        self.assertEqual(job["result"]["meeting_id"], job["meeting_id"])
        self.assertEqual(job["result"]["project_id"], REQUEST["project_id"])
        self.assertTrue((self.root / job["meeting_id"] / f'{job["meeting_id"]}_summary.json').is_file())

    def test_can_register_again_after_finish(self):
        self.start()
        self.llm.gate.set()
        self.wait_finished()
        self.assertEqual(self.start({**REQUEST, "title": "다음 회의"}).status_code, 202)

    def test_failure_keeps_inputs_and_hides_details_and_retry_resumes(self):
        self.llm = GatedLLM(fail_at=2)
        self.llm.gate.set()
        self.start()
        job = self.wait_finished()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["request"]["title"], REQUEST["title"])
        self.assertNotIn("비밀-내부-정보", json.dumps(job, ensure_ascii=False))
        retry = GatedLLM()
        retry.gate.set()
        self.llm = retry
        self.assertEqual(self.start().status_code, 202)
        self.assertEqual(self.wait_finished()["status"], "done")
        self.assertEqual(retry.calls, ["LLMVerification"])

    def test_bad_input_is_422_without_job_or_llm(self):
        self.assertEqual(self.start({**REQUEST, "transcript": "없는사람: 가"}).status_code, 422)
        self.assertEqual(self.start({**REQUEST, "attendees": []}).status_code, 422)
        self.assertEqual(self.status()["status"], "none")
        self.assertEqual(self.llm.calls, [])

    def test_restart_marks_running_job_interrupted(self):
        self.store.write_job("u1", json.dumps({"status": "running", "meeting_id": None, "message": None,
                                               "request": REQUEST, "draft": {}, "started_at": "t"}))
        job = self.status()
        self.assertEqual(job["status"], "failed")
        self.assertIn("다시 시작", job["message"])
        self.assertEqual(job["request"]["title"], REQUEST["title"])

    def test_clear_only_after_finish(self):
        self.start()
        self.assertEqual(self.client.delete("/api/meeting-jobs/current").status_code, 409)
        self.llm.gate.set()
        self.wait_finished()
        self.assertEqual(self.client.delete("/api/meeting-jobs/current").status_code, 204)
        self.assertEqual(self.status()["status"], "none")

    def test_requires_login(self):
        app.dependency_overrides.pop(current_user)
        self.assertEqual(self.client.get("/api/meeting-jobs/current").status_code, 401)
        self.assertEqual(self.start().status_code, 401)
        self.assertEqual(self.client.delete("/api/meeting-jobs/current").status_code, 401)

    def test_invalid_user_id_cannot_escape_storage(self):
        for bad in ("../밖", "a/b", ""):
            with self.assertRaises(ValueError):
                self.store.read_job(bad)


if __name__ == "__main__":
    unittest.main()


class SaveAfterAnalysisTests(JobBase):
    """분석이 끝나면 서버가 바로 저장 (브라우저와 상관없이) · 분석 중인 프로젝트는 폴더 삭제를 막음"""

    def test_done_job_is_saved_by_server(self):
        self.start({**REQUEST, "project_id": "P_A"})
        self.assertEqual(meeting_jobs.running_users("P_A"), ["u1"])   # 실행 중 → 이 프로젝트는 못 지움
        self.assertEqual(meeting_jobs.running_users("P_B"), [])
        self.llm.gate.set()
        job = self.wait_finished()
        self.assertEqual((job["status"], job["saved"]), ("done", "draft"))
        self.assertEqual(self.saved, [("u1", job["meeting_id"])])
        self.assertEqual(meeting_jobs.running_users("P_A"), [])

    def test_deleted_project_is_reported(self):
        def gone(*_):
            raise meeting_jobs.save_analysis.ProjectGone("P_A")
        with patch.object(meeting_jobs.save_analysis, "save_result", gone):
            self.start({**REQUEST, "project_id": "P_A"})
            self.llm.gate.set()
            job = self.wait_finished()
        self.assertEqual((job["status"], job["saved"]), ("done", None))
        self.assertEqual(job["save_error"], meeting_jobs.save_analysis.PROJECT_GONE)

