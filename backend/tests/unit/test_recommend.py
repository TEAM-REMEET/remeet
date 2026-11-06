"""담당자 추천: 에이전트 그래프 · 적재 정규화 · API (가짜 DB · LLM, 실제 호출 없음)"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.adapters.neo4j.load import normalize_meeting, valid_follow_ups
from followup.analysis.recommend.recommend_agent import build_graph, most_similar, run_once
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user


class FakeLLM:
    """interpret 답은 action_item_id별 큐에서 꺼낸다. Exception이면 호출 실패로 취급."""

    def __init__(self, answers: dict):
        self.answers, self.payloads = answers, []

    def bind(self, **_):
        return self

    def invoke(self, messages):
        payload = json.loads(messages[1][1])
        self.payloads.append(payload)
        answer = self.answers[payload["action_item"]["action_item_id"]].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=json.dumps(answer))


def person(uid, position, level, active=True, team="t1"):
    return {"user_id": uid, "name": uid, "is_active": active, "position": position,
            "teams": [{"id": team, "name": team}], "roles": [], "open_tasks": 0,
            "skills": [{"id": "A", "name": "A", "level": level}]}


class AgentGraphTests(unittest.TestCase):
    """playground recommend_agent.py --self-check를 옮긴 것."""

    def test_graph(self):
        people = [person("s1", "senior", 4), person("s2", "senior", 2), person("s3", "senior", 3),
                  person("m1", "mid", 3), person("j1", "junior", 1), person("x1", None, 4), person("off", "mid", 4, False),
                  person("o1", "senior", 4, team="t2"), person("o2", "junior", 4, team="t2"),  # 참석자(s1) 팀 밖
                  person("z1", "mid", 0)]
        people[3]["open_tasks"] = 1
        good = {"required_skills": [{"skill_id": "A", "importance": "core", "reason": "핵심"}], "unmatched_skills": ["엑셀"]}
        bad = {"required_skills": [{"skill_id": "NOPE", "importance": "core"}], "unmatched_skills": ["3D 프린팅"]}
        llm = FakeLLM({"ok": [bad, good], "unclear": [bad, bad], "borrow": [bad, bad],
                       "llmfail": [RuntimeError(), RuntimeError()],
                       "nocand": [{"required_skills": [{"skill_id": "B", "importance": "core"}]}]})
        saved = {}
        tools = {
            "catalog": lambda: {"skills": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}], "roles": [], "teams": [],
                                "users": [{"id": p["user_id"]} for p in people]},
            "items": lambda force: ["ok", "unclear", "borrow", "llmfail", "nocand", "gone"],
            "context": lambda i: None if i == "gone" else {
                "action_item_id": i, "meeting_id": "m-1", "title": "할 일", "discussion_title": "소주제",
                "discussion": ["논의"], "agenda_title": "안건", "background": ["배경"], "decisions": ["결정"],
                "attendees": [{"user_id": "s1", "name": "s1"}]},
            "candidates": lambda: (people, True),
            "similar": lambda i: [] if i == "unclear" else [
                {"id": "far", "description": "예산 정리", "required_skills": [{"skill_id": "B", "importance": "core"}]},
                {"id": "near", "description": "할 일 정리",
                 "required_skills": [{"skill_id": "A", "importance": "core", "reason": "빌림"}]}],
            "save": lambda out: saved.update({out["action_item_id"]: out}),
        }
        status = {r["action_item_id"]: r["status"] for r in run_once(build_graph(tools, llm), False)}
        self.assertEqual(status, {"ok": "pending_manager_review", "unclear": "skill_unclear",
                                  "borrow": "pending_manager_review", "llmfail": "error",
                                  "nocand": "no_candidate", "gone": "error"})
        self.assertEqual(set(saved), {"ok", "unclear", "borrow", "nocand"})  # 실패 건은 저장 안 함 → 다음 요청에 다시

        # o1은 s3보다 점수가 높지만 팀 밖이라 senior 자리를 못 얻는다. o2는 모자란 junior 자리만 채운다
        rows = [(r["position"], r["user_id"], r["rank"]) for r in saved["ok"]["recommendations"]]
        self.assertEqual(rows, [("senior", "s1", 1), ("senior", "s3", 2), ("mid", "m1", 1),
                                ("junior", "j1", 1), ("junior", "o2", 2)])
        self.assertEqual(saved["ok"]["missing_skills"], ["엑셀"])
        self.assertEqual(saved["borrow"]["borrowed_from"], "near")
        self.assertEqual(saved["unclear"]["recommendations"], [])
        self.assertEqual(saved["nocand"]["recommendations"], [])
        self.assertIsNone(most_similar("x", []))
        self.assertNotIn("decisions", json.dumps(llm.payloads[0]))  # LLM에는 정해진 맥락만


def summary(**extra):
    action = {"item_id": "m1_a1_d2", "agenda_id": "m1_a1", "section": "major_discussions", "order": 2,
              "text": "시안을 보내기로 함.", "evidence_utterance_indices": [2], "status": "ACTIVE",
              "subtopic_id": "m1_a1_st1", "subtopic": "일정", "discussion_tag": "액션", "decision_tag": "결정",
              "assignees": ["바트"], "assignee_user_ids": ["u1"], "due": "2026-11-07"}
    unmatched = {**action, "item_id": "m1_a1_d3", "order": 3, "assignees": ["바트", "외부인"],
                 "assignee_user_ids": ["u1", None]}
    deleted = {**action, "item_id": "m1_a1_d4", "order": 4, "status": "DELETED"}
    point = {**action, "item_id": "m1_a1_d1", "order": 1, "discussion_tag": "제안", "text": "금요일 마감 제안.",
             "assignees": [], "assignee_user_ids": [], "due": None}
    return {
        "meeting_id": "m1", "created_at": "2026-11-05T10:00:00", "config": {},
        "source_document": {"input_path": "m1", "utterance_count": 2, "first_utterance_index": 1,
                            "last_utterance_index": 2},
        "meeting_info": {"title": "1차 회의", "date": "2026-11-05", "datetime_raw": None,
                         "participants": [{"user_id": "u1", "name": "바트", "job": "PM", "display_name": "바트"}],
                         "participant_count": 1, "participants_raw": None},
        "transcript": [], "agenda_index": [], "postprocess_log": [],
        "meeting_results": {"decision_item_ids": [], "action_item_ids": ["m1_a1_d2"], "unresolved_item_ids": [],
                            "next_topic_item_ids": []},
        "agendas": [{"agenda_id": "m1_a1", "agenda_number": 1, "agenda_title": "일정", "evidence_range": [1, 2],
                     "subtopics": [{"subtopic_id": "m1_a1_st1", "agenda_id": "m1_a1", "order": 1, "title": "일정",
                                    "item_ids": ["m1_a1_d1", "m1_a1_d2"]}],
                     "discussion_background": [], "major_discussions": [point, action, unmatched, deleted],
                     "unresolved_issues": [], "next_meeting_topics": []}],
        **extra,
    }


class NormalizeTests(unittest.TestCase):
    def test_meeting(self):
        m = normalize_meeting(summary(project_id="TS3003"))
        self.assertEqual((m["project_id"], m["attendees"], m["held_at"]), ("TS3003", ["u1"], "2026-11-05"))
        d = m["agendas"][0]["discussions"][0]
        self.assertEqual(d["discussions"], ["[제안][결정] 금요일 마감 제안."])
        actions = {a["id"]: a["assignee_ids"] for a in d["actions"]}
        # 담당자가 모두 대조돼야 바로 배정, 하나라도 못 맞추면 추천으로 / DELETED는 적재하지 않음
        self.assertEqual(actions, {"m1_a1_d2": ["u1"], "m1_a1_d3": []})
        self.assertEqual(m["agendas"][0]["decisions"], ["금요일 마감 제안."])  # 액션은 결정 목록에 넣지 않음

    def test_no_project(self):
        self.assertEqual(normalize_meeting(summary())["project_id"], "project-m1")

    def test_follow_ups_checked(self):
        raw = {"links": [{"action_item_id": "a", "follows_up_id": "p"}, {"action_item_id": "a", "follows_up_id": "q"},
                         {"action_item_id": "x", "follows_up_id": "p"}, {"action_item_id": "b", "follows_up_id": "nope"}]}
        self.assertEqual([(l["id"], l["prev"]) for l in valid_follow_ups(raw, {"a", "b"}, {"p", "q"})], [("a", "p")])
        self.assertEqual(valid_follow_ups("깨진 응답", {"a"}, {"p"}), [])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(app.dependency_overrides.clear)
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_recommend(self):
        item = {"item_id": "m1_a1_d3", "text": "시안", "due": None, "recommend_status": None, "missing_skills": None,
                "assignees": [], "predecessors": [], "candidates": [
                    {"user_id": "u2", "name": "리처드", "team": "UI", "role": "UI 디자이너", "score": 80.5,
                     "skill_score": 100.0, "role_team_score": None, "workload_score": 50.0, "attended": True,
                     "skills": ["UI 설계"], "open_tasks": 1}]}
        with patch("followup.entrypoints.api.recommend_routes.recommend_for_meeting", return_value=[item]) as run:
            res = self.client.post("/api/meetings/m1/recommendations", json=summary())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["items"][0]["candidates"][0]["user_id"], "u2")
        self.assertEqual(run.call_args.args[0]["meeting_id"], "m1")

    def test_recommend_errors(self):
        self.assertEqual(self.client.post("/api/meetings/other/recommendations", json=summary()).status_code, 422)
        with patch("followup.entrypoints.api.recommend_routes.recommend_for_meeting", side_effect=RuntimeError("비밀")):
            res = self.client.post("/api/meetings/m1/recommendations", json=summary())
        self.assertEqual(res.status_code, 502)
        self.assertNotIn("비밀", res.text)

    def test_assign(self):
        target = "followup.entrypoints.api.recommend_routes.action_items.set_assignees"
        with patch(target, return_value=[{"user_id": "u2", "name": "리처드", "source": "manual"}]) as setter:
            res = self.client.put("/api/action-items/m1_a1_d3/assignee", json={"user_ids": ["u2", "u2"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(setter.call_args.args, ("m1_a1_d3", ["u2"]))  # 중복 제거
        with patch(target, return_value=None):
            self.assertEqual(self.client.put("/api/action-items/x/assignee", json={"user_ids": []}).status_code, 404)
        with patch(target, side_effect=ValueError("없는 사람: u99")):
            self.assertEqual(self.client.put("/api/action-items/x/assignee", json={"user_ids": ["u99"]}).status_code, 400)

    def test_needs_login(self):
        app.dependency_overrides.clear()
        self.assertEqual(self.client.get("/api/meetings/m1/minutes").status_code, 401)


if __name__ == "__main__":
    unittest.main()
