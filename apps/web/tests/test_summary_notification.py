"""「요약이 끝났어요」 알림을 누르면 분석 및 검토 화면으로 (서버 없이).

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import actions  # noqa: E402


class SummaryNotificationTests(unittest.TestCase):
    def click(self, status: str):
        noti = SimpleNamespace(id="n-1", kind="created", meeting=None, folder=None)
        state = SimpleNamespace(read_notifications=set())
        with patch.object(actions, "get_state", return_value=state), \
                patch.object(actions.sel, "notifications", return_value=[noti]), \
                patch.object(actions.repo, "mark_notification_read"), \
                patch.object(actions.repo, "current_summary_job", return_value={"status": status}), \
                patch.object(actions, "go") as go, patch.object(actions, "_set") as set_:
            actions.open_notification("n-1")
        return go, set_

    def test_finished_job_opens_review_screen(self):
        go, _ = self.click("done")
        go.assert_called_once_with("compose")

    def test_already_cleared_job_only_explains(self):
        go, set_ = self.click("none")
        go.assert_not_called()
        set_.assert_called_once_with(toast="이미 확정했거나 지운 분석 결과예요.")


if __name__ == "__main__":
    unittest.main()
