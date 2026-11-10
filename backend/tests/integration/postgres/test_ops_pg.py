"""외부 전송 실패 · 연동 상태 · 폴더 브리프 수정 · 회의 상태 변경 이력 (migrations/007).

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_ops_pg.py -v   (backend 폴더에서)
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
class OpsIntegrationTests(unittest.TestCase):
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
            cur.execute('''TRUNCATE notifications, failed_exports, meeting_exports, integration_status, integration_tools,
                        meeting_status_history, meeting_shares, meetings, folders, accounts CASCADE''')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@test.local','USER'), ('u2','u2@test.local','USER'),
                       ('u3','u3@test.local','USER'), ('ad','ad@test.local','ADMIN')""")
            cur.execute("""INSERT INTO integration_tools (id,mark,name,description,is_phase2,sort_order) VALUES
                       ('slack','S','Slack','',FALSE,0), ('jira','J','Jira','',FALSE,1), ('teams','T','Teams','',TRUE,2)""")
            cur.execute("INSERT INTO integration_status (tool_id,status) VALUES ('slack','on'), ('jira','expired')")

    def _meeting(self, mid='m1', project_id=None):
        from followup.adapters.postgres.meetings import create_meeting
        return create_meeting(actor_id='u1', meeting_id=mid, project_id=project_id, title=f'{mid} 회의')

    def _notis(self, user):
        with self.transaction() as cur:
            cur.execute('SELECT kind, meeting_id FROM notifications WHERE user_id=%s ORDER BY created_at', (user,))
            return [(r['kind'], r['meeting_id']) for r in cur.fetchall()]

    # ------------------------------------------------------------ 상태 변경 이력
    def test_status_history_records_who_and_when(self):
        from followup.adapters.postgres import meetings
        from followup.adapters.postgres.common import PermissionDenied
        self._meeting()
        for st in ('confirmed', 'sending', 'fail', 'done'):     # fail → done 은 007 에서 허용
            meetings.update_meeting(actor_id='u1', meeting_id='m1', status=st)
        h = meetings.list_status_history(actor_id='u1', meeting_id='m1')
        self.assertEqual([(x['from_status'], x['to_status'], x['changed_by']) for x in h],
                         [(None, 'review', 'u1'), ('review', 'confirmed', 'u1'), ('confirmed', 'sending', 'u1'),
                          ('sending', 'fail', 'u1'), ('fail', 'done', 'u1')])
        meetings.update_meeting(actor_id='u1', meeting_id='m1', title='제목만 바꿈')   # 상태 안 바뀌면 기록 없음
        self.assertEqual(len(meetings.list_status_history(actor_id='u1', meeting_id='m1')), 5)
        with self.assertRaises(PermissionDenied):
            meetings.list_status_history(actor_id='u3', meeting_id='m1')

    # ------------------------------------------------------------ 폴더 브리프
    def test_folder_briefs(self):
        from followup.adapters.postgres import folders
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        f = folders.create_folder(actor_id='u1', name='프로젝트', members=['u2'])
        items = [{'label': '목표', 'text': '11월 출시'}, {'label': '범위', 'text': '결제'}]
        folders.set_briefs(actor_id='u1', folder_id=f['id'], items=items)
        self.assertEqual(folders.list_briefs(actor_id='u2', folder_id=f['id']), items)
        folders.set_briefs(actor_id='u1', folder_id=f['id'], items=items[1:])
        self.assertEqual(folders.list_briefs(actor_id='u1', folder_id=f['id']), items[1:])
        with self.assertRaises(PermissionDenied):
            folders.set_briefs(actor_id='u2', folder_id=f['id'], items=[])
        with self.assertRaises(PermissionDenied):
            folders.list_briefs(actor_id='u3', folder_id=f['id'])
        with self.assertRaises(ValidationError):
            folders.set_briefs(actor_id='u1', folder_id=f['id'], items=[{'label': '', 'text': 'x'}])

    # ------------------------------------------------------------ 외부 연동 · 전송 실패
    def test_tool_status_admin_only(self):
        from followup.adapters.postgres import integrations as ig
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        with self.assertRaises(PermissionDenied):
            ig.set_tool_status(actor_id='u1', tool_id='slack', status='off')
        self.assertEqual(ig.set_tool_status(actor_id='ad', tool_id='jira', status='on')['updated_by'], 'ad')
        with self.assertRaises(ValidationError):
            ig.set_tool_status(actor_id='ad', tool_id='teams', status='on')      # 2단계 도구
        tools = {t['id']: t for t in ig.list_tools(actor_id='u1')}
        self.assertEqual({k: t['status'] for k, t in tools.items()}, {'slack': 'on', 'jira': 'on', 'teams': 'off'})
        self.assertIsNone(tools['slack']['last_sync_at'])
        self._meeting()
        ig.record_export(actor_id='u1', meeting_id='m1', tool_id='slack')
        self.assertIsNotNone({t['id']: t for t in ig.list_tools(actor_id='u1')}['slack']['last_sync_at'])

    def test_export_marks_meeting_exported(self):
        """외부 도구 전송 성공 · 다시 보내서 해결 → 회의 상세 exported_at (migrations/024)."""
        from followup.adapters.postgres import integrations as ig
        from followup.adapters.postgres.detail import get_meeting_detail
        self._meeting('m1')
        self._meeting('m2')
        self.assertIsNone(get_meeting_detail(actor_id='u1', meeting_id='m1')['exported_at'])   # 결과 전달만 함
        ig.record_export(actor_id='u1', meeting_id='m1', tool_id='slack')
        first = get_meeting_detail(actor_id='u1', meeting_id='m1')['exported_at']
        self.assertIsNotNone(first)
        ig.record_export(actor_id='u1', meeting_id='m1', tool_id='slack')                    # 다시 보내도 한 행
        with self.transaction() as cur:
            cur.execute("SELECT count(*) AS n FROM meeting_exports WHERE meeting_id='m1'")
            self.assertEqual(cur.fetchone()['n'], 1)
        self.assertIsNone(get_meeting_detail(actor_id='u1', meeting_id='m2')['exported_at'])   # 다른 회의는 그대로
        f = ig.record_failure(actor_id='u1', meeting_id='m2', tool_id='slack', kind='simple', reason='오류')
        self.assertIsNone(get_meeting_detail(actor_id='u1', meeting_id='m2')['exported_at'])   # 실패는 보낸 게 아님
        ig.resolve_failure(actor_id='u1', failure_id=f['id'])
        self.assertIsNotNone(get_meeting_detail(actor_id='u1', meeting_id='m2')['exported_at'])

    def test_failures_resend_flow(self):
        from followup.adapters.postgres import integrations as ig
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        from followup.adapters.postgres.meetings import update_meeting
        self._meeting()
        for st in ('confirmed', 'sending'):
            update_meeting(actor_id='u1', meeting_id='m1', status=st)
        simple = ig.record_failure(actor_id='u1', meeting_id='m1', tool_id='slack', kind='simple',
                                   reason='일시적인 네트워크 오류', code='timeout (30초)')
        tech = ig.record_failure(actor_id='u1', meeting_id='m1', tool_id='jira', kind='tech',
                                 reason='Jira 인증이 만료됐어요.', code='401 Unauthorized')
        self.assertEqual(self._notis('ad'), [('fail', 'm1')])                     # tech 만 관리자에게
        with self.assertRaises(PermissionDenied):
            ig.record_failure(actor_id='u2', meeting_id='m1', tool_id='slack', kind='simple', reason='x')
        self.assertEqual(ig.finish_delivery(actor_id='u1', meeting_id='m1'), 'fail')

        # 목록: 관리자는 전부, 사용자는 회의 단위로만
        self.assertEqual(len(ig.list_failures(actor_id='ad')), 2)
        with self.assertRaises(PermissionDenied):
            ig.list_failures(actor_id='u1')
        self.assertEqual({f['kind'] for f in ig.list_failures(actor_id='u1', meeting_id='m1', open_only=True)},
                         {'simple', 'tech'})

        # simple: 주관자가 해결 · tech: 주관자는 못 함
        with self.assertRaises(PermissionDenied):
            ig.resolve_failure(actor_id='u1', failure_id=tech['id'])
        with self.assertRaises(PermissionDenied):
            ig.resolve_failure(actor_id='u2', failure_id=simple['id'])
        ig.resolve_failure(actor_id='u1', failure_id=simple['id'])
        with self.assertRaises(ValidationError):
            ig.resolve_failure(actor_id='u1', failure_id=simple['id'])
        with self.transaction() as cur:
            cur.execute("SELECT status FROM meetings WHERE id='m1'")
            self.assertEqual(cur.fetchone()['status'], 'fail')                   # tech 가 아직 남음
        # 관리자가 tech 해결 → 남은 실패 없음 → done + 주관자에게 전달 완료 알림
        ig.resolve_failure(actor_id='ad', failure_id=tech['id'])
        with self.transaction() as cur:
            cur.execute("SELECT status FROM meetings WHERE id='m1'")
            self.assertEqual(cur.fetchone()['status'], 'done')
        self.assertIn(('delivered', 'm1'), self._notis('u1'))
        self.assertEqual(ig.list_failures(actor_id='ad', open_only=True), [])

    def test_my_todo_ids_for_dashboard(self):
        """홈 대시보드: 내가 담당자(공동 포함)인 할 일 id · 지운 할 일은 뺌 (표는 읽기만)."""
        from followup.adapters.postgres import detail
        self._meeting()
        a = detail.add_todo(actor_id='u1', meeting_id='m1', text='견적 비교', owners=['u2', 'u3'])
        b = detail.add_todo(actor_id='u1', meeting_id='m1', text='디자인 검토', owners=['u3'])
        detail.delete_todo(actor_id='u1', todo_id=b['id'])
        self.assertEqual(detail.my_todo_ids('u3', 10), [a['id']])                # 공동 담당자 · 지운 일은 뺌
        self.assertEqual(detail.my_todo_ids('u2', 10), [a['id']])
        self.assertEqual(detail.my_todo_ids('u1', 10), [])                       # 주관자라도 담당자가 아니면 없음

    def test_preferences_saved_per_user(self):
        """026: 즐겨찾기 · 최근을 사람마다 저장 (다시 로그인해도 남음) · 허용 키 · 개수 · 중복 정리."""
        from followup.adapters.postgres import preferences as pref
        from followup.adapters.postgres.common import ValidationError
        self.assertEqual(pref.get_preferences(actor_id='u1'), {})
        self.assertEqual(pref.set_preference(actor_id='u1', key='people_fav', value=['u2', 'u3', 'u2']), ['u2', 'u3'])
        pref.set_preference(actor_id='u1', key='team_recent', value=['서비스기획팀'])
        pref.set_preference(actor_id='u1', key='people_fav', value=['u3'])  # 통째로 바꿈
        self.assertEqual(pref.get_preferences(actor_id='u1'), {'people_fav': ['u3'], 'team_recent': ['서비스기획팀']})
        self.assertEqual(pref.get_preferences(actor_id='u2'), {})  # 남의 설정은 안 보임
        pref.set_preference(actor_id='ad', key='team_fav', value=['프로덕트부'])  # 관리자도 (구성원 관리의 팀 즐겨찾기)
        for key, value in (('theme', ['dark']), ('people_recent', [f'u{i}' for i in range(9)]), ('team_fav', [''])):
            with self.assertRaises(ValidationError):
                pref.set_preference(actor_id='u1', key=key, value=value)

    def test_failed_keys_and_reconnect_hands_tech_to_host(self):
        """022: 실패한 것(key) 저장 · None = 모름 · 다시 연결하면 남은 tech → simple + 주관자에게 알림."""
        from followup.adapters.postgres import integrations as ig
        from followup.adapters.postgres.common import ValidationError
        self._meeting()
        part = ig.record_failure(actor_id='u1', meeting_id='m1', tool_id='slack', kind='simple',
                                 reason='2건을 보내지 못했어요', keys=['41', '42'])
        self.assertEqual(part['failed_keys'], ['41', '42'])
        tech = ig.record_failure(actor_id='u1', meeting_id='m1', tool_id='jira', kind='tech', reason='인증 만료')
        self.assertIsNone(tech['failed_keys'])                                   # 아무것도 못 보냄 → 다시 보낼 때 전부
        with self.assertRaises(ValidationError):                                 # 빈 목록은 「없음」·「모름」이 섞임
            ig.record_failure(actor_id='u1', meeting_id='m1', tool_id='slack', kind='simple', reason='x', keys=[])
        ig.set_tool_status(actor_id='ad', tool_id='jira', status='on')           # 관리자가 다시 연결
        kinds = {f['id']: f['kind'] for f in ig.list_failures(actor_id='u1', meeting_id='m1')}
        self.assertEqual((kinds[tech['id']], kinds[part['id']]), ('simple', 'simple'))
        self.assertIn(('integration', 'm1'), self._notis('u1'))                  # 주관자에게 「다시 보내기」 안내
        ig.resolve_failure(actor_id='u1', failure_id=tech['id'])                 # 이제 주관자가 다시 보내 해결


if __name__ == '__main__':
    unittest.main()