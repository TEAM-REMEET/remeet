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
    "directory_id": "weekly",
    "title": "신제품 리모컨 1차 회의",
    "date": "2026-11-05",
    "host_user_id": "u1",
    "attendees": [{"user_id": "u1", "name": "바트", "job": "프로젝트 매니저", "team": "프로젝트 관리팀"},
                  {"user_id": "u10", "name": "조현우B", "job": "산업 디자이너", "team": "산업 디자인팀"}],
    "transcript_format": "colon",
    "transcript": "바트: 시안은 금요일까지 보낼게요.\n\n조현우B: 저도 같이 볼게요.",
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
        self.assertRegex(body["meeting_id"], r"^M_[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertEqual(body["project_id"], "TS3003")
        self.assertEqual(body["directory_id"], "weekly")
        self.assertEqual(body["host_user_id"], "u1")
        self.assertEqual([p["user_id"] for p in body["meeting_info"]["participants"]], ["u1", "u10"])
        action = body["agendas"][0]["major_discussions"][0]
        self.assertEqual(action["item_id"], f"{body['meeting_id']}_a1_d1")
        self.assertEqual(action["assignee_user_ids"], ["u1", "u10"])
        self.assertEqual(action["status"], "ACTIVE")
        self.assertIn("바트: 시안은 금요일까지", llm.users[0])
        self.assertNotIn("u10", llm.users[0])       # user_id는 LLM에 넘기지 않음
        self.assertNotIn("u1", llm.users[0])

    def test_saved_json_equals_response_and_old_json_still_loads(self):
        body = self.post(FakeLLM()).json()
        path = self.root / body["meeting_id"] / f"{body['meeting_id']}_summary.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), body)

    def test_format_error_is_422_without_llm_call(self):
        llm = FakeLLM()
        response = self.post(llm, {**REQUEST, "transcript": "바트: 가\n\n없는사람: 나"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("'없는사람'이(가) 참석자 목록에 없음", response.json()["detail"]["errors"][0])
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

    def test_same_name_with_different_jobs_full_flow(self):
        llm = FakeLLM()
        body = {**REQUEST, "host_user_id": "u2",
                "attendees": [{"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "마케터"}],
                "transcript": "홍길동(디자이너): 가\n\n홍길동(마케터): 나"}
        result = self.post(llm, body).json()
        self.assertIn("홍길동A: 가", llm.users[0])       # A · B는 LLM 입력에만
        self.assertIn("홍길동B: 나", llm.users[0])
        self.assertEqual([(u["speaker_user_id"], u["speaker_display"]) for u in result["transcript"]],
                         [("u2", "홍길동(디자이너)"), ("u3", "홍길동(마케터)")])
        self.assertNotIn("홍길동A", json.dumps(result["transcript"], ensure_ascii=False))

    def test_plain_format_full_flow(self):
        llm = FakeLLM()
        body = {**REQUEST, "transcript_format": "plain", "transcript": "바트가 시안을 보냅니다.\n\n참고: 시간은 10:30 입니다."}
        result = self.post(llm, body).json()
        self.assertEqual([u["text"] for u in result["transcript"]], ["바트가 시안을 보냅니다.", "참고: 시간은 10:30 입니다."])
        self.assertEqual({u["speaker_user_id"] for u in result["transcript"]}, {None})
        self.assertEqual([p["user_id"] for p in result["meeting_info"]["participants"]], ["u1", "u10"])
        self.assertEqual(result["meeting_info"]["speaker_user_ids"], [])
        self.assertEqual(result["agendas"][0]["major_discussions"][0]["assignee_user_ids"], ["u1", "u10"])

    def test_retry_keeps_meeting_id_and_leaves_only_summary_folder(self):
        self.post(FakeLLM(fail_at=2))
        body = self.post(FakeLLM()).json()
        self.assertEqual([p.name for p in self.root.iterdir()], [body["meeting_id"]])

    def test_resend_after_completion_is_new_meeting(self):
        first, second = self.post(FakeLLM()).json(), self.post(FakeLLM()).json()
        self.assertNotEqual(first["meeting_id"], second["meeting_id"])
        self.assertEqual(len(list(self.root.iterdir())), 2)

    def test_format_error_leaves_nothing_behind(self):
        self.post(FakeLLM(), {**REQUEST, "transcript": "없는사람: 나"})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_without_project_id_creates_new_project_id(self):
        body = {k: v for k, v in REQUEST.items() if k != "project_id"}
        self.assertRegex(self.post(FakeLLM(), body).json()["project_id"], r"^P_[0-9A-HJKMNP-TV-Z]{26}$")

    def test_retry_without_project_id_keeps_new_project_id(self):
        body = {k: v for k, v in REQUEST.items() if k != "project_id"}
        self.post(FakeLLM(fail_at=2), body)
        saved = {p.name: p.read_text(encoding="utf-8") for p in self.root.glob("*/steps/project_id.json")}
        self.assertEqual(len(saved), 1)
        retry = FakeLLM()
        response = self.post(retry, body).json()
        self.assertEqual(response["project_id"], next(iter(saved.values())))
        self.assertEqual(retry.calls, ["LLMVerification"])

    def test_blank_project_id_is_422_without_llm_call(self):
        llm = FakeLLM()
        self.assertEqual(self.post(llm, {**REQUEST, "project_id": "  "}).status_code, 422)
        self.assertEqual(llm.calls, [])

    def test_invalid_directory_id_is_422_without_llm_call(self):
        llm = FakeLLM()
        self.assertEqual(self.post(llm, {**REQUEST, "directory_id": "yearly"}).status_code, 422)
        self.assertEqual(llm.calls, [])


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


class HostUserIdTests(unittest.TestCase):
    def test_old_json_without_host_user_id_still_loads(self):
        from followup.analysis.summary.schemas import MeetingSummary
        self.assertIsNone(MeetingSummary.model_fields["host_user_id"].default)


class ProjectIdCompatibilityTests(unittest.TestCase):
    def test_json_without_project_id_still_loads(self):
        from followup.analysis.summary.schemas import MeetingSummary
        self.assertIsNone(MeetingSummary.model_fields["project_id"].default)


class HealthTests(unittest.TestCase):
    def test_health_still_works(self):
        self.assertEqual(TestClient(app).get("/api/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
