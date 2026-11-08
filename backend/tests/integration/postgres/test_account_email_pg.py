"""외부 도구 전송용 이메일 조회 (adapters/postgres/people.emails_of · migrations/020).

앱 계정(remeet_app)도 accounts.email 은 읽고 password_hash 는 못 읽는지 확인.
이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_account_email_pg.py -v   (backend 폴더에서)
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


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class AccountEmailTests(unittest.TestCase):
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
        with transaction() as cur:
            cur.execute("INSERT INTO accounts (user_id, email) VALUES ('u1', 'u1@test.local'), ('u2', 'u2@test.local')")

    @classmethod
    def tearDownClass(cls):
        from followup.adapters.postgres.db import pool
        if pool.cache_info().currsize:
            pool().close()
            pool.cache_clear()

    def test_emails_of(self):
        from followup.adapters.postgres import people
        self.assertEqual(people.emails_of({'u1', 'u2', 'nobody'}), {'u1': 'u1@test.local', 'u2': 'u2@test.local'})
        self.assertEqual(people.emails_of(set()), {})

    def test_app_role_reads_email_not_password(self):
        import psycopg
        from followup.adapters.postgres import people
        with self.transaction() as cur:
            cur.execute('SET LOCAL ROLE remeet_app')
            cur.execute(people.EMAILS, (['u1'],))
            self.assertEqual(cur.fetchall(), [{'user_id': 'u1', 'email': 'u1@test.local'}])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.transaction() as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute('SELECT password_hash FROM accounts')


if __name__ == '__main__':
    unittest.main()
