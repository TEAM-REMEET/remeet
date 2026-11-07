"""PostgreSQL API (routes/projects · shares · notifications · search · integrations) 를 실제 DB 로.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다. backend 폴더에서:
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_api_pg.py -v
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')
ROOT = Path(__file__).resolve().parents[3] / 'src' / 'followup' / 'adapters' / 'postgres'  # schema.sql · migrations/
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))
SECRET = 'test-secret'


def auth(user_id: str, permission: str = 'member') -> dict:
    from followup.domain.tokens import make_token
    token = make_token({'user_id': user_id, 'name': user_id, 'email': f'{user_id}@t.local', 'permission': permission},
                       SECRET, 600)
    return {'Authorization': f'Bearer {token}'}


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class ApiTests(unittest.TestCase):
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
            cur.execute('TRUNCATE notifications, failed_exports, meeting_shares, meetings, folders, accounts CASCADE')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@t.local','USER'), ('u2','u2@t.local','USER'), ('u3','u3@t.local','USER'),
                       ('ad','ad@t.local','ADMIN')""")
            cur.execute("UPDATE integration_status SET status='off'")
        self.U1, self.U2, self.U3, self.AD = auth('u1'), auth('u2'), auth('u3'), auth('ad', 'admin')

    def call(self, method, url, headers, json=None, status=200):
        r = self.client.request(method, url, headers=headers, json=json)
        self.assertEqual(r.status_code, status, r.text)
        return r.json() if r.content else None

    def _meeting(self, project_id, title='주간 회의', attendees=('u2',)):
        from followup.adapters.postgres import compose
        m = compose.save_draft(actor_id='u1', content={'summary': {'text': f'{title} 요약', 'src': []}},
                               title=title, project_id=project_id, attendees=list(attendees),
                               transcript=[{'line_id': '1', 'speaker': '도윤', 'text': 'PG 수수료는 2.5%'}])
        compose.submit_meeting(actor_id='u1', meeting_id=m['id'])
        return m['id']

    def test_login_required(self):
        for url in ('/api/projects', '/api/meetings', '/api/search?q=x', '/api/notifications',
                    '/api/shares/sent', '/api/integrations/tools', '/api/folders/tree'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_projects_briefs_members_and_lists(self):
        p = self.call('POST', '/api/projects', self.U1, {'name': '결제 개편', 'members': ['u2']}, 201)
        pid = p['id']
        self.call('POST', '/api/projects', self.U1, {'name': ''}, status=422)
        self.assertEqual([x['id'] for x in self.call('GET', '/api/projects', self.U2)], [pid])
        self.call('GET', f'/api/projects/{pid}', self.U3, status=403)                 # 멤버 아님
        self.call('GET', '/api/projects/없음', self.U1, status=404)
        self.call('PATCH', f'/api/projects/{pid}', self.U2, {'name': '바꿈'}, status=403)  # 주관자만
        self.assertEqual(self.call('PATCH', f'/api/projects/{pid}', self.U1, {'name': '결제 시스템 개편'})['name'],
                         '결제 시스템 개편')

        briefs = [{'label': '목표', 'text': '11월 출시'}]
        self.assertEqual(self.call('PUT', f'/api/projects/{pid}/briefs', self.U1, briefs), briefs)
        self.assertEqual(self.call('GET', f'/api/projects/{pid}/briefs', self.U2), briefs)
        self.call('PUT', f'/api/projects/{pid}/briefs', self.U2, briefs, status=403)

        self.call('POST', f'/api/projects/{pid}/members', self.U1, {'user_id': 'u3'}, 204)
        self.call('GET', f'/api/projects/{pid}', self.U3)
        self.call('POST', f'/api/projects/{pid}/members', self.U1, {'user_id': 'nobody'}, status=409)  # 012 외래 키
        self.call('DELETE', f'/api/projects/{pid}/members/u3', self.U1, status=204)
        self.call('GET', f'/api/projects/{pid}', self.U3, status=403)

        mid = self._meeting(pid)
        self.assertEqual([m['id'] for m in self.call('GET', '/api/meetings', self.U2)], [mid])
        self.assertEqual([m['id'] for m in self.call('GET', f'/api/projects/{pid}/meetings', self.U1)], [mid])
        self.assertEqual(self.call('GET', '/api/meetings', self.U3), [])                # 참석 · 공유 X
        tree = self.call('GET', '/api/folders/tree', self.U1)
        self.assertEqual(set(tree), {'regular', 'projects'})
        self.call('GET', '/api/meetings?limit=0', self.U1, status=422)
        h = self.call('GET', f'/api/meetings/{mid}/status-history', self.U1)
        self.assertEqual([x['to_status'] for x in h][-1], 'review')                     # 회의록 생성 → 검토 중
        self.call('GET', f'/api/meetings/{mid}/status-history', self.U3, status=403)

    def test_admin_cannot_use_meeting_features(self):
        self.call('GET', '/api/projects', self.AD, status=403)

    def test_shares_and_notifications(self):
        pid = self.call('POST', '/api/projects', self.U1, {'name': '정산'}, 201)['id']
        mid = self._meeting(pid, attendees=())
        self.call('POST', f'/api/meetings/{mid}/shares', self.U1, {}, status=400)          # 받는 사람 없음
        self.call('POST', f'/api/meetings/{mid}/shares', self.U1, {'recipient_user_id': 'u3', 'scope': 'all'}, 422)
        sh = self.call('POST', f'/api/meetings/{mid}/shares', self.U1, {'recipient_user_id': 'u3'}, 201)
        self.call('POST', f'/api/meetings/{mid}/shares', self.U3, {'recipient_user_id': 'u2'}, status=403)
        self.assertEqual([x['meeting_id'] for x in self.call('GET', '/api/shares/received', self.U3)], [mid])
        self.assertEqual([x['id'] for x in self.call('GET', '/api/shares/sent', self.U1)], [sh['id']])
        self.assertEqual(self.call('PATCH', f"/api/shares/{sh['id']}", self.U1,
                                   {'scope': 'full', 'lock_raw': True})['scope'], 'full')
        self.call('DELETE', f"/api/shares/{sh['id']}", self.U1)
        self.assertEqual(self.call('GET', '/api/shares/received', self.U3), [])

        notis = self.call('GET', '/api/notifications', self.U3)                        # 공유 알림
        self.assertTrue(notis)
        nid = notis[0]['id']
        self.call('PATCH', f'/api/notifications/{nid}', self.U2, {'read': True}, status=403)  # 남의 알림
        self.assertTrue(self.call('PATCH', f'/api/notifications/{nid}', self.U3, {'read': True})['is_read'])
        self.assertNotIn(nid, [n['id'] for n in self.call('GET', '/api/notifications?unread_only=true', self.U3)])
        self.call('DELETE', f'/api/notifications/{nid}', self.U3, status=204)

    def test_search(self):
        pid = self.call('POST', '/api/projects', self.U1, {'name': 'PG 연동'}, 201)['id']
        mid = self._meeting(pid, title='PG사 선정')
        res = self.call('GET', '/api/search?q=pg', self.U1)
        self.assertEqual(res['counts']['title'], 1)
        self.assertEqual(res['title'][0]['id'], mid)
        self.assertEqual(res['counts']['folder'], 1)
        self.call('GET', '/api/search?q=', self.U1, status=422)
        self.call('GET', '/api/search?q=%20%20', self.U1, status=400)                     # 공백만

    def test_integrations(self):
        tools = {t['id']: t for t in self.call('GET', '/api/integrations/tools', self.U1)}
        self.assertEqual(tools['slack']['status'], 'off')
        self.call('PUT', '/api/integrations/tools/slack/status', self.U1, {'status': 'on'}, status=403)
        self.assertEqual(self.call('PUT', '/api/integrations/tools/slack/status', self.AD, {'status': 'on'})['status'],
                         'on')
        self.call('PUT', '/api/integrations/tools/slack/status', self.AD, {'status': 'broken'}, status=422)

        from followup.adapters.postgres import compose, integrations, meetings
        pid = self.call('POST', '/api/projects', self.U1, {'name': '전송'}, 201)['id']
        mid = self._meeting(pid)
        compose.confirm_meeting(actor_id='u1', meeting_id=mid)
        meetings.update_meeting(actor_id='u1', meeting_id=mid, status='sending')
        f = integrations.record_failure(actor_id='u1', meeting_id=mid, tool_id='slack', kind='simple', reason='timeout')
        self.call('GET', '/api/integrations/failures', self.U1, status=403)              # 전체는 관리자만
        self.assertEqual(len(self.call('GET', '/api/integrations/failures', self.AD)), 1)
        self.assertEqual(len(self.call('GET', f'/api/integrations/failures?meeting_id={mid}', self.U1)), 1)
        self.call('POST', f"/api/integrations/failures/{f['id']}/resolve", self.U2, status=403)
        self.call('POST', f"/api/integrations/failures/{f['id']}/resolve", self.U1)
        self.call('POST', f"/api/integrations/failures/{f['id']}/resolve", self.U1, status=400)  # 이미 해결
        self.call('POST', '/api/integrations/failures/없음/resolve', self.AD, status=404)

    def test_people_api_syncs_to_postgres(self):
        """POST /api/people: Neo4j(가짜) 저장 뒤 PostgreSQL accounts · people 에도 들어감."""
        from followup.adapters.neo4j import people as neo4j_people
        from followup.adapters.postgres.db import query
        with self.transaction() as cur:
            cur.execute("INSERT INTO teams (team_id,name) VALUES ('t-x','테스트팀') ON CONFLICT DO NOTHING")
        person = {'user_id': 'u77', 'name': '새사람', 'email': 'u77@t.local', 'permission': 'member',
                  'position': 'junior', 'is_active': True, 'team_id': 't-x', 'team': '테스트팀', 'role_id': 'r-x77',
                  'job': '테스터', 'skills': []}
        body = {'name': '새사람', 'email': 'u77@t.local', 'team_id': 't-x', 'job': '테스터', 'position': 'junior'}
        with patch.object(neo4j_people, 'create_person', return_value=person):
            self.call('POST', '/api/people', self.AD, body, 201)
        self.assertEqual(query("SELECT name FROM people WHERE user_id='u77'"), [{'name': '새사람'}])
        pid = self.call('POST', '/api/projects', self.U1, {'name': '새 멤버', 'members': ['u77']}, 201)['id']
        self.call('GET', f'/api/projects/{pid}', auth('u77'))


if __name__ == '__main__':
    unittest.main()