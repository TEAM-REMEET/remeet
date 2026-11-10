"""새 회의록 주관자 바꾸기 (state/actions.confirm_pick): 작성자 '나'는 참석자에 남고, 다른 예전 주관자는 빠짐.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import actions  # noqa: E402
from state import selectors as sel  # noqa: E402
from state.store import AppState  # noqa: E402


class ConfirmHostTests(unittest.TestCase):
    def pick(self, host, people, new_host):
        s = AppState(user_id="me", nn_host=host, nn_people=people, pick_draft=[new_host])
        with patch.object(actions, "get_state", lambda: s), patch.object(actions, "me_id", lambda: "me"), \
                patch.object(sel, "can_host", lambda uid: True), patch.object(actions, "close_modal", lambda: None):
            actions.confirm_pick("cp_host")
        return s.nn_people

    def test_me_stays_when_host_changes_from_me(self):
        self.assertEqual(self.pick("me", ["me", "u2"], "u3"), ["u3", "me", "u2"])

    def test_other_old_host_is_dropped(self):
        self.assertEqual(self.pick("u3", ["u3", "me", "u2"], "u4"), ["u4", "me", "u2"])


if __name__ == "__main__":
    unittest.main()
