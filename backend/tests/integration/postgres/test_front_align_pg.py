"""프론트(web) 기준 맞춤 (migrations/010): 이름 · item_id · 마감일 · 검토 끝난 요약 JSON · 물어보기 기록 · 공유 목록 · 알림 종류.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_front_align_pg.py -v   (backend 폴더에서)
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
class FrontAlignTests(unittest.TestCase):
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
        with self.transaction() as cur:
            cur.execute('TRUNCATE ask_history, notifications, meetings, folders, accounts CASCADE')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@test.local','USER'), ('u2','u2@test.local','USER'), ('ad','ad@test.local','ADMIN')""")

    def test_column_names_match_front(self):
        with self.transaction() as cur:
            cur.execute("""SELECT table_name, column_name FROM information_schema.columns
                           WHERE (table_name, column_name) IN (('meetings','project_id'), ('meetings','directory_id'),
                                 ('meetings','folder_id'), ('meetings','type_id'), ('folders','type_id'),
                                 ('meeting_todos','item_id'), ('meeting_summaries','summary_json'))""")
            cols = {(r['table_name'], r['column_name']) for r in cur.fetchall()}
        self.assertEqual(cols, {('meetings', 'project_id'), ('meetings', 'directory_id'),
                                ('meeting_todos', 'item_id'), ('meeting_summaries', 'summary_json')})

    def test_item_id_due_and_summary_json(self):
        from followup.adapters.postgres import compose, detail
        summary_json = {'meeting_id': 'x', 'action_items': [{'item_id': 'ai-1', 'description': '견적 비교'}]}
        m = compose.save_draft(actor_id='u1', content={'summary': {'text': '요약', 'src': []}},
                               todos=[{'text': '견적 비교', 'item_id': 'ai-1', 'due': '11/5', 'src': []},
                                      {'text': '공유', 'src': []}], summary_json=summary_json)
        d = detail.get_meeting_detail(actor_id='u1', meeting_id=m['id'])
        self.assertEqual([(t['item_id'], t['due']) for t in d['todos']], [('ai-1', '11/5'), ('', '')])
        self.assertEqual(detail.get_summary_json(actor_id='u1', meeting_id=m['id']), summary_json)
        tid = d['todos'][0]['id']
        self.assertEqual(detail.set_todo_due(actor_id='u1', todo_id=tid, due='11/7', due_date='2026-11-07')['due_label'],
                         '11/7')
        self.assertEqual(detail.list_todos(actor_id='u1', meeting_id=m['id'])[0]['due'], '11/7')
        from followup.adapters.postgres.common import PermissionDenied
        with self.assertRaises(PermissionDenied):
            detail.set_todo_due(actor_id='u2', todo_id=tid, due='오늘')

    def test_ask_history_is_private(self):
        from followup.adapters.postgres import ask
        from followup.adapters.postgres.common import NotFoundError
        a = ask.save_ask(actor_id='u1', question='PG사 정했어?', answer={'answer_status': 'grounded', 'summary': '아직'})
        ask.save_ask(actor_id='u1', question='출시일?', answer={'answer_status': 'no_evidence'})
        self.assertEqual([h['question'] for h in ask.list_ask_history(actor_id='u1')], ['출시일?', 'PG사 정했어?'])
        self.assertEqual(ask.list_ask_history(actor_id='u1')[1]['answer']['summary'], '아직')
        self.assertEqual(ask.list_ask_history(actor_id='u2'), [])
        with self.assertRaises(NotFoundError):
            ask.delete_ask(actor_id='u2', ask_id=a['id'])
        ask.delete_ask(actor_id='u1', ask_id=a['id'])
        self.assertEqual(ask.clear_ask_history(actor_id='u1'), 1)

        def rls(user):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute('SELECT count(*) AS n FROM ask_history')
                return cur.fetchone()['n']
        ask.save_ask(actor_id='u1', question='q', answer={})
        self.assertEqual((rls('u1'), rls('u2')), (1, 0))

    def test_sent_and_received_lists(self):
        from followup.adapters.postgres import shares
        from followup.adapters.postgres.meetings import create_meeting
        with self.transaction() as cur:   # 팀 공유는 teams 를 가리킴 (migrations/012)
            cur.execute("INSERT INTO teams (team_id, name) VALUES ('fin', '재무팀') ON CONFLICT DO NOTHING")
        create_meeting(actor_id='u1', meeting_id='m1', title='주간')
        create_meeting(actor_id='u1', meeting_id='m2', title='월간')
        shares.create_share(actor_id='u1', meeting_id='m1', recipient_user_id='u2', scope='summary')
        s2 = shares.create_share(actor_id='u1', meeting_id='m2', recipient_team_id='fin', scope='full')
        self.assertEqual([(x['meeting_id'], x['recipient_user_id'], x['recipient_team_id'])
                          for x in shares.list_sent(actor_id='u1')], [('m2', None, 'fin'), ('m1', 'u2', None)])
        self.assertEqual([(x['meeting_id'], x['sender_id'], x['scope']) for x in shares.list_received(actor_id='u2')],
                         [('m1', 'u1', 'summary')])
        shares.revoke_share(actor_id='u1', share_id=s2['id'])
        self.assertEqual(len(shares.list_sent(actor_id='u1')), 1)

    def test_foreign_keys_block_unknown_people(self):
        """012: 없는 사람 id 는 DB 가 막음 · 기존 데이터에 있으면 012 가 목록을 보여 주며 멈춤."""
        import psycopg
        from followup.adapters.postgres.meetings import create_meeting
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.transaction() as cur:
                cur.execute("INSERT INTO meetings (id, host_user_id) VALUES ('x', 'ghost')")
        create_meeting(actor_id='u1', meeting_id='m1')
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):       # 계정 삭제는 막힘 (비활성화 규칙)
            with self.transaction() as cur:
                cur.execute("DELETE FROM accounts WHERE user_id = 'u1'")
        sql = (ROOT / 'migrations' / '012_user_foreign_keys.sql').read_text(encoding='utf-8')
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "notifications.user_id.*'ghost'"):
            with self.transaction() as cur:       # 예전 데이터 흉내: 외래 키를 빼고 없는 사람 알림을 넣은 뒤 012 를 다시
                cur.execute('ALTER TABLE notifications DROP CONSTRAINT notifications_user_id_account_fk')
                cur.execute("INSERT INTO notifications (id, user_id, kind, text) VALUES ('n-x', 'ghost', 'review', 'x')")
                cur.execute(sql)
        with self.transaction() as cur:           # 위 트랜잭션은 통째로 취소됐으니 외래 키는 그대로
            cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'notifications_user_id_account_fk'")
            self.assertIsNotNone(cur.fetchone())

    def test_budget_notification_kind(self):
        from followup.adapters.postgres import notifications
        n = notifications.create_notification(actor_id='ad', user_id='ad', kind='budget', text='예산 80% 사용')
        self.assertEqual(n['kind'], 'budget')


if __name__ == '__main__':
    unittest.main()
