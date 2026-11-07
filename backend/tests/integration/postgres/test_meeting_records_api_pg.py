"""회의록 API (routes/meeting_records) + 프론트에서 빠졌던 것(멤버 · 회의 종류 · 보낸 사람 · 알림 문구 · 조직 이름)을 실제 DB 로.

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다. backend 폴더에서:
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_meeting_records_api_pg.py -v
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
class MeetingRecordApiTests(unittest.TestCase):
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
            cur.execute('TRUNCATE notifications, meetings, folders, person_skills, people, roles, teams, accounts CASCADE')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@t.local','USER'), ('u2','u2@t.local','USER'), ('u3','u3@t.local','USER')""")
            cur.execute("INSERT INTO teams (team_id,name) VALUES ('t-pm','기획팀')")
            cur.execute("INSERT INTO roles (role_id,name,team_id) VALUES ('r-pm','PM','t-pm')")
            cur.execute("""INSERT INTO people (user_id,name,position,team_id,role_id) VALUES
                       ('u1','김팀장','senior','t-pm','r-pm'), ('u2','이신입','junior','t-pm','r-pm'),
                       ('u3','강수현','mid','t-pm','r-pm')""")
        self.U1, self.U2, self.U3 = auth('u1'), auth('u2'), auth('u3')

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

    # ------------------------------------------------------------ B. 작성 흐름 · 상세 · 할 일
    def test_draft_submit_confirm_and_detail(self):
        pid = self._project()
        mid = self._draft(pid)
        self.assertEqual([d['id'] for d in self.call('GET', '/api/drafts', self.U1)], [mid])
        self.call('GET', f'/api/meetings/{mid}', self.U2, status=403)                  # 미리보기는 작성자 · 주관자만
        self.call('POST', '/api/drafts', self.U1, self._body(pid, meeting_id=mid, title='주간 회의 (수정)'), 201)  # 전체를 다시 보냄

        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        self.assertEqual(self.call('GET', '/api/drafts', self.U1), [])
        self.assertEqual([m['id'] for m in self.call('GET', '/api/review-queue', self.U1)], [mid])
        d = self.call('GET', f'/api/meetings/{mid}', self.U2)                          # 참석자는 생성 뒤 보임
        self.assertEqual((d['title'], d['initial_status'], d['directory_id']), ('주간 회의 (수정)', 'review', 'weekly'))
        self.assertEqual(d['summary']['text'], 'PG 수수료 2.5% 로 결정')
        self.assertEqual([p['text'] for p in d['paragraphs']], ['PG 수수료는 2.5%예요', '정산은 D+1'])
        self.assertIn('created', [n['kind'] for n in self.call('GET', '/api/notifications', self.U2)])
        self.call('POST', f'/api/meetings/{mid}/confirm', self.U2, status=403)        # 참석자는 확정 못 함
        self.call('POST', f'/api/meetings/{mid}/confirm', self.U1)
        self.assertEqual(self.call('GET', f'/api/meetings/{mid}', self.U1)['initial_status'], 'confirmed')
        self.call('GET', '/api/meetings/없음', self.U1, status=404)

    def test_summary_versions_attendees_sources_branch(self):
        pid = self._project(members=('u2', 'u3'))
        mid = self._draft(pid, summary_json={'meeting_id': 'x', 'agendas': []})
        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        new = {'summary': {'text': '고친 요약', 'src': []}}
        self.call('PUT', f'/api/meetings/{mid}/summary', self.U2, {'content': new}, status=403)
        self.call('PUT', f'/api/meetings/{mid}/summary', self.U1, {'content': new}, 201)
        self.assertEqual([v['version'] for v in self.call('GET', f'/api/meetings/{mid}/summaries', self.U1)], [2, 1])
        self.assertEqual(self.call('GET', f'/api/meetings/{mid}?version=1', self.U1)['summary']['text'],
                         'PG 수수료 2.5% 로 결정')
        self.assertEqual(self.call('GET', f'/api/meetings/{mid}/summary-json', self.U1),
                         {'meeting_id': 'x', 'agendas': []})

        self.assertEqual(set(self.call('PUT', f'/api/meetings/{mid}/attendees', self.U1, {'user_ids': ['u3']})),
                         {'u1', 'u3'})                                                  # 주관자는 늘 포함
        self.call('GET', f'/api/meetings/{mid}', self.U3)
        self.call('PUT', f'/api/meetings/{mid}/attendees', self.U1, {'user_ids': ['nobody']}, status=409)

        self.assertEqual(self.call('GET', f'/api/meetings/{mid}/sources', self.U1), [])
        self.call('GET', f'/api/meetings/{mid}/sources/latest', self.U1, status=404)

        self.call('POST', f'/api/meetings/{mid}/branch-requests', self.U3, status=204)
        self.assertEqual([r['requested_by'] for r in self.call('GET', f'/api/meetings/{mid}/branch-requests', self.U1)],
                         ['u3'])

    def test_todos(self):
        pid = self._project()
        mid = self._draft(pid)
        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        ai = self.call('GET', f'/api/meetings/{mid}/todos', self.U2)
        self.assertEqual([(t['text'], t['owners']) for t in ai], [('수수료 비교표 정리', ['u2'])])
        t = self.call('POST', f'/api/meetings/{mid}/todos', self.U1,
                      {'text': '정산 일정 공유', 'owners': ['u1', 'u2'], 'due': '11/7', 'due_date': '2026-11-07'}, 201)
        self.call('POST', f'/api/meetings/{mid}/todos', self.U2, {'text': '참석자는 못 함'}, status=403)
        self.call('POST', f'/api/meetings/{mid}/todos', self.U1, {'text': ''}, status=422)
        self.assertEqual(self.call('PUT', f"/api/todos/{t['id']}/owners", self.U1, {'owners': ['u2']}), ['u2'])
        self.assertEqual(self.call('PUT', f"/api/todos/{t['id']}/due", self.U1,
                                   {'due': '11/10', 'due_date': '2026-11-10'})['due'], '11/10')
        self.call('DELETE', f"/api/todos/{t['id']}", self.U1, status=204)
        self.assertEqual([x['id'] for x in self.call('GET', f'/api/meetings/{mid}/todos?deleted=true', self.U1)],
                         [t['id']])
        self.call('POST', f"/api/todos/{t['id']}/restore", self.U1, status=204)
        self.assertEqual(len(self.call('GET', f'/api/meetings/{mid}/todos', self.U1)), 2)
        self.call('DELETE', '/api/todos/999999', self.U1, status=404)

    # ------------------------------------------------------------ C. 프론트에서 빠졌던 것
    def test_project_members_and_meeting_types(self):
        pid = self._project(members=('u2', 'u3'))
        self.assertEqual(self.call('GET', f'/api/projects/{pid}', self.U2)['members'], ['u1', 'u2', 'u3'])
        self.assertEqual(self.call('GET', f'/api/projects/{pid}/members', self.U1), ['u1', 'u2', 'u3'])
        types = self.call('GET', '/api/meeting-types', self.U1)
        self.assertEqual([t['id'] for t in types], ['monthly', 'weekly', 'normal'])
        self.assertTrue(all(t['name'] and 'meta' in t for t in types))
        mid = self._draft(pid, attendees=[])
        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        hidden = {t['id']: t['hidden_count'] for t in self.call('GET', '/api/meeting-types', self.U3)}
        self.assertEqual(hidden['weekly'], 1)                                           # 멤버지만 참석 · 공유 X
        self.assertEqual({t['id']: t['hidden_count'] for t in self.call('GET', '/api/meeting-types', self.U1)}['weekly'], 0)

    def test_share_sender_and_notification_texts(self):
        pid = self._project(members=('u2',))
        texts = [n['text'] for n in self.call('GET', '/api/notifications', self.U2) if n['kind'] == 'member']
        self.assertEqual(texts, ['김팀장 · 기획팀 님이 「결제 개편」 폴더에 나를 멤버로 추가했어요.'])
        mid = self._draft(pid)
        self.call('POST', f'/api/meetings/{mid}/submit', self.U1, {})
        self.call('POST', f'/api/meetings/{mid}/shares', self.U1, {'recipient_user_id': 'u3'}, 201)
        self.assertEqual(self.call('GET', '/api/shares/received', self.U3)[0]['sender'], '김팀장 · 기획팀')
        shared = [n['text'] for n in self.call('GET', '/api/notifications', self.U3) if n['kind'] == 'shared']
        self.assertEqual(shared, ['김팀장 · 기획팀 님이 「주간 회의」을 공유했어요.'])

    def test_organization_name(self):
        self.assertEqual(self.call('GET', '/api/integrations/organization', self.U1), {'name': ''})
        with self.transaction() as cur:
            cur.execute("UPDATE app_settings SET value = '\"데모컴퍼니\"' WHERE key = 'organization_name'")
        self.assertEqual(self.call('GET', '/api/integrations/organization', self.U1), {'name': '데모컴퍼니'})


if __name__ == '__main__':
    unittest.main()