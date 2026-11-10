"""사람 표 기억 (data/repository._people_tables): 긴 원문을 그릴 때 사람 표를 문단마다 새로 만들지 않음.
가짜 구성원 목록(people_rows)으로 몇 번 만드는지 세요 (서버 없이).

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402


class PeopleMemoTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"user_id": "u1", "name": "서윤", "team": "기획"}, {"user_id": "u2", "name": "민준", "team": None}]
        self.fetches = 0
        self.token = "tok"

        def rows():
            self.fetches += 1
            return [dict(r) for r in self.rows]  # st.cache_data 처럼 매번 복사본

        for target, name, fn in [(repo, "people_rows", rows), (repo, "_token", lambda: self.token)]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)
        repo._people_memo.clear()
        self.addCleanup(repo._people_memo.clear)

    def test_many_lookups_build_once(self):
        for _ in range(500):  # 원문 문단마다 화자 찾기
            self.assertEqual(repo.people_by_short_name()["서윤"], "u1")
            self.assertEqual(repo.people()["u2"].team, "")
        self.assertEqual(self.fetches, 1)

    def test_people_change_rebuilds(self):
        repo.people()
        self.rows[0]["name"] = "서윤2"
        with patch.object(repo, "_people_rows"), patch.object(repo, "_catalog"), patch.object(repo, "org_changed"):
            repo._people_changed()  # 구성원을 고침 → 다음에 새로 만듦
        self.assertEqual(repo.people()["u1"].name, "서윤2")
        self.assertEqual(self.fetches, 2)

    def test_expires_and_separates_tokens(self):
        repo.people()
        self.token = "other"  # 다른 사람 출입증 → 따로
        repo.people()
        self.assertEqual(self.fetches, 2)
        later = repo.time.monotonic() + repo._PEOPLE_MEMO_TTL + 1
        with patch.object(repo.time, "monotonic", lambda: later):
            repo.people()
        self.assertEqual(self.fetches, 3)


if __name__ == "__main__":
    unittest.main()
