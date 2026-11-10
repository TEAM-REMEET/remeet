"""검토 중인 회의를 열면: 내가 확정할 사람(주관자 · 검토자)이면 3단계(담당자 추천) 화면, 아니면 회의 상세.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402
from state import actions, router  # noqa: E402
from state import selectors as sel  # noqa: E402
from state.store import AppState  # noqa: E402


class OpenReviewMeetingTests(unittest.TestCase):
    def setUp(self):
        self.state, self.status, self.manager = AppState(), "review", True
        for target, name, fn in [
            (actions, "get_state", lambda: self.state),
            (router, "request", lambda screen: None),
            (repo, "meeting", lambda mid: SimpleNamespace(id=mid)),
            (repo, "is_saved", lambda mid: True),
            (sel, "status_of", lambda s, mid: self.status),
            (sel, "manages", lambda m: self.manager),
        ]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_manager_opens_step_three(self):
        actions.open_meeting("M_1")
        self.assertEqual((self.state.screen, self.state.fin_step, self.state.fin_flow), ("finalize", 1, "M_1"))

    def test_detail_and_others_stay_on_meeting_page(self):
        actions.open_meeting("M_1", detail=True)  # 3단계 화면의 「회의록 보기」
        self.assertEqual(self.state.screen, "note")
        actions.open_meeting("M_1", editing=True)
        self.assertEqual(self.state.screen, "note")
        self.manager = False  # 참석자 · 공유받은 사람
        actions.open_meeting("M_1")
        self.assertEqual(self.state.screen, "note")
        self.manager, self.status = True, "confirmed"  # 확정 뒤
        actions.open_meeting("M_1")
        self.assertEqual(self.state.screen, "note")


if __name__ == "__main__":
    unittest.main()
