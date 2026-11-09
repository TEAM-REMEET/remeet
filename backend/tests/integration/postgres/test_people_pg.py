"""구성원 표(팀원 작업 · migrations/011 · seed_people.py · adapters/postgres/people.py)를 실제 DB 에서 + 회의 쪽과 연결.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_people_pg.py -v   (backend 폴더에서)
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')
ROOT = Path(__file__).resolve().parents[3] / 'src' / 'followup' / 'adapters' / 'postgres'  # schema.sql · migrations/
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))

ORG = {  # users.json · skills.json 과 같은 모양 (작게)
    "teams": [{"team_id": "t-pm", "name": "기획팀"}, {"team_id": "t-fin", "name": "재무팀"}],
    "roles": [{"role_id": "r-pm", "name": "PM", "team_id": "t-pm"}, {"role_id": "r-fin", "name": "재무", "team_id": "t-fin"}],
    "skills": [{"skill_id": "PM-01", "name": "일정 관리", "domain": "PM", "tools": "Microsoft Project|Jira"},
               {"skill_id": "FI-01", "name": "정산", "domain": "FIN", "tools": ""}],
    "people": [
        {"user_id": "u1", "email": "u1@t.local", "name": "김팀장", "position": "senior", "team_id": "t-pm",
         "role_id": "r-pm", "skills": [{"skill_id": "PM-01", "level": 4}], "password_hash": "scrypt$x"},
        {"user_id": "u2", "email": "u2@t.local", "name": "이신입", "position": "junior", "team_id": "t-pm",
         "role_id": "r-pm", "skills": [{"skill_id": "PM-01", "level": 1}]},
        {"user_id": "u3", "email": "u3@t.local", "name": "강수현", "position": "mid", "team_id": "t-fin",
         "role_id": "r-fin", "skills": []},
    ],
    "admins": [{"user_id": "ad", "email": "ad@t.local"}],
}


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class PeopleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(TEST_URL).get('dbname', '').endswith('_test'):
            raise RuntimeError('_test DB 만 허용')
        os.environ['DATABASE_URL'] = TEST_URL
        from followup.adapters.postgres.db import pool, transaction
        pool.cache_clear()
        cls.transaction = staticmethod(transaction)
        with transaction() as cur:
            cur.execute((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        from followup.adapters.postgres import migrate
        with redirect_stdout(io.StringIO()):
            migrate.main()

    @classmethod
    def tearDownClass(cls):
        from followup.adapters.postgres.db import pool
        if pool.cache_info().currsize:
            pool().close()
            pool.cache_clear()

    def setUp(self):
        from followup.adapters.postgres import seed_people
        with self.transaction() as cur:
            cur.execute('TRUNCATE notifications, meetings, folders, person_skills, people, skills, roles, teams, '
                        'accounts CASCADE')
        self.assertEqual(seed_people.check_org(ORG), [])
        seed_people.write(seed_people.build_rows(ORG), force=False)

    def test_seed_rows_and_refuse_reload(self):
        from followup.adapters.postgres import seed_people
        from followup.adapters.postgres.db import query
        self.assertEqual(query("SELECT tools FROM skills WHERE skill_id='PM-01'")[0]['tools'], ['Microsoft Project', 'Jira'])
        self.assertEqual({r['user_id']: r['role'] for r in query('SELECT user_id, role FROM accounts')},
                         {'u1': 'USER', 'u2': 'USER', 'u3': 'USER', 'ad': 'ADMIN'})
        self.assertEqual(query("SELECT count(*) AS n FROM accounts WHERE password_hash IS NOT NULL")[0]['n'], 0)
        self.assertEqual(query("SELECT count(*) AS n FROM people WHERE user_id = 'ad'")[0]['n'], 0)  # 관리자는 accounts 만
        with self.assertRaises(SystemExit):
            seed_people.write(seed_people.build_rows(ORG), force=False)
        seed_people.write(seed_people.build_rows(ORG), force=True)

    def test_sync_people_add_update_deactivate(self):
        from followup.adapters.postgres.common import ValidationError
        from followup.adapters.postgres.people import sync_people
        from followup.adapters.postgres.db import query
        p = {"user_id": "u9", "email": "u9@t.local", "permission": "user", "name": "새사람", "position": "junior",
             "team_id": "t-fin", "role_id": "r-new", "job": "회계", "skills": [{"skill_id": "FI-01", "level": 2}]}
        sync_people([p])
        self.assertEqual(query("SELECT team_id FROM roles WHERE role_id='r-new'")[0]['team_id'], 't-fin')
        sync_people([{**p, "position": "mid", "skills": [{"skill_id": "PM-01", "level": 3}]}])
        self.assertEqual(query("SELECT skill_id, level FROM person_skills WHERE user_id='u9'"),
                         [{'skill_id': 'PM-01', 'level': 3}])
        sync_people([{**p, "is_active": False, "skills": []}])
        self.assertEqual(query("SELECT is_active FROM people WHERE user_id='u9'")[0]['is_active'], False)
        with self.assertRaises(ValidationError):                          # 한 명이라도 틀리면 전부 취소
            sync_people([{**p, "user_id": "u10", "email": "u10@t.local"}, {**p, "user_id": "u11", "email": ""}])
        self.assertEqual(query("SELECT count(*) AS n FROM accounts WHERE user_id='u10'")[0]['n'], 0)

    def test_junior_cannot_host_senior_can(self):
        """compose.seniority() 가 people.position 을 봄 → 신입은 주관자 X (윗사람을 주관자로), mid · senior 는 주관자 O · 검토자 없음."""
        from followup.adapters.postgres import compose
        from followup.adapters.postgres.common import ValidationError
        content = {'summary': {'text': '요약', 'src': []}}
        jr = compose.save_draft(actor_id='u2', content=content)['id']                  # 신입 본인이 주관자
        with self.assertRaisesRegex(ValidationError, '주관자는 시니어 · 미드만'):
            compose.submit_meeting(actor_id='u2', meeting_id=jr)
        jr2 = compose.save_draft(actor_id='u2', content=content, host_id='u1')['id']   # 윗사람을 주관자로 → 넘김
        compose.handoff_meeting(actor_id='u2', meeting_id=jr2, request={})
        self.assertIsNone(compose.submit_meeting(actor_id='u1', meeting_id=jr2)['reviewer_id'])
        sr = compose.save_draft(actor_id='u3', content=content)['id']
        with self.assertRaisesRegex(ValidationError, '검토자'):
            compose.submit_meeting(actor_id='u3', meeting_id=sr, reviewer_id='u1')
        self.assertIsNone(compose.submit_meeting(actor_id='u3', meeting_id=sr)['reviewer_id'])

    def test_sender_name_and_people_search(self):
        from followup.adapters.postgres import compose, detail, folders, search, shares
        p = folders.create_folder(actor_id='u3', name='정산', members=['u1'])
        m = compose.save_draft(actor_id='u3', content={'summary': {'text': '정산 요약', 'src': []}}, project_id=p['id'],
                               attendees=['u2'], transcript=[{'line_id': '1', 'speaker': '이신입', 'text': '정산 일정 공유'}])
        compose.submit_meeting(actor_id='u3', meeting_id=m['id'])
        shares.create_share(actor_id='u3', meeting_id=m['id'], recipient_user_id='u1', scope='summary')
        d = detail.get_meeting_detail(actor_id='u1', meeting_id=m['id'])
        self.assertEqual((d['shared']['from'], d['shared']['sender_id']), ('강수현 · 재무팀', 'u3'))   # web ShareInfo.sender
        self.assertEqual(search.find_people(actor_id='u1', q='신입'), {'u2': '이신입'})
        res = search.search_all(actor_id='u3', q='신입')
        self.assertEqual([(r['id'], r['role'], r['said']) for r in res['people']], [(m['id'], '참석', ['정산 일정 공유'])])


if __name__ == '__main__':
    unittest.main()