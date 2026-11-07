"""사용자 수정(HITL) → 근거 연결 · 검증 → 최종 JSON (가짜 LLM, 실제 API 호출 없음)"""
import json
import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup import settings as cfg
from followup.adapters.storage.local_files import LocalFileStore
from followup.analysis.summary.hitl import HitlEditError
from followup.analysis.summary.schemas import (HitlEdits, LLMAgenda, LLMDiscussionItem, LLMEvidenceIssue, LLMEvidenceLink,
                                               LLMEvidenceLinks, LLMEvidenceVerification, LLMMeetingSummary, LLMVerification,
                                               MeetingSummary)
from followup.application.review_meeting import MeetingNotFoundError, review_meeting
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user
from followup.entrypoints.api.summary_routes import get_llm, get_store

REQUEST = {
    "project_id": "P_X", "directory_id": "weekly", "title": "신제품 회의", "date": "2026-11-05", "host_user_id": "u1",
    "attendees": [{"user_id": "u1", "name": "바트", "job": "프로젝트 매니저"}, {"user_id": "u2", "name": "리처드", "job": "산업 디자이너"}],
    "transcript_format": "colon",
    "transcript": "바트: 시안은 금요일까지 보낼게요.\n\n리처드: 저도 같이 볼게요.\n\n바트: 비용도 조사해 주세요.",
}


class FakeLLM:
    """요약 응답 + 근거 연결 · 검증 응답을 순서대로 돌려줌"""

    def __init__(self, links=None, verdicts=None, fail_at=None):
        self.calls, self.users, self.links, self.verdicts, self.fail_at = [], [], links, verdicts or [], fail_at

    def parse(self, system, user, schema, effort):
        self.calls.append(schema.__name__)
        self.users.append(user)
        if self.fail_at == len(self.calls):
            raise ConnectionError("비밀-내부-정보")
        if schema is LLMVerification:
            return LLMVerification(issues=[])
        if schema is LLMMeetingSummary:
            action = LLMDiscussionItem(subtopic="일정", discussion_tag="액션", decision_tag="결정", text="시안을 보내기로 함.",
                                       assignees=["바트"], due="2026-11-06", evidence_utterance_indices=[1])
            decision = LLMDiscussionItem(subtopic="일정", discussion_tag="제안", decision_tag="결정", text="금요일에 확정함.",
                                         assignees=[], due=None, evidence_utterance_indices=[2])
            return LLMMeetingSummary(agendas=[LLMAgenda(agenda_title="일정", discussion_background=[], major_discussions=[decision, action],
                                                        unresolved_issues=[], next_meeting_topics=[])])
        if schema is LLMEvidenceLinks:
            links = self.links if self.links is not None else [
                LLMEvidenceLink(item_id=i, evidence_utterance_indices=[3], reason="근거") for i in _targets(user)]
            return LLMEvidenceLinks(links=links)
        if schema is LLMEvidenceVerification:
            return self.verdicts.pop(0) if self.verdicts else LLMEvidenceVerification(issues=[])
        raise AssertionError(schema)


def _targets(user: str) -> list[str]:
    return [line[2:].split(" | ")[0] for line in user.splitlines() if line.startswith("- M_")]


def edits(*items) -> HitlEdits:
    return HitlEdits.model_validate({"edits": list(items)})


class ReviewBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"OPENAI_MODEL": "fake-model", "LANGSMITH_TRACING": "false"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(app.dependency_overrides.clear)
        self.store = LocalFileStore(self.root)
        app.dependency_overrides[get_store] = lambda: self.store
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}
        self.client = TestClient(app, raise_server_exceptions=False)
        app.dependency_overrides[get_llm] = lambda: FakeLLM()
        self.original = self.client.post("/api/meetings", json=REQUEST).json()
        self.mid = self.original["meeting_id"]
        self.action = next(d for d in self.original["agendas"][0]["major_discussions"] if d["discussion_tag"] == "액션")
        self.agenda = self.original["agendas"][0]["agenda_id"]

    def run_review(self, *items, llm=None):
        return review_meeting(self.mid, edits(*items), llm=llm or FakeLLM(), store=self.store)

    def final_path(self):
        return self.root / self.mid / f"{self.mid}_summary_final.json"


