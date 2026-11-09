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
            # 직급 (구성원 표 people · 다른 시험이 남긴 값과 섞이지 않게 여기서 정함)
            cur.execute("INSERT INTO teams (team_id, name) VALUES ('t-test', '시험팀') ON CONFLICT DO NOTHING")
            cur.execute("INSERT INTO roles (role_id, name, team_id) VALUES ('r-test', '시험', 't-test') ON CONFLICT DO NOTHING")
            cur.execute("""INSERT INTO people (user_id, name, position, team_id, role_id) VALUES
                       ('boss','윗사람','senior','t-test','r-test'), ('jr','신입','junior','t-test','r-test'),
                       ('u2','참석자','mid','t-test','r-test'), ('u3','관계없음','mid','t-test','r-test')
                       ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, position = EXCLUDED.position""")

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

    def _legacy_reviewer(self, mid, reviewer):
        """예전 방식(검토자를 두던 때)으로 만든 회의록 흉내: reviewer_id 를 직접 넣음."""
        with self.transaction() as cur:
            cur.execute("UPDATE meetings SET reviewer_id = %s WHERE id = %s", (reviewer, mid))

    def _handed(self):
        """신입(jr)이 쓰고 주관자로 윗사람(boss)을 고름 · 분석 끝 → 넘김. (회의 id, 프로젝트 id)"""
        from followup.adapters.postgres import compose, folders
        pid = folders.create_folder(actor_id='jr', name='출시', members=['u2'])['id']   # 주관자(boss)는 아직 멤버 아님
        mid = self._draft(host_id='boss', project_id=pid)['id']
        compose.handoff_meeting(actor_id='jr', meeting_id=mid, request={'title': '출시 회의', 'host_user_id': 'boss'},
                                draft={'nn_name': ''})
        return mid, pid

    def test_self_host_creates_and_confirms(self):
        """작성자 = 주관자 (시니어): 미리보기 → 생성 → 바로 확정 · 넘기기 없음."""
        from followup.adapters.postgres import compose, detail, minutes
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        from followup.adapters.postgres.meetings import list_status_history, update_meeting
        d = self._draft(actor_id='boss', attendees=['u2'])
        mid = d['id']
        self.assertEqual((d['status'], d['created_by'], d['host_user_id'], d['summary_version']), ('draft', 'boss', 'boss', 1))
        self.assertEqual(self._draft(actor_id='boss', meeting_id=mid, title='출시 회의 (수정)')['summary_version'], 2)
        self.assertEqual([x['id'] for x in compose.list_my_drafts(actor_id='boss')], [mid])
        with self.assertRaises(PermissionDenied):                                       # 미리보기는 작성자 · 주관자만
            detail.get_meeting_detail(actor_id='u2', meeting_id=mid)
        self.assertEqual(minutes.list_all_meetings(actor_id='u2'), [])
        with self.assertRaises(ValidationError):                                        # 생성은 전용 함수로만
            update_meeting(actor_id='boss', meeting_id=mid, status='review')
        with self.assertRaisesRegex(ValidationError, '내가 주관자'):                       # 내가 주관자면 넘기지 않음
            compose.handoff_meeting(actor_id='boss', meeting_id=mid, request={})
        with self.transaction() as cur:                                                 # 미리보기(요약) 없는 draft
            cur.execute("INSERT INTO meetings (id, host_user_id, status, created_by) VALUES ('empty', 'boss', 'draft', 'boss')")
        with self.assertRaisesRegex(ValidationError, '미리보기'):
            compose.submit_meeting(actor_id='boss', meeting_id='empty')
        with self.assertRaisesRegex(ValidationError, '검토자'):                           # 검토자 단계 없음
            compose.submit_meeting(actor_id='boss', meeting_id=mid, reviewer_id='u3')
        r = compose.submit_meeting(actor_id='boss', meeting_id=mid)
        self.assertIsNone(r['reviewer_id'])
        self.assertEqual((self._notis('u2'), self._notis('boss')), (['created'], []))   # 나한테는 알림 X
        self.assertEqual(compose.list_review_queue(actor_id='boss'), [])                 # 내 회의는 검토 대기에 없음
        with self.assertRaises(ValidationError):
            compose.submit_meeting(actor_id='boss', meeting_id=mid)
        self.assertTrue(detail.get_meeting_detail(actor_id='boss', meeting_id=mid)['can_edit'])
        with self.assertRaises(PermissionDenied):
            compose.confirm_meeting(actor_id='u2', meeting_id=mid)
        self.assertEqual(compose.confirm_meeting(actor_id='boss', meeting_id=mid)['status'], 'confirmed')
        self.assertEqual([(h['from_status'], h['to_status'], h['changed_by'])
                          for h in list_status_history(actor_id='u2', meeting_id=mid)],
                         [(None, 'draft', 'boss'), ('draft', 'review', 'boss'), ('review', 'confirmed', 'boss')])

    def test_handoff_to_host(self):
        """작성자(jr) ≠ 주관자(boss): 분석 끝 → 넘김 → 주관자에게 알림 · 검토 대기 → 주관자만 2단계부터 이어서 · 작성자는 손 못 댐."""
        from followup.adapters.postgres import compose, detail, folders, meetings, shares
        from followup.adapters.postgres.common import NotFoundError, PermissionDenied, ValidationError
        pid = folders.create_folder(actor_id='jr', name='출시', members=['u2'])['id']
        mid = self._draft(host_id='boss', project_id=pid)['id']
        self.assertEqual(compose.list_review_queue(actor_id='boss'), [])                  # 넘기기 전엔 대기 없음
        with self.assertRaisesRegex(PermissionDenied, '주관자만'):                          # 작성자가 바로 생성 X
            compose.submit_meeting(actor_id='jr', meeting_id=mid)
        with self.assertRaises(PermissionDenied):                                         # 쓴 사람만 넘김
            compose.handoff_meeting(actor_id='u2', meeting_id=mid, request={})
        with self.assertRaises(NotFoundError):                                            # 넘기기 전엔 열 수 없음
            compose.get_handoff(actor_id='boss', meeting_id=mid)
        compose.handoff_meeting(actor_id='jr', meeting_id=mid, request={'title': '출시 회의', 'host_user_id': 'boss'},
                                draft={'nn_name': ''})
        with self.assertRaisesRegex(ValidationError, '이미'):
            compose.handoff_meeting(actor_id='jr', meeting_id=mid, request={})
        self.assertEqual(self._notis('boss'), ['member', 'review'])                       # 프로젝트 멤버로 추가 + 검토 요청
        q = compose.list_review_queue(actor_id='boss')
        self.assertEqual([(x['id'], x['kind'], x['author_name']) for x in q], [(mid, 'handoff', '신입')])
        self.assertIn('boss', folders.list_members(actor_id='jr', folder_id=pid))          # 주관자는 프로젝트 멤버로

        # 작성자는 더 못 고침 (읽기는 됨)
        with self.assertRaisesRegex(PermissionDenied, '주관자'):
            self._draft(meeting_id=mid, host_id='boss', project_id=pid)
        with self.assertRaises(PermissionDenied):
            detail.save_summary(actor_id='jr', meeting_id=mid, content=CONTENT)
        self.assertFalse(detail.get_meeting_detail(actor_id='jr', meeting_id=mid)['can_edit'])
        with self.assertRaises(PermissionDenied):
            compose.get_handoff(actor_id='jr', meeting_id=mid)

        # 주관자: 넘겨받은 값으로 2단계 → 고쳐 저장 → 생성 → 확정
        h = compose.get_handoff(actor_id='boss', meeting_id=mid)
        self.assertEqual((h['author'], h['request']['title'], h['draft']), ('jr', '출시 회의', {'nn_name': ''}))
        self.assertTrue(detail.get_meeting_detail(actor_id='boss', meeting_id=mid)['can_edit'])
        self._draft(actor_id='boss', meeting_id=mid, host_id='boss', project_id=pid, title='출시 회의 (주관자 수정)')
        with self.assertRaises(PermissionDenied):
            compose.submit_meeting(actor_id='jr', meeting_id=mid)
        compose.submit_meeting(actor_id='boss', meeting_id=mid)
        self.assertEqual(compose.list_review_queue(actor_id='boss'), [])
        with self.assertRaisesRegex(ValidationError, '이미 생성'):
            compose.get_handoff(actor_id='boss', meeting_id=mid)
        self.assertEqual(self._notis('u2'), ['member', 'created'])                       # 프로젝트 멤버(만들 때) · 회의록 생성
        d = detail.get_meeting_detail(actor_id='jr', meeting_id=mid)
        self.assertEqual((d['title'], d['author'], d['can_edit']), ('출시 회의 (주관자 수정)', 'jr', False))
        with self.assertRaises(PermissionDenied):
            compose.confirm_meeting(actor_id='jr', meeting_id=mid)
        for call in (lambda: shares.create_share(actor_id='jr', meeting_id=mid, recipient_user_id='u3'),
                     lambda: meetings.delete_meeting(actor_id='jr', meeting_id=mid)):
            with self.assertRaises(PermissionDenied):                                     # 작성자는 공유 · 삭제 X
                call()
        self.assertEqual(compose.confirm_meeting(actor_id='boss', meeting_id=mid)['confirmed_by'], 'boss')
        s = shares.create_share(actor_id='boss', meeting_id=mid, recipient_user_id='u3')    # 주관자 공유 · 삭제 O
        shares.revoke_share(actor_id='boss', share_id=s['id'])
        meetings.delete_meeting(actor_id='boss', meeting_id=mid)
        with self.assertRaises(NotFoundError):
            detail.get_meeting_detail(actor_id='boss', meeting_id=mid)

    def test_junior_cannot_be_host(self):
        """주관자는 시니어 · 미드만: 신입이 자기를 주관자로 · 시니어가 신입을 주관자로 → 생성 · 넘기기 거절."""
        from followup.adapters.postgres import compose
        from followup.adapters.postgres.common import ValidationError
        for actor in ('jr', 'boss'):
            with self.subTest(actor=actor):
                mid = self._draft(actor_id=actor, host_id='jr', attendees=['u2'])['id']
                with self.assertRaisesRegex(ValidationError, '주관자는 시니어 · 미드만'):
                    compose.submit_meeting(actor_id=actor, meeting_id=mid)
        mid = self._draft(actor_id='boss', host_id='jr', attendees=['u2'])['id']
        with self.assertRaisesRegex(ValidationError, '주관자는 시니어 · 미드만'):
            compose.handoff_meeting(actor_id='boss', meeting_id=mid, request={})

    def test_seniority_hook(self):
        """compose.seniority() 가 직급을 알려 줌 → 신입은 주관자 X · 직급을 모르면 막지 않음."""
        from unittest.mock import patch
        from followup.adapters.postgres import compose
        from followup.adapters.postgres.common import ValidationError
        mid = self._draft(actor_id='boss')['id']
        with patch.object(compose, 'seniority', return_value='junior'):
            with self.assertRaisesRegex(ValidationError, '주관자는'):
                compose.submit_meeting(actor_id='boss', meeting_id=mid)
        with patch.object(compose, 'seniority', return_value=None):
            compose.submit_meeting(actor_id='boss', meeting_id=mid)

    def test_rls_draft_handoff_and_editor(self):
        from followup.adapters.postgres import compose, folders

        def run(user, sql):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute(sql)
                return cur.rowcount if not cur.description else cur.fetchone()['n']

        pid = folders.create_folder(actor_id='jr', name='출시', members=['u2'])['id']
        mid = self._draft(host_id='boss', project_id=pid)['id']
        count = f"SELECT count(*) AS n FROM meetings WHERE id = '{mid}'"
        upd = f"UPDATE meeting_todos SET urgent = TRUE WHERE meeting_id = '{mid}'"
        self.assertEqual((run('jr', count), run('boss', count), run('u2', count)), (1, 1, 0))   # draft: 작성자 · 주관자
        self.assertEqual((run('jr', upd), run('u2', upd)), (1, 0))                              # 넘기기 전: 작성자 O
        compose.handoff_meeting(actor_id='jr', meeting_id=mid, request={})
        self.assertEqual((run('jr', upd), run('boss', upd)), (0, 1))                            # 넘긴 뒤: 주관자만
        compose.submit_meeting(actor_id='boss', meeting_id=mid)
        self.assertEqual((run('u2', count), run('u3', count)), (1, 0))
        self.assertEqual((run('jr', upd), run('boss', upd), run('u2', upd)), (0, 1, 0))         # 생성 뒤: 주관자만
        self._legacy_reviewer(mid, 'u3')                                                      # 예전 검토자는 고치기 O
        self.assertEqual(run('u3', upd), 1)

    def test_delete_and_share_host_only(self):
        """022: 삭제 · 공유는 주관자만. 작성자 · (예전 방식의) 검토자 · 참석자 · 남은 못 함 (앱 + RLS)."""
        from followup.adapters.postgres import compose, detail, meetings, shares
        from followup.adapters.postgres.common import NotFoundError, PermissionDenied

        def run(user, sql):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute(sql)
                return cur.rowcount if not cur.description else cur.fetchone()['n']

        mid, _ = self._handed()
        compose.submit_meeting(actor_id='boss', meeting_id=mid)
        self._legacy_reviewer(mid, 'u3')
        for who in ('jr', 'u3', 'u2'):                                                     # 작성자 · 검토자 · 참석자 X
            with self.assertRaises(PermissionDenied):
                shares.create_share(actor_id=who, meeting_id=mid, recipient_user_id='u2')
            with self.assertRaises(PermissionDenied):
                meetings.delete_meeting(actor_id=who, meeting_id=mid)
        a = shares.create_share(actor_id='boss', meeting_id=mid, recipient_user_id='u2')     # 주관자 O
        with self.assertRaises(PermissionDenied):
            shares.revoke_share(actor_id='jr', share_id=a['id'])
        shares.revoke_share(actor_id='boss', share_id=a['id'])

        ins = ("WITH x AS (INSERT INTO meeting_shares (meeting_id,sender_id,recipient_user_id,scope) "
               f"VALUES ('{mid}', '{{u}}', 'u2', 'summary') RETURNING 1) SELECT count(*) AS n FROM x")
        for who in ('jr', 'u3', 'u2'):
            with self.assertRaises(Exception):
                run(who, ins.format(u=who))
        self.assertEqual(run('boss', ins.format(u='boss')), 1)
        delete = f"SELECT count(*) AS n FROM remeet_internal.delete_meeting('{mid}')"
        self.assertEqual((run('jr', delete), run('u3', delete), run('u2', delete)), (0, 0, 0))
        self.assertEqual(run('boss', delete), 1)
        with self.assertRaises(NotFoundError):
            detail.get_meeting_detail(actor_id='boss', meeting_id=mid)

if __name__ == '__main__':
    unittest.main()