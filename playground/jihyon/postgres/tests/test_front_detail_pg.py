"""web 목업 → seed.py → PostgreSQL → crud.detail.get_meeting_detail() → web 의 _meeting() 이 목업과 같은 Meeting 을 만드는지.

web 폴더가 있어야 돌아요 (WEB_DIR 또는 저장소 기본 위치). 이름이 _test 로 끝나는 DB 만 쓰고, 그 DB 는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  uv run python -m unittest discover -s tests -p test_front_detail_pg.py -v
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import fields
from pathlib import Path

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')
ROOT = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get('WEB_DIR') or ROOT.parents[2] / 'web')
HAS_WEB = (WEB / 'data' / 'mock' / 'meetings.json').exists()

# 목업과 일부러 다른 칸
#   when   : 요일은 DB 가 실제 날짜(config.YEAR=2026)로 계산 — 목업 요일은 2025년 달력 기준이라 하루씩 어긋남
#   topic  : 예전 period- 폴더(주차별 · 월별 전용)는 없앴으므로 그 회의는 프로젝트 없음(None)
#   shared : 보낸 사람이 DB 에서는 id (화면이 구성원 표로 "이름 · 팀" 으로 바꿈)
EXPECTED_DIFF = {'when', 'topic', 'shared'}


@unittest.skipUnless(TEST_URL and HAS_WEB, 'TEST_DATABASE_URL 과 web 폴더(WEB_DIR)가 필요합니다.')
class FrontDetailTests(unittest.TestCase):
    def test_every_mock_meeting_round_trips(self):
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(TEST_URL).get('dbname', '').endswith('_test'):
            raise RuntimeError('_test DB 만 허용')
        os.environ['DATABASE_URL'] = TEST_URL
        os.environ['WEB_DIR'] = str(WEB)
        from db import pool
        pool.cache_clear()
        import migrate
        import seed
        with redirect_stdout(io.StringIO()):
            seed.seed()
            migrate.main()
        from data import repository as front       # web/data/repository.py
        from crud.detail import get_meeting_detail

        mock = json.loads((WEB / 'data' / 'mock' / 'meetings.json').read_text(encoding='utf-8'))
        for d in mock:
            actor = seed.SEED_USER_ID if d.get('shared') else d['host']
            with self.subTest(meeting=d['id'], actor=actor):
                got = get_meeting_detail(actor_id=actor, meeting_id=d['id'])
                a, b = front._meeting(d), front._meeting(got)
                diff = {f.name for f in fields(a) if getattr(a, f.name) != getattr(b, f.name)}
                self.assertLessEqual(diff, EXPECTED_DIFF)
                self.assertEqual(b.date, a.date)
                if a.shared:
                    self.assertEqual((b.shared.scope, b.shared.received, b.shared.lock_raw),
                                     (a.shared.scope, a.shared.received, a.shared.lock_raw))
                if a.topic and not a.topic.startswith('period-'):
                    self.assertEqual(b.topic, a.topic)
        pool().close()
        pool.cache_clear()


if __name__ == '__main__':
    unittest.main()
