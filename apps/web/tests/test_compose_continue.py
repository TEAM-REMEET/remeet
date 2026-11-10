"""중단된 분석 되살리기: 안내 창 「이어하기」 → 입력이 남은 작성 화면 · 「새 회의록」은 지금처럼 새로.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402
from state import actions, compose_ai, router  # noqa: E402
from state.store import AppState  # noqa: E402

JOB = {"status": "failed", "message": "서버가 다시 시작되어 분석이 중단됐어요.",
       "request": {"title": "주간 회의", "date": "2026-11-10", "host_user_id": "u1", "attendees": [{"user_id": "u1"}],
                   "directory_id": "weekly", "transcript_format": "plain", "transcript": "바트: 안녕하세요."},
       "draft": {"nn_name": ""}}


class ComposeContinueTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState(screen="home")
        for target, name, fn in [
            (actions, "get_state", lambda: self.state),
            (router, "request", lambda screen: None),
            (repo, "current_summary_job", lambda: JOB),
            (actions, "me_id", lambda: "u1"),
            (compose_ai, "match_speakers", lambda labels, people: ({}, [])),  # 구성원 목록(서버)을 부르지 않게
        ]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_continue_goes_to_compose_with_inputs(self):
        actions.compose_resume()  # 새로고침 뒤 사이드바가 부름
        self.assertEqual((self.state.modal, self.state.cp_resumed, self.state.nn_title), ("cp_format_error", True, "주간 회의"))
        actions.compose_continue()
        s = self.state
        self.assertEqual((s.screen, s.modal, s.cp_step, s.cp_resumed), ("compose", None, 1, False))
        self.assertEqual((s.nn_title, s.nn_text), ("주간 회의", "바트: 안녕하세요."))  # 입력 그대로
        self.assertIn("중단", s.nn_error)

    def test_closing_with_x_then_errors_say_ok_again(self):
        actions.compose_resume()
        actions.close_modal()
        self.assertFalse(self.state.cp_resumed)


if __name__ == "__main__":
    unittest.main()
