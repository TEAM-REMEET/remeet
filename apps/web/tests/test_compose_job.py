"""분석이 끝났을 때 (state/actions._job_done): 서버가 넘김 → 새 화면 · 서버가 저장 → 다시 저장 안 함 · 프로젝트 지워짐 → 1단계.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import actions  # noqa: E402
from state.store import AppState  # noqa: E402

RESULT = {"meeting_id": "M_1", "project_id": "P_1"}


class JobDoneTests(unittest.TestCase):
    def done(self, **job):
        s, calls = AppState(cp_step=2, cp_job_running=True), []
        with patch.object(actions.repo, "meetings_changed", lambda: calls.append("refresh")), \
                patch.object(actions, "_handed_off", lambda st: calls.append("handed_off")), \
                patch.object(actions, "_apply_result", lambda st, r: calls.append("apply")), \
                patch.object(actions, "_save_analysis", lambda st: calls.append("save")), \
                patch.object(actions, "_format_error", lambda e: calls.append(("error", e))):
            actions._job_done(s, {"status": "done", "result": RESULT, **job})
        return s, calls

    def test_server_handed_off(self):
        s, calls = self.done(saved="handoff")
        self.assertEqual(calls, ["refresh", "handed_off"])
        self.assertFalse(s.cp_job_running)

    def test_server_saved_draft_is_not_saved_again(self):
        s, calls = self.done(saved="draft")
        self.assertEqual(calls, ["refresh", "apply", "save"])
        self.assertEqual(s.cp_saved_id, "M_1")   # _save_analysis 가 같은 id 면 다시 저장하지 않음

    def test_project_deleted_goes_back_to_step_1(self):
        s, calls = self.done(saved=None, save_error="선택한 프로젝트가 삭제됐어요.")
        self.assertEqual(calls, ["refresh", ("error", "선택한 프로젝트가 삭제됐어요.")])
        self.assertEqual(s.cp_step, 1)

    def test_server_save_failed_falls_back_to_web_save(self):
        s, calls = self.done(saved=None)
        self.assertEqual(calls, ["apply", "save"])
        self.assertEqual(s.cp_saved_id, "")


if __name__ == "__main__":
    unittest.main()
