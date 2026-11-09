"""결과 전달 상태 · 전송 기록 · 회의 삭제 · 신입 검토자 규칙 (routes/meeting_records · integrations) 을 실제 DB 로.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다. backend 폴더에서:
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_delivery_api_pg.py -v
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
SECRET = 'test-secret'

CONTENT = {'summary': {'text': 'PG 수수료 2.5% 로 결정', 'src': ['1']},
           'decisions': [{'text': '수수료 2.5% 확정', 'src': ['1']}]}
TRANSCRIPT = [{'line_id': '1', 'speaker': '도윤', 'text': 'PG 수수료는 2.5%예요'},
              {'line_id': '2', 'speaker': '서윤', 'text': '정산은 D+1'}]


def auth(user_id: str, permission: str = 'member') -> dict:
    from followup.domain.tokens import make_token
    token = make_token({'user_id': user_id, 'name': user_id, 'email': f'{user_id}@t.local', 'permission': permission},
                       SECRET, 600)
    return {'Authorization': f'Bearer {token}'}


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class DeliveryAndReviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        if not conninfo_to_dict(TEST_URL).get('dbname', '').endswith('_test'):
            raise RuntimeError('_test DB 만 허용')
        os.environ['DATABASE_URL'] = TEST_URL
        from followup import settings
        settings.AUTH_SECRET = SECRET
        from followup.adapters.postgres import migrate
        from followup.adapters.postgres.db import close_pool, transaction
        close_pool()
        cls.transaction = staticmethod(transaction)
        with transaction() as cur:
            cur.execute((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        with redirect_stdout(io.StringIO()):
            migrate.main()
        from fastapi.testclient import TestClient
        from followup.entrypoints.api.main import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        from followup.adapters.postgres.db import close_pool
        close_pool()

    def setUp(self):
        with self.transaction() as cur:
            cur.execute('TRUNCATE notifications, failed_exports, meetings, folders, person_skills, people, roles, teams, '
                        'accounts CASCADE')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@t.local','USER'), ('u2','u2@t.local','USER'), ('u3','u3@t.local','USER'),
                       ('u4','u4@t.local','USER'), ('ad','ad@t.local','ADMIN')""")
            cur.execute("UPDATE integration_status SET status = 'on'")
            cur.execute("INSERT INTO teams (team_id,name) VALUES ('t-pm','기획팀')")
            cur.execute("INSERT INTO roles (role_id,name,team_id) VALUES ('r-pm','PM','t-pm')")
            cur.execute("""INSERT INTO people (user_id,name,position,team_id,role_id) VALUES
                       ('u1','김팀장','senior','t-pm','r-pm'), ('u2','이신입','junior','t-pm','r-pm'),
                       ('u3','강수현','mid','t-pm','r-pm'), ('u4','박신입','junior','t-pm','r-pm')""")
        self.U1, self.U2, self.U3, self.U4, self.AD = auth('u1'), auth('u2'), auth('u3'), auth('u4'), auth('ad', 'admin')

    def call(self, method, url, headers, json=None, status=200):
        r = self.client.request(method, url, headers=headers, json=json)
        self.assertEqual(r.status_code, status, r.text)
        return r.json() if r.content else None

    def _project(self, members=('u2',)):
        return self.call('POST', '/api/projects', self.U1, {'name': '결제 개편', 'members': list(members)}, 201)['id']

    def _draft(self, pid, **extra):
        return self.call('POST', '/api/drafts', self.U1, self._body(pid, **extra), 201)['id']

    @staticmethod
    def _body(pid, **extra):
        return {'content': CONTENT, 'title': '주간 회의', 'directory_id': 'weekly', 'project_id': pid,
                'held_at': '2026-10-27T10:00:00+09:00', 'attendees': ['u2'], 'transcript': TRANSCRIPT,
                'todos': [{'text': '수수료 비교표 정리', 'owners': ['u2'], 'due': '11/5', 'src': ['1']}], **extra}

    def _confirmed(self):
        pid = self._project()
        mid = self._draft(pid)
        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        self.call('POST', f'/api/meetings/{mid}/confirm', self.U1)
        return mid

    def test_status_flow_and_resend(self):
        mid = self._confirmed()
        self.call('PUT', f'/api/meetings/{mid}/status', self.U2, {'status': 'sending'}, status=403)   # 참석자는 못 함
        self.call('PUT', f'/api/meetings/{mid}/status', self.U1, {'status': 'done'}, status=409)      # confirmed → done X
        self.call('PUT', f'/api/meetings/{mid}/status', self.U1, {'status': 'review'}, status=422)
        for st in ('sending', 'done', 'sending', 'done', 'fail', 'done'):                           # 016: done → sending · fail
            self.assertEqual(self.call('PUT', f'/api/meetings/{mid}/status', self.U1, {'status': st})['status'], st)
        h = [x['to_status'] for x in self.call('GET', f'/api/meetings/{mid}/status-history', self.U1)]
        self.assertEqual(h[-3:], ['done', 'fail', 'done'])

    def test_failures_exports_finish(self):
        mid = self._confirmed()
        self.call('PUT', f'/api/meetings/{mid}/status', self.U1, {'status': 'sending'})
        self.call('POST', '/api/integrations/exports', self.U1, {'meeting_id': mid, 'tool_id': 'mail'}, 204)
        tools = {t['id']: t for t in self.call('GET', '/api/integrations/tools', self.U1)}
        self.assertIsNotNone(tools['mail']['last_sync_at'])
        f = self.call('POST', '/api/integrations/failures', self.U1,
                      {'meeting_id': mid, 'tool_id': 'jira', 'kind': 'tech', 'reason': 'Jira 연결 실패', 'code': '401'}, 201)
        self.call('POST', '/api/integrations/failures', self.U2,
                  {'meeting_id': mid, 'tool_id': 'jira', 'kind': 'simple', 'reason': 'x'}, status=403)
        self.assertIn('fail', [n['kind'] for n in self.call('GET', '/api/notifications', self.AD)])   # tech → 관리자 알림
        self.assertEqual(self.call('POST', f'/api/meetings/{mid}/delivery/finish', self.U1)['status'], 'fail')
        self.call('POST', f"/api/integrations/failures/{f['id']}/resolve", self.AD)                  # 관리자 해결 → done
        d = self.call('GET', f'/api/meetings/{mid}', self.U1)
        self.assertEqual(d['initial_status'], 'done')
        self.assertIn('delivered', [n['kind'] for n in self.call('GET', '/api/notifications', self.U1)])

    def test_delete_meeting(self):
        mid = self._confirmed()
        self.call('DELETE', f'/api/meetings/{mid}', self.U2, status=403)
        self.call('DELETE', f'/api/meetings/{mid}', self.U1, status=204)
        self.call('GET', f'/api/meetings/{mid}', self.U1, status=404)
        self.assertNotIn(mid, [m['id'] for m in self.call('GET', '/api/meetings', self.U1)])

    def test_junior_writes_handoff_to_senior_host(self):
        """신입(u2)이 씀 → 주관자는 시니어 · 미드만 (자기 · 신입 u4 X) → 분석 끝 → 넘김(API) → 주관자(u3)만 이어서 (022)."""
        pid = self.call('POST', '/api/projects', self.U2, {'name': '신입 회의', 'members': ['u4']}, 201)['id']
        body = {**self._body(pid), 'attendees': ['u4']}
        mid = self.call('POST', '/api/drafts', self.U2, body, 201)['id']
        self.call('POST', f'/api/meetings/{mid}/submit', self.U2, {}, status=400)                     # 신입 본인이 주관자
        self.call('POST', '/api/drafts', self.U2, {**body, 'meeting_id': mid, 'host_id': 'u4'}, 201)
        self.call('POST', f'/api/meetings/{mid}/handoff', self.U2, {'request': {}}, status=400)       # 신입 u4 도 주관자 X
        self.call('POST', '/api/drafts', self.U2, {**body, 'meeting_id': mid, 'host_id': 'u3'}, 201)
        r = self.call('POST', f'/api/meetings/{mid}/handoff', self.U2, {'request': {'title': '주간 회의'}, 'draft': {'nn_name': ''}})
        self.assertEqual(r['host_user_id'], 'u3')
        self.assertIn('review', [n['kind'] for n in self.call('GET', '/api/notifications', self.U3)])
        self.assertEqual([(x['id'], x['kind']) for x in self.call('GET', '/api/review-queue', self.U3)], [(mid, 'handoff')])
        self.call('GET', f'/api/meetings/{mid}/handoff', self.U2, status=403)                         # 작성자는 못 엶
        h = self.call('GET', f'/api/meetings/{mid}/handoff', self.U3)
        self.assertEqual((h['author'], h['request']['title']), ('u2', '주간 회의'))
        self.assertFalse(self.call('GET', f'/api/meetings/{mid}', self.U2)['can_edit'])                # 작성자는 손 못 댐
        self.call('POST', '/api/drafts', self.U2, {**body, 'meeting_id': mid, 'host_id': 'u3'}, status=403)
        self.call('POST', '/api/drafts', self.U3, {**body, 'meeting_id': mid, 'host_id': 'u3'}, 201)   # 주관자는 고쳐 저장
        self.call('POST', f'/api/meetings/{mid}/submit', self.U3, {'reviewer_id': 'u1'}, status=400)  # 검토자 지정 X
        self.call('POST', f'/api/meetings/{mid}/submit', self.U2, {}, status=403)
        r = self.call('POST', f'/api/meetings/{mid}/submit', self.U3, {})
        self.assertEqual((r['host_user_id'], r['reviewer_id']), ('u3', None))
        self.call('POST', f'/api/meetings/{mid}/confirm', self.U2, status=403)                         # 작성자 X
        self.call('POST', f'/api/meetings/{mid}/confirm', self.U3)                                     # 주관자 확정 O
        self.call('DELETE', f'/api/meetings/{mid}', self.U2, status=403)

if __name__ == '__main__':
    unittest.main()
