"""회의록 작성 흐름 (migrations/008): 미리보기 저장 → 생성(알림) → 윗사람 검토 · 수정 → 확정.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_compose_pg.py -v   (backend 폴더에서)
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

CONTENT = {
    'summary': {'text': '출시 일정 논의', 'src': ['1']},
    'agendas': [{'title': '출시 일정', 'status': '논의', 'tone': 'info', 'src': ['1'],
                 'points': [{'text': '12/1 가능', 'src': ['2']}]}],
}
LINES = [{'line_id': '1', 'speaker': '신입', 'text': '출시 일정 얘기할게요'},
         {'line_id': '2', 'speaker': '팀장', 'text': '12/1 가능해요'}]


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class ComposeFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(TEST_URL).get('dbname', '').endswith('_test'):
            raise RuntimeError('안전을 위해 이름이 _test 로 끝나는 별도 데이터베이스만 허용합니다.')
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
            cur.execute('TRUNCATE notifications, meeting_status_history, meetings, folders, accounts CASCADE')
            # boss = 윗사람 · jr = 신입 · u2 = 참석자 · u3 = 관계없는 사람
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('boss','boss@test.local','USER'), ('jr','jr@test.local','USER'),
                       ('u2','u2@test.local','USER'), ('u3','u3@test.local','USER'), ('ad','ad@test.local','ADMIN')""")

    def _notis(self, user):
        with self.transaction() as cur:
            cur.execute('SELECT kind FROM notifications WHERE user_id=%s ORDER BY created_at', (user,))
            return [r['kind'] for r in cur.fetchall()]

    def _draft(self, **kw):
        from followup.adapters.postgres import compose
        args = dict(actor_id='jr', content=CONTENT, title='출시 회의', attendees=['boss', 'u2'], transcript=LINES,
                    todos=[{'text': '일정표 공유', 'owners': ['jr'], 'src': ['2']}])
        args.update(kw)
        return compose.save_draft(**args)

    def test_junior_writes_senior_edits_and_confirms(self):
        from followup.adapters.postgres import compose, detail, minutes
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        from followup.adapters.postgres.meetings import list_status_history, update_meeting

        # ① 미리보기 저장 (두 번 저장 → 요약 버전 2) · 작성자 · 주관자만 보임
        d = self._draft()
        mid = d['id']
        self.assertEqual((d['status'], d['created_by'], d['host_user_id'], d['summary_version']),
                         ('draft', 'jr', 'jr', 1))
        self.assertEqual(self._draft(meeting_id=mid, title='출시 회의 (수정)')['summary_version'], 2)
        self.assertEqual([x['id'] for x in compose.list_my_drafts(actor_id='jr')], [mid])
        for who in ('boss', 'u2'):
            with self.subTest(who=who):
                with self.assertRaises(PermissionDenied):
                    detail.get_meeting_detail(actor_id=who, meeting_id=mid)
                self.assertEqual(minutes.list_all_meetings(actor_id=who), [])
        with self.assertRaises(ValidationError):            # 생성은 전용 함수로만
            update_meeting(actor_id='jr', meeting_id=mid, status='review')

        # ② 생성: 신입이 윗사람을 검토자로 지정 → 알림
        with self.assertRaises(ValidationError):
            compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='jr')
        with self.assertRaises(ValidationError):
            compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='ad')
        with self.assertRaises(PermissionDenied):
            compose.submit_meeting(actor_id='u2', meeting_id=mid, reviewer_id='boss')
        compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='boss')
        self.assertEqual((self._notis('u2'), self._notis('boss'), self._notis('jr')), (['created'], ['review'], []))
        self.assertEqual([x['id'] for x in compose.list_review_queue(actor_id='boss')], [mid])
        with self.assertRaises(ValidationError):
            compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='boss')
        with self.assertRaises(ValidationError):            # 생성된 뒤엔 미리보기 저장 X
            self._draft(meeting_id=mid)

        # ③ 신입은 이제 읽기만 · 윗사람이 수정
        dj = detail.get_meeting_detail(actor_id='jr', meeting_id=mid)
        self.assertEqual((dj['can_edit'], dj['author'], dj['reviewer'], len(dj['paragraphs'])), (False, 'jr', 'boss', 2))
        for call in (lambda: detail.save_summary(actor_id='jr', meeting_id=mid, content=CONTENT),
                     lambda: detail.add_todo(actor_id='jr', meeting_id=mid, text='x'),
                     lambda: compose.confirm_meeting(actor_id='jr', meeting_id=mid)):
            with self.subTest(call=call):
                with self.assertRaisesRegex(PermissionDenied, '검토자'):
                    call()
        detail.save_summary(actor_id='boss', meeting_id=mid, content={**CONTENT, 'summary': {'text': '팀장 수정', 'src': []}})
        self.assertTrue(detail.get_meeting_detail(actor_id='boss', meeting_id=mid)['can_edit'])
        self.assertEqual(detail.get_meeting_detail(actor_id='u2', meeting_id=mid)['summary']['text'], '팀장 수정')

        # ④ 확정
        with self.assertRaises(PermissionDenied):
            compose.confirm_meeting(actor_id='u2', meeting_id=mid)
        c = compose.confirm_meeting(actor_id='boss', meeting_id=mid)
        self.assertEqual((c['status'], c['confirmed_by']), ('confirmed', 'boss'))
        self.assertIsNotNone(c['confirmed_at'])
        with self.assertRaises(ValidationError):
            compose.confirm_meeting(actor_id='boss', meeting_id=mid)
        self.assertEqual([(h['from_status'], h['to_status'], h['changed_by'])
                          for h in list_status_history(actor_id='u2', meeting_id=mid)],
                         [(None, 'draft', 'jr'), ('draft', 'review', 'jr'), ('review', 'confirmed', 'boss')])

    def test_without_reviewer_host_confirms(self):
        from followup.adapters.postgres import compose, detail
        from followup.adapters.postgres.common import ValidationError
        mid = self._draft(actor_id='boss', attendees=['u2'])['id']
        with self.transaction() as cur:                     # 미리보기(요약) 없는 draft
            cur.execute("""INSERT INTO meetings (id, host_user_id, status, created_by)
                           VALUES ('empty', 'boss', 'draft', 'boss')""")
        with self.assertRaisesRegex(ValidationError, '미리보기'):
            compose.submit_meeting(actor_id='boss', meeting_id='empty')
        compose.submit_meeting(actor_id='boss', meeting_id=mid)                       # 내가 주관자 → 검토 없이 바로 확정
        self.assertEqual((self._notis('u2'), self._notis('boss')), (['created'], []))   # 나한테는 알림 X · 검토 알림 없음
        self.assertTrue(detail.get_meeting_detail(actor_id='boss', meeting_id=mid)['can_edit'])
        self.assertEqual(compose.confirm_meeting(actor_id='boss', meeting_id=mid)['status'], 'confirmed')

    def test_junior_with_senior_host(self):
        """신입이 쓰고 주관자는 윗사람: 생성할 때 주관자를 검토자로 지정 → 생성 뒤 신입은 못 고침."""
        from followup.adapters.postgres import compose, detail
        from followup.adapters.postgres.common import PermissionDenied
        mid = self._draft(host_id='boss')['id']
        self.assertTrue(detail.get_meeting_detail(actor_id='boss', meeting_id=mid)['can_edit'])   # draft: 주관자도 봄
        compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='boss')
        self.assertEqual(self._notis('boss'), ['review'])
        with self.assertRaises(PermissionDenied):
            detail.save_summary(actor_id='jr', meeting_id=mid, content=CONTENT)
        compose.confirm_meeting(actor_id='boss', meeting_id=mid)

    def test_author_not_host_requests_review(self):
        """주관자가 아닌 사람이 씀 → 생성 = 「검토 요청하기」: 주관자에게 review 알림, 주관자만 고치고 확정 (web compose_finish)."""
        from followup.adapters.postgres import compose, detail
        from followup.adapters.postgres.common import PermissionDenied
        mid = self._draft(actor_id='u2', host_id='boss', attendees=['jr'])['id']
        r = compose.submit_meeting(actor_id='u2', meeting_id=mid)
        self.assertEqual(r['reviewer_id'], 'boss')                                     # 자동으로 주관자
        self.assertEqual((self._notis('boss'), self._notis('jr'), self._notis('u2')), (['review'], ['created'], []))
        self.assertEqual([(w, detail.get_meeting_detail(actor_id=w, meeting_id=mid)['can_edit']) for w in ('u2', 'boss', 'jr')],
                         [('u2', False), ('boss', True), ('jr', False)])
        self.assertEqual([x['id'] for x in compose.list_review_queue(actor_id='boss')], [mid])
        with self.assertRaises(PermissionDenied):
            compose.confirm_meeting(actor_id='u2', meeting_id=mid)
        detail.save_summary(actor_id='boss', meeting_id=mid, content=CONTENT)
        self.assertEqual(compose.confirm_meeting(actor_id='boss', meeting_id=mid)['confirmed_by'], 'boss')

    def test_seniority_hook(self):
        """구성원 표가 생기면 compose.seniority() 가 직급을 알려 줌 → 신입은 검토자 필수 · 시니어는 검토 단계 없음."""
        from unittest.mock import patch
        from followup.adapters.postgres import compose
        from followup.adapters.postgres.common import ValidationError
        mid = self._draft()['id']
        with patch.object(compose, 'seniority', return_value='junior'):
            with self.assertRaisesRegex(ValidationError, '윗사람'):
                compose.submit_meeting(actor_id='jr', meeting_id=mid)
        with patch.object(compose, 'seniority', return_value='senior'):
            with self.assertRaisesRegex(ValidationError, '신입'):
                compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='boss')
            compose.submit_meeting(actor_id='jr', meeting_id=mid)

    def test_rls_draft_and_editor(self):
        from followup.adapters.postgres import compose

        def run(user, sql):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute(sql)
                return cur.rowcount if not cur.description else cur.fetchone()['n']

        mid = self._draft()['id']
        count = f"SELECT count(*) AS n FROM meetings WHERE id = '{mid}'"
        self.assertEqual((run('jr', count), run('boss', count), run('u2', count)), (1, 0, 0))   # draft
        compose.submit_meeting(actor_id='jr', meeting_id=mid, reviewer_id='boss')
        self.assertEqual((run('u2', count), run('u3', count)), (1, 0))
        upd = f"UPDATE meeting_todos SET urgent = TRUE WHERE meeting_id = '{mid}'"
        self.assertEqual((run('jr', upd), run('boss', upd)), (0, 1))                         # 신입 X · 검토자 O


if __name__ == '__main__':
    unittest.main()