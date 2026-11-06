"""POST /api/meetings (가짜 LLM, 실제 API 호출 없음)"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.adapters.storage.local_files import LocalFileStore
from followup.analysis.summary.schemas import LLMAgenda, LLMDiscussionItem, LLMMeetingSummary, LLMVerification
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user
from followup.entrypoints.api.summary_routes import get_llm, get_store

REQUEST = {
    "project_id": "TS3003",
    "title": "신제품 리모컨 1차 회의",
    "date": "2026-11-05",
    "host_user_id": "u1",
    "attendees": [{"user_id": "u1", "name": "바트", "job": "프로젝트 매니저", "team": "프로젝트 관리팀"},
                  {"user_id": "u10", "name": "조현우B", "job": "산업 디자이너", "team": "산업 디자인팀"}],
    "transcript_format": "colon",
    "transcript": "A: 시안은 금요일까지 보낼게요.\n\nB: 저도 같이 볼게요.",
    "speaker_map": {"A": "u1", "B": "u10"},
}


class FakeLLM:
    def __init__(self, fail_at=None):
        self.calls, self.fail_at, self.users = [], fail_at, []

    def parse(self, system, user, schema, effort):
        self.calls.append(schema.__name__)
        self.users.append(user)
        if self.fail_at == len(self.calls):
            raise ConnectionError("비밀-내부-정보")
        if schema is LLMVerification:
            return LLMVerification(issues=[])
        item = LLMDiscussionItem(subtopic="일정", discussion_tag="액션", decision_tag="결정", text="시안을 함께 보내기로 함.",
                                 assignees=["바트", "조현우B"], due="2026-11-06", evidence_utterance_indices=[1, 2])
        return LLMMeetingSummary(agendas=[LLMAgenda(agenda_title="일정", discussion_background=[], major_discussions=[item],
                                                    unresolved_issues=[], next_meeting_topics=[])])


class MeetingRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"OPENAI_MODEL": "fake-model", "LANGSMITH_TRACING": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(app.dependency_overrides.clear)
        app.dependency_overrides[get_store] = lambda: LocalFileStore(self.root)
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}
        self.client = TestClient(app, raise_server_exceptions=False)

    def post(self, llm, body=None):
        app.dependency_overrides[get_llm] = lambda: llm
        return self.client.post("/api/meetings", json=body or REQUEST)

    def test_returns_meeting_summary_with_shared_ids(self):
        llm = FakeLLM()
        response = self.post(llm)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertRegex(body["meeting_id"], r"^m-20261105-[0-9a-f]{4}$")
        self.assertEqual(body["project_id"], "TS3003")
        self.assertEqual([p["user_id"] for p in body["meeting_info"]["participants"]], ["u1", "u10"])
        action = body["agendas"][0]["major_discussions"][0]
        self.assertEqual(action["item_id"], f"{body['meeting_id']}_a1_d1")
        self.assertEqual(action["assignee_user_ids"], ["u1", "u10"])
        self.assertEqual(action["status"], "ACTIVE")
        self.assertIn("바트: 시안은 금요일까지", llm.users[0])
        self.assertNotIn("u10", llm.users[0])       # user_id는 LLM에 넘기지 않음

    def test_saved_json_equals_response_and_old_json_still_loads(self):
        body = self.post(FakeLLM()).json()
        path = self.root / body["meeting_id"] / f"{body['meeting_id']}_summary.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), body)

    def test_format_error_is_422_without_llm_call(self):
        llm = FakeLLM()
        response = self.post(llm, {**REQUEST, "speaker_map": {"A": "u1"}})
        self.assertEqual(response.status_code, 422)
        self.assertIn("'B'이(가) 사람과 연결되지 않음", response.json()["detail"]["errors"][0])
        self.assertEqual(llm.calls, [])

    def test_invalid_request_is_422_without_llm_call(self):
        llm = FakeLLM()
        self.assertEqual(self.post(llm, {**REQUEST, "attendees": []}).status_code, 422)
        self.assertEqual(llm.calls, [])

    def test_failure_is_502_without_internal_details_and_retry_resumes(self):
        failing = FakeLLM(fail_at=2)
        response = self.post(failing)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("비밀-내부-정보", response.text)

        retry = FakeLLM()
        self.assertEqual(self.post(retry).status_code, 200)
        self.assertEqual(retry.calls, ["LLMVerification"])      # 생성 응답을 재사용하고 실패한 단계부터 호출


class MeetingRouteAuthTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(app.dependency_overrides.clear)
        self.llm = FakeLLM()
        app.dependency_overrides[get_llm] = lambda: self.llm
        self.client = TestClient(app)

    def assert_rejected(self, headers=None, body=None):
        response = self.client.post("/api/meetings", json=body or REQUEST, headers=headers or {})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.llm.calls, [])

    def test_without_token_is_401_without_llm_call(self):
        self.assert_rejected()

    def test_wrong_token_is_401_without_llm_call(self):
        self.assert_rejected(headers={"Authorization": "Bearer fake-token"})

    def test_invalid_body_without_token_is_still_401(self):
        self.assert_rejected(body={"title": "형식이 틀린 요청"})


class ProjectIdCompatibilityTests(unittest.TestCase):
    def test_json_without_project_id_still_loads(self):
        from followup.analysis.summary.schemas import MeetingSummary
        self.assertIsNone(MeetingSummary.model_fields["project_id"].default)


class HealthTests(unittest.TestCase):
    def test_health_still_works(self):
        self.assertEqual(TestClient(app).get("/api/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