class ApplyTests(ReviewBase):
    def test_update_changes_only_written_values_and_links_evidence(self):
        llm = FakeLLM()
        result = self.run_review({"op": "update", "item_id": self.action["item_id"], "text": "새 액션", "due": None}, llm=llm)
        item = result.find_item(self.action["item_id"])
        self.assertEqual((item.text, item.due, item.assignee_user_ids), ("새 액션", None, ["u1"]))
        self.assertEqual(item.evidence_utterance_indices, [3])
        self.assertEqual(llm.calls, ["LLMEvidenceLinks", "LLMEvidenceVerification"])
        record = result.hitl.edits[0]
        self.assertEqual((record.evidence_status, record.evidence_before, record.evidence_after), ("LINKED", [1], [3]))
        self.assertEqual(sorted(record.changed_fields), ["due", "text"])

    def test_update_assignees_fills_all_person_fields(self):
        item = self.run_review({"op": "update", "item_id": self.action["item_id"], "assignee_user_ids": ["u2"]}).find_item(self.action["item_id"])
        self.assertEqual((item.assignees, item.assignee_user_ids, item.assignee_names, item.assignee_jobs),
                         (["리처드"], ["u2"], ["리처드"], ["산업 디자이너"]))

    def test_confirming_unmatched_assignee_clears_review_even_with_same_value(self):
        summary = MeetingSummary.model_validate_json(self.store.read_summary(self.mid))
        item = summary.find_item(self.action["item_id"])
        item.status, item.review_reasons = "REVIEW_REQUIRED", ["담당자 확인 필요: 참여자에 없음 (홍길동)"]
        self.store.save_summary(summary)
        llm = FakeLLM()
        result = self.run_review({"op": "update", "item_id": item.item_id, "assignee_user_ids": item.assignee_user_ids}, llm=llm)
        after = result.find_item(item.item_id)
        self.assertEqual((after.status, after.review_reasons), ("ACTIVE", []))
        self.assertEqual(llm.calls, [])                     # 값이 같으면 근거를 다시 연결하지 않음

    def test_delete_needs_no_llm_and_updates_results(self):
        llm = FakeLLM()
        result = self.run_review({"op": "delete", "item_id": self.action["item_id"]}, llm=llm)
        self.assertEqual(llm.calls, [])
        self.assertEqual(result.find_item(self.action["item_id"]).status, "DELETED")
        self.assertNotIn(self.action["item_id"], result.meeting_results.action_item_ids)

    def test_no_edits_saves_final_without_llm(self):
        llm = FakeLLM()
        result = self.run_review(llm=llm)
        self.assertEqual((llm.calls, result.hitl.status), ([], "NO_CHANGES"))
        self.assertTrue(self.final_path().is_file())

    def test_create_in_existing_agenda(self):
        result = self.run_review({"op": "create", "agenda_id": self.agenda, "text": "추가 액션", "assignee_user_ids": ["u2"], "due": "2026-12-01"})
        record = result.hitl.edits[0]
        self.assertEqual(record.item_id, f"{self.agenda}_d3")
        item = result.find_item(record.item_id)
        self.assertEqual((item.text, item.discussion_tag, item.decision_tag, item.due, item.assignee_user_ids),
                         ("추가 액션", "액션", "결정", "2026-12-01", ["u2"]))
        self.assertIn(record.item_id, result.meeting_results.action_item_ids)

    def test_unparsable_due_is_saved_empty_with_note(self):
        result = self.run_review({"op": "create", "agenda_id": self.agenda, "text": "추가 액션", "due": "다음 주"})
        self.assertIsNone(result.find_item(result.hitl.edits[0].item_id).due)
        self.assertIn("빈 일정", result.hitl.edits[0].note)


