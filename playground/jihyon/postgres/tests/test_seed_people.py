"""seed_people.py 파싱 검사 (DB 없이). postgres/ 폴더에서:
    python -m unittest discover -s tests -p test_seed_people.py -v
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import seed_people  # noqa: E402


class SeedPeopleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.org = seed_people.read_org(seed_people.SEED_DIR)

    def test_real_seed_files_are_valid(self):
        self.assertEqual(seed_people.check_org(self.org), [])

    def test_rows_match_seed(self):
        rows = seed_people.build_rows(self.org)
        self.assertEqual(len(rows["people"]), len(self.org["people"]))
        self.assertEqual(len(rows["person_skills"]), sum(len(p["skills"]) for p in self.org["people"]))
        # 관리 계정은 accounts 에만 (people · 스킬 없음)
        admins = {a["user_id"] for a in self.org["admins"]}
        self.assertEqual({r[0] for r in rows["accounts"] if r[2] == "ADMIN"}, admins)
        self.assertFalse(admins & {r[0] for r in rows["people"]})
        # 비밀번호는 넣지 않는다
        self.assertTrue(all(len(r) == 3 for r in rows["accounts"]))
        # "Microsoft Project|Jira" → 배열
        pm01 = next(r for r in rows["skills"] if r[0] == "PM-01")
        self.assertEqual(pm01[4], ["Microsoft Project", "Jira"])

    def test_bad_references_are_reported(self):
        org = copy.deepcopy(self.org)
        p = org["people"][0]
        p["team_id"], p["position"] = "t-none", "intern"
        p["skills"].append({"skill_id": "XX-99", "level": 9})
        org["people"].append(copy.deepcopy(org["people"][1]))
        errors = "\n".join(seed_people.check_org(org))
        for word in ("없는 팀 t-none", "직급", "없는 스킬 XX-99", "레벨은 1~5", "user_id 중복", "이메일 중복"):
            self.assertIn(word, errors)


if __name__ == "__main__":
    unittest.main()
