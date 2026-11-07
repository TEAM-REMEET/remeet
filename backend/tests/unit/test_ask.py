"""POST /api/ask · 회의에 물어보기 (가짜 LLM · 가짜 Neo4j, 실제 API 호출 없음)"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.analysis.onboarding import onboarding_agent as agent
from followup.application import ask_meetings
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user


def fake_tools(agendas=lambda: []):
    topics = [{"topic_id": "T1", "title": "PG사 선정", "parent_id": None}]
    meetings = [{"meeting_id": "M1", "title": "1차 회의", "held_at": "2026-10-29"}]
    return {"topics": lambda: topics, "meetings": lambda: meetings, "agendas": agendas, "decisions": lambda: [],
            "write": lambda d, s: None, "decision_evidence": lambda ids: [], "contacts": lambda aids: [],
            "discussions": lambda aids: [], "minutes": lambda mid: {}}


class AgentTests(unittest.TestCase):
    def test_original_self_check(self):
        agent.self_check()  # 원본 에이전트의 자체 검사 (backfill · 근거 검사 · 재조회 · no_evidence …)

    def test_unknown_question_is_no_evidence(self):
        llm = agent.FakeLLM({"classify": lambda p: {"intent": "other", "topic_ids": []}})
        answer = ask_meetings.ask("회사 연차 규정 알려줘", llm=llm, tools=fake_tools())
        self.assertEqual(answer["answer_status"], "no_evidence")
        self.assertEqual(answer["summary"], agent.NO_EVIDENCE)

    def test_backfill_failure_still_answers(self):
        def broken():
            raise ConnectionError("neo4j down")
        llm = agent.FakeLLM({"classify": lambda p: {"intent": "other", "topic_ids": []}})
        answer = ask_meetings.ask("질문", llm=llm, tools=fake_tools(agendas=broken))
        self.assertEqual(answer["answer_status"], "no_evidence")

    def test_minutes_reads_backend_speaker_fields(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "M9"
            folder.mkdir()
            (folder / "M9_summary.json").write_text(json.dumps({
                "transcript": [{"utterance_index": 1, "speaker_display": "바트", "speaker_job": "PM", "text": "안녕"}],
                "agendas": []}), encoding="utf-8")
            agent.load_minutes.cache_clear()
            with patch.dict("os.environ", {"SUMMARY_OUTPUT_DIR": root}):
                utt = agent.load_minutes("M9")["utterances"][1]
            agent.load_minutes.cache_clear()
        self.assertEqual((utt["speaker"], utt["role"], utt["text"]), ("바트", "PM", "안녕"))


class AskRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def login(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1"}

    def test_requires_login(self):
        with patch.object(ask_meetings, "ask") as run:
            res = self.client.post("/api/ask", json={"question": "PG사는?"})
        self.assertEqual(res.status_code, 401)
        run.assert_not_called()

    def test_empty_question(self):
        self.login()
        with patch.object(ask_meetings, "ask") as run:
            self.assertEqual(self.client.post("/api/ask", json={"question": "   "}).status_code, 422)
            self.assertEqual(self.client.post("/api/ask", json={"question": ""}).status_code, 422)
        run.assert_not_called()

    def test_answer(self):
        self.login()
        answer = {"question": "PG사는?", "answer_status": "grounded", "summary": "A사·B사 후보 유지"}
        with patch.object(ask_meetings, "ask", return_value=answer) as run:
            res = self.client.post("/api/ask", json={"question": "  PG사는?  "})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), answer)
        run.assert_called_once_with("PG사는?")

    def test_failure_hides_internal_error(self):
        self.login()
        with patch.object(ask_meetings, "ask", side_effect=RuntimeError("비밀-내부-정보")):
            res = self.client.post("/api/ask", json={"question": "PG사는?"})
        self.assertEqual(res.status_code, 502)
        self.assertNotIn("비밀", res.text)


if __name__ == "__main__":
    unittest.main()