class MiscAgendaTests(ReviewBase):
    def test_creates_misc_agenda_with_max_plus_one_at_the_end(self):
        result = self.run_review({"op": "create", "agenda_id": "기타", "text": "기타 액션"},
                                 {"op": "create", "agenda_id": "기타", "text": "기타 액션 2"})
        misc = result.agendas[-1]
        self.assertEqual(misc.agenda_id, f"{self.mid}_a2")
        self.assertEqual((misc.agenda_title, misc.agenda_number), ("기타", 2))
        self.assertEqual([it.text for it in misc.major_discussions], ["기타 액션", "기타 액션 2"])
        self.assertEqual([a.agenda_title for a in result.agendas].count("기타"), 1)
        self.assertEqual(result.agenda_index[-1].agenda_title, "기타")

    def test_existing_misc_agenda_keeps_its_id(self):
        created = self.run_review({"op": "create", "agenda_id": "기타", "text": "첫 액션"})
        saved = json.loads(self.store.read_summary(self.mid))
        saved["agendas"] = json.loads(created.model_dump_json())["agendas"]
        (self.root / self.mid / f"{self.mid}_summary.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        result = self.run_review({"op": "create", "agenda_id": "기타", "text": "둘째 액션"})
        misc = [a for a in result.agendas if a.agenda_title == "기타"]
        self.assertEqual([a.agenda_id for a in misc], [f"{self.mid}_a2"])
        self.assertEqual(len(misc[0].major_discussions), 2)

    def test_emptied_misc_agenda_is_reused(self):
        created = self.run_review({"op": "create", "agenda_id": "기타", "text": "지울 액션"})
        item_id = created.hitl.edits[0].item_id
        saved = json.loads(created.model_dump_json())
        saved["hitl"] = None
        (self.root / self.mid / f"{self.mid}_summary.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        emptied = self.run_review({"op": "delete", "item_id": item_id})
        (self.root / self.mid / f"{self.mid}_summary.json").write_text(emptied.model_dump_json(), encoding="utf-8")
        result = self.run_review({"op": "create", "agenda_id": "기타", "text": "새 액션"})
        self.assertEqual([a.agenda_id for a in result.agendas if a.agenda_title == "기타"], [f"{self.mid}_a2"])
        self.assertEqual(result.hitl.edits[0].item_id, f"{self.mid}_a2_d2")


class EvidenceTests(ReviewBase):
    def target(self):
        return {"op": "update", "item_id": self.action["item_id"], "text": "새 액션"}

    def issue(self):
        return LLMEvidenceVerification(issues=[LLMEvidenceIssue(item_id=self.action["item_id"], issue_type="관련 없는 근거",
                                                                 description="다른 업무", utterance_indices=[3])])

    def test_llm_cannot_change_user_values(self):
        link = LLMEvidenceLink(item_id=self.action["item_id"], evidence_utterance_indices=[2, 99, 2], reason="근거")
        ignored = LLMEvidenceLink(item_id="M_없는_항목", evidence_utterance_indices=[1], reason="무시")
        result = self.run_review(self.target(), llm=FakeLLM(links=[link, ignored]))
        item = result.find_item(self.action["item_id"])
        self.assertEqual((item.text, item.evidence_utterance_indices), ("새 액션", [2]))

    def test_failed_item_is_relinked_then_passes(self):
        llm = FakeLLM(verdicts=[self.issue(), LLMEvidenceVerification(issues=[])])
        result = self.run_review(self.target(), llm=llm)
        self.assertEqual(llm.calls, ["LLMEvidenceLinks", "LLMEvidenceVerification"] * 2)
        self.assertEqual([r.passed for r in result.hitl.rounds], [False, True])
        self.assertIn("관련 없는 근거", llm.users[2])     # 다시 연결할 때 검증 결과를 넘김
        self.assertEqual(result.hitl.edits[0].evidence_status, "LINKED")

    def test_last_round_failure_clears_evidence_keeps_action(self):
        result = self.run_review(self.target(), llm=FakeLLM(verdicts=[self.issue(), self.issue()]))
        item = result.find_item(self.action["item_id"])
        self.assertEqual((item.text, item.status, item.evidence_utterance_indices), ("새 액션", "ACTIVE", []))
        self.assertEqual(result.hitl.edits[0].evidence_status, "UNVERIFIED")
        self.assertIn("근거 검증 미통과", item.history[-1].reason)

    def test_calls_never_exceed_limit(self):
        llm = FakeLLM(verdicts=[self.issue() for _ in range(5)])
        self.run_review(self.target(), llm=llm)
        self.assertEqual(len(llm.calls), cfg.hitl_max_llm_calls())

    def test_user_id_is_not_sent_to_llm(self):
        llm = FakeLLM()
        self.run_review(self.target(), llm=llm)
        self.assertNotIn("u2", llm.users[0])

    def test_failure_retry_resumes_and_final_saved_only_on_success(self):
        failing = FakeLLM(fail_at=2)
        with self.assertRaises(ConnectionError):
            self.run_review(self.target(), llm=failing)
        self.assertFalse(self.final_path().exists())
        retry = FakeLLM()
        self.run_review(self.target(), llm=retry)
        self.assertEqual(retry.calls, ["LLMEvidenceVerification"])
        self.assertTrue(self.final_path().is_file())


class StorageTests(ReviewBase):
    def test_edits_file_keeps_only_written_values(self):
        self.run_review({"op": "create", "agenda_id": self.agenda, "text": "새 액션", "assignee_user_ids": ["u2"]},
                        {"op": "update", "item_id": self.action["item_id"], "due": "2026-12-01"})
        saved = json.loads((self.root / self.mid / f"{self.mid}_hitl_edits.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, {"edits": [{"op": "create", "agenda_id": self.agenda, "text": "새 액션", "assignee_user_ids": ["u2"]},
                                           {"op": "update", "item_id": self.action["item_id"], "due": "2026-12-01"}]})

    def test_rejected_edits_are_not_saved(self):
        with self.assertRaises(HitlEditError):
            self.run_review({"op": "delete", "item_id": "없음"})
        self.assertFalse((self.root / self.mid / f"{self.mid}_hitl_edits.json").exists())

    def test_original_summary_is_not_changed(self):
        before = self.store.read_summary(self.mid)
        result = self.run_review({"op": "delete", "item_id": self.action["item_id"]})
        self.assertEqual(self.store.read_summary(self.mid), before)
        self.assertEqual(json.loads(self.final_path().read_text(encoding="utf-8")), json.loads(result.model_dump_json()))

    def test_second_review_restarts_from_original(self):
        self.run_review({"op": "delete", "item_id": self.action["item_id"]})
        result = self.run_review({"op": "update", "item_id": self.action["item_id"], "text": "다시 수정"})
        self.assertEqual(result.find_item(self.action["item_id"]).status, "ACTIVE")


class TimeLimitTests(ReviewBase):
    def test_over_limit_is_504_and_nothing_final_is_saved(self):
        import threading

        gate = threading.Event()
        self.addCleanup(gate.set)

        class Slow(FakeLLM):
            def parse(self, system, user, schema, effort):
                gate.wait(5)
                return super().parse(system, user, schema, effort)

        slow = Slow()
        app.dependency_overrides[get_llm] = lambda: slow
        with patch.object(cfg, "REVIEW_TIMEOUT_SECONDS", 0.2):
            response = self.client.post(f"/api/meetings/{self.mid}/review",
                                        json={"edits": [{"op": "update", "item_id": self.action["item_id"], "text": "새"}]})
        self.assertEqual(response.status_code, 504)
        self.assertIn("10분", response.json()["detail"])
        self.assertFalse(self.final_path().exists())
        gate.set()
        time.sleep(0.2)
        self.assertEqual(slow.calls, ["LLMEvidenceLinks"])      # 기한이 지나 검증은 호출하지 않음


class FinalAssigneeTests(ReviewBase):
    def put(self, *changes, mid=None):
        return self.client.put(f"/api/meetings/{mid or self.mid}/final/assignees", json={"changes": list(changes)})

    def test_saves_assignees_and_due_into_final_json_without_llm(self):
        self.run_review()
        app.dependency_overrides[get_llm] = lambda: (_ for _ in ()).throw(AssertionError("LLM 호출"))
        response = self.put({"item_id": self.action["item_id"], "due": "2026-12-24",
                             "assignees": [{"user_id": "u2", "name": "아무개", "job": "x"},
                                           {"user_id": "u99", "name": "홍길동", "job": "개발자"},
                                           {"user_id": "u98", "name": "리처드", "job": "기획자"}]})
        self.assertEqual(response.status_code, 200)
        saved = json.loads(self.final_path().read_text(encoding="utf-8"))
        item = next(d for a in saved["agendas"] for d in a["major_discussions"] if d["item_id"] == self.action["item_id"])
        self.assertEqual(item["assignees"], ["리처드", "홍길동", "리처드(기획자)"])       # 참석자는 회의 정보 값 · 이름이 겹치면 직무
        self.assertEqual(item["assignee_user_ids"], ["u2", "u99", "u98"])
        self.assertEqual(item["assignee_jobs"], ["산업 디자이너", "개발자", "기획자"])
        self.assertEqual(item["due"], "2026-12-24")
        self.assertEqual(item["evidence_utterance_indices"], self.action["evidence_utterance_indices"])
        self.assertTrue(any(h["stage"] == "assign" for h in item["history"]))
        record = saved["hitl"]["edits"][-1]
        self.assertEqual((record["item_id"], record["evidence_status"]), (self.action["item_id"], "NOT_NEEDED"))
        self.assertEqual(response.json()["agendas"], saved["agendas"])

    def test_same_values_do_not_add_records(self):
        self.run_review()
        before = self.final_path().read_text(encoding="utf-8")
        response = self.put({"item_id": self.action["item_id"], "assignees": [{"user_id": "u1", "name": "?"}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.final_path().read_text(encoding="utf-8"), before)

    def test_needs_final_json_and_valid_items(self):
        self.assertEqual(self.put({"item_id": self.action["item_id"], "due": None}).status_code, 404)
        self.run_review()
        not_action = next(d for d in self.original["agendas"][0]["major_discussions"] if d["discussion_tag"] != "액션")
        self.assertEqual(self.put({"item_id": not_action["item_id"], "due": None}).status_code, 422)
        self.assertEqual(self.put({"item_id": self.action["item_id"]}).status_code, 422)
        self.assertEqual(self.put({"item_id": self.action["item_id"], "assignees": [{"user_id": "", "name": "a"}]}).status_code, 422)


class ValidationTests(ReviewBase):
    def rejected(self, *items):
        llm = FakeLLM()
        with self.assertRaises(HitlEditError) as caught:
            self.run_review(*items, llm=llm)
        self.assertEqual(llm.calls, [])
        return " ".join(caught.exception.errors)

    def test_bad_edits_are_rejected_without_llm(self):
        item = self.action["item_id"]
        self.assertIn("없는 안건", self.rejected({"op": "create", "agenda_id": "없음", "text": "가"}))
        self.assertIn("액션명이 비어", self.rejected({"op": "create", "agenda_id": self.agenda, "text": " "}))
        self.assertIn("참여자가 아닌", self.rejected({"op": "update", "item_id": item, "assignee_user_ids": ["u9"]}))
        self.assertIn("없거나 삭제된", self.rejected({"op": "delete", "item_id": "없음"}))
        self.assertIn("변경할 값이 없음", self.rejected({"op": "update", "item_id": item}))
        self.assertIn("두 번", self.rejected({"op": "delete", "item_id": item}, {"op": "update", "item_id": item, "text": "가"}))
        decision = next(d for d in self.original["agendas"][0]["major_discussions"] if d["discussion_tag"] != "액션")
        self.assertIn("액션이 아닌", self.rejected({"op": "delete", "item_id": decision["item_id"]}))

    def test_missing_meeting_and_bad_id(self):
        for meeting_id in ("M_없음", "../밖"):
            with self.assertRaises(MeetingNotFoundError):
                review_meeting(meeting_id, edits(), llm=FakeLLM(), store=self.store)


class RouteTests(ReviewBase):
    def post(self, body, llm=None, meeting_id=None):
        app.dependency_overrides[get_llm] = lambda: llm or FakeLLM()
        return self.client.post(f"/api/meetings/{meeting_id or self.mid}/review", json=body)

    def test_returns_final_summary(self):
        body = self.post({"edits": [{"op": "create", "agenda_id": "기타", "text": "기타 액션"}]}).json()
        self.assertEqual(body["hitl"]["status"], "COMPLETED")
        self.assertEqual(body["agendas"][-1]["agenda_title"], "기타")
        for key in ("meeting_id", "project_id", "directory_id", "host_user_id"):
            self.assertEqual(body[key], self.original[key])

    def test_error_codes(self):
        self.assertEqual(self.post({"edits": []}, meeting_id="M_없음").status_code, 404)
        response = self.post({"edits": [{"op": "delete", "item_id": "없음"}]})
        self.assertEqual(response.status_code, 422)
        self.assertIn("없거나 삭제된", response.json()["detail"]["errors"][0])
        self.assertEqual(self.post({"edits": [{"op": "move"}]}).status_code, 422)
        failed = self.post({"edits": [{"op": "update", "item_id": self.action["item_id"], "text": "새"}]}, llm=FakeLLM(fail_at=1))
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("비밀-내부-정보", failed.text)

    def test_requires_login(self):
        app.dependency_overrides.pop(current_user)
        self.assertEqual(self.post({"edits": []}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
