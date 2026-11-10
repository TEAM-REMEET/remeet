"""즐겨찾기 · 최근 (state/actions): 로그인할 때 서버에서 불러오고, 바꿀 때마다 저장 (서버 없이 가짜 repo).

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import api  # noqa: E402
from data import repository as repo  # noqa: E402
from state import actions  # noqa: E402
from state.store import AppState  # noqa: E402


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.state, self.saved = AppState(), []
        for target, name, fn in [
            (actions, "get_state", lambda: self.state),
            (repo, "save_preference", lambda key, value: self.saved.append((key, value))),
        ]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_load_fills_state(self):
        with patch.object(repo, "preferences", return_value={"people_fav": ["u2", "u3"], "team_recent": ["기획팀"]}):
            actions._load_preferences()
        self.assertEqual((self.state.pp_fav, self.state.adm_org_recent, self.state.pp_recent),
                         ({"u2", "u3"}, ["기획팀"], []))  # 저장 안 된 키(최근 사람)는 빈 채로

    def test_load_failure_keeps_login_going(self):
        with patch.object(repo, "preferences", side_effect=api.ApiError("백엔드에 연결할 수 없어요")):
            actions._load_preferences()  # 예외 없이 빈 즐겨찾기로
        self.assertEqual(self.state.pp_fav, set())

    def test_each_change_is_saved(self):
        actions.people_fav("u3")
        actions.people_fav("u2")
        actions.people_fav("u3")  # 다시 누르면 빠짐
        actions.people_pick("ctx", "u5", lambda pid: None)
        actions.people_pick("ctx", "u5", lambda pid: None)  # 맨 앞 사람을 다시 고르면 저장 안 함
        actions.org_fav("기획팀")
        self.assertEqual(self.saved, [("people_fav", ["u3"]), ("people_fav", ["u2", "u3"]), ("people_fav", ["u2"]),
                                      ("people_recent", ["u5"]), ("team_fav", ["기획팀"])])

    def test_save_failure_tells_only_for_favorites(self):
        with patch.object(repo, "save_preference", side_effect=api.ApiError("x")):
            actions.people_pick("ctx", "u5", lambda pid: None)
            self.assertFalse(self.state.toast)  # 최근은 조용히
            actions.people_fav("u3")
        self.assertIn("즐겨찾기를 저장하지 못했어요", self.state.toast)
        self.assertEqual(self.state.pp_fav, {"u3"})  # 화면은 그대로


if __name__ == "__main__":
    unittest.main()
