"""분석 직후 서버 저장 (application/save_analysis): 내가 주관자 → draft · 다른 사람 → 새 프로젝트 만들고 넘김 · 지워진 프로젝트.
PostgreSQL 대신 가짜 함수로 무엇을 불렀는지만 봐요.
"""
import unittest
from unittest.mock import patch

from followup.adapters.postgres.common import NotFoundError
from followup.analysis.summary.transcript_builder import MeetingSubmission
from followup.application import save_analysis

BASE = {"directory_id": "weekly", "title": "1차 회의", "date": "2026-11-05", "host_user_id": "u1",
        "attendees": [{"user_id": "u1", "name": "바트", "job": "PM"}, {"user_id": "u2", "name": "리처드", "job": "디자인"}],
        "transcript_format": "colon", "transcript": "바트: 안녕하세요."}
SUMMARY = {"meeting_id": "M_1", "project_id": "P_NEW"}


class SaveResultTests(unittest.TestCase):
    def run_save(self, writer, existing=(), **req):
        calls = []

        def get_folder(*, actor_id, folder_id):
            if folder_id not in existing:
                raise NotFoundError("없음")

        with patch.object(save_analysis.folders, "get_folder", get_folder), \
                patch.object(save_analysis.folders, "create_folder", lambda **kw: calls.append(("create", kw))), \
                patch.object(save_analysis.compose, "save_draft", lambda **kw: calls.append(("draft", kw))), \
                patch.object(save_analysis.compose, "handoff_meeting", lambda **kw: calls.append(("handoff", kw))):
            result = save_analysis.save_result(writer, MeetingSubmission(**{**BASE, **req}), SUMMARY, {"nn_name": "리모컨"})
        return result, calls

    def test_host_writer_saves_draft_in_picked_project(self):
        result, calls = self.run_save("u1", existing={"P_A"}, project_id="P_A")
        self.assertEqual(result, "draft")
        self.assertEqual([c[0] for c in calls], ["draft"])
        self.assertEqual(calls[0][1]["project_id"], "P_A")
        self.assertEqual(calls[0][1]["attendees"], ["u1", "u2"])

    def test_host_writer_new_project_is_not_created_yet(self):
        result, calls = self.run_save("u1")
        self.assertEqual(result, "draft")
        self.assertEqual([c[0] for c in calls], ["draft"])
        self.assertIsNone(calls[0][1]["project_id"])

    def test_other_host_creates_new_project_then_hands_off(self):
        result, calls = self.run_save("u2")
        self.assertEqual(result, "handoff")
        self.assertEqual([c[0] for c in calls], ["create", "draft", "handoff"])
        self.assertEqual((calls[0][1]["folder_id"], calls[0][1]["name"], calls[0][1]["members"]), ("P_NEW", "리모컨", ["u1"]))
        self.assertEqual(calls[1][1]["project_id"], "P_NEW")
        self.assertEqual(calls[2][1]["draft"], {"nn_name": "리모컨"})

    def test_deleted_project_raises_before_saving(self):
        with self.assertRaises(save_analysis.ProjectGone):
            self.run_save("u2", project_id="P_GONE")


if __name__ == "__main__":
    unittest.main()
