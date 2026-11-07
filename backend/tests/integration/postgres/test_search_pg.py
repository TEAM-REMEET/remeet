"""회의 검색 (adapters/postgres/search.py · migrations/009).

이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_search_pg.py -v   (backend 폴더에서)
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')
ROOT = Path(__file__).resolve().parents[3] / 'src' / 'followup' / 'adapters' / 'postgres'  # schema.sql · migrations/
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class SearchTests(unittest.TestCase):
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
        from followup.adapters.postgres import compose, detail, folders, shares
        with self.transaction() as cur:
            cur.execute('TRUNCATE notifications, meetings, folders, accounts CASCADE')
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@test.local','USER'), ('u2','u2@test.local','USER'), ('u3','u3@test.local','USER')""")
        # 검색 범위 = 내 프로젝트의 회의 중 볼 수 있는 것 (web visible_note_ids) → 회의는 모두 이 프로젝트에
        self.proj = folders.create_folder(actor_id='u1', name='결제 시스템 개편', members=['u2', 'u3'])
        P = self.proj['id']

        def make(title, day, *, folder=P, type_id='normal', lines=(), decision=None, todo=None, submit=True,
                 attendees=('u2',)):
            content = {'summary': {'text': f'{title} 요약', 'src': []},
                       'decisions': [{'text': decision, 'src': []}] if decision else []}
            m = compose.save_draft(actor_id='u1', content=content, title=title, directory_id=type_id, project_id=folder,
                                   held_at=datetime(2026, 10, day, 1, tzinfo=timezone.utc), attendees=list(attendees),
                                   transcript=[{'line_id': str(i), 'speaker': sp, 'text': t}
                                               for i, (sp, t) in enumerate(lines, 1)],
                                   todos=[{'text': todo, 'src': []}] if todo else [])
            if submit:
                compose.submit_meeting(actor_id='u1', meeting_id=m['id'])
            return m['id']

        self.m_week = make('주간 회의 — 10월 4주차', 27, type_id='weekly',
                           lines=[('도윤', 'PG 수수료는 2.5%예요'), ('서윤', '정산은 D+1')], decision='수수료 2.5% 확정')
        self.m_outside = make('PG 바깥 회의', 21, folder=None, lines=[('도윤', '수수료 얘기')])   # 프로젝트 없음 → 검색 X
        self.m_proj = make('PG사 선정', 22, lines=[('도윤', '기술 검토 끝')],
                           todo='수수료 비교표 정리')
        self.m_draft = make('비밀 회의 수수료', 28, submit=False, lines=[('도윤', '수수료 미정')])
        # u3 에게는 요약만 공유 (원문 X), u2 는 참석자
        self.m_shared = make('정산 정책 리뷰', 20, lines=[('수현', '수수료 인상 검토')], attendees=())
        shares.create_share(actor_id='u1', meeting_id=self.m_shared, recipient_user_id='u3', scope='summary')
        detail.set_attendees(actor_id='u1', meeting_id=self.m_shared, user_ids=[])

    def test_titles(self):
        from followup.adapters.postgres import search
        from followup.adapters.postgres.common import ValidationError
        self.assertEqual([r['id'] for r in search.search_titles(actor_id='u1', q='pg')], [self.m_proj])   # 대소문자 무시
        # 미리보기(draft)는 트리처럼 검색에도 안 나옴 (내 것이라도) · 프로젝트 없는 회의도 안 나옴 (web visible_note_ids)
        self.assertEqual([r['id'] for r in search.search_titles(actor_id='u1', q='회의')], [self.m_week])
        self.assertEqual([r['id'] for r in search.search_titles(actor_id='u2', q='회의')], [self.m_week])
        self.assertEqual(search.search_titles(actor_id='u3', q='회의'), [])          # 멤버지만 참석 · 공유 X
        self.assertNotIn(self.m_outside, [r['id'] for r in search.search_titles(actor_id='u1', q='PG')])
        with self.assertRaises(ValidationError):
            search.search_titles(actor_id='u1', q='  ')

    def test_folders(self):
        """[폴더명] 내 프로젝트 이름만 (web: 정기 회의 폴더는 검색 안 함)."""
        from followup.adapters.postgres import search
        r = search.search_folders(actor_id='u1', q='결제')
        self.assertEqual([(x['id'], x['name'], x['count'], x['member_count']) for x in r],
                         [(self.proj['id'], '결제 시스템 개편', 3, 3)])               # draft 는 회의 수에서 빠짐
        self.assertEqual(search.search_folders(actor_id='u1', q='10월 4주'), [])
        self.assertEqual(search.search_folders(actor_id='u2', q='결제')[0]['count'], 2)   # u2: 참석한 회의만
        with self.transaction() as cur:
            cur.execute("INSERT INTO accounts (user_id,email) VALUES ('u9','u9@test.local')")
        self.assertEqual(search.search_folders(actor_id='u9', q='결제'), [])            # 프로젝트 멤버 아님

    def test_content_respects_raw_permission(self):
        from followup.adapters.postgres import search
        hits = {r['id']: r for r in search.search_content(actor_id='u1', q='수수료')}
        self.assertEqual(set(hits), {self.m_week, self.m_proj, self.m_shared})       # draft · 프로젝트 밖 회의 X
        self.assertEqual(hits[self.m_week]['matches'], 2)                            # 원문 1 + 결정 1
        self.assertEqual([s['where'] for s in hits[self.m_week]['snippets']], ['decision', 'transcript'])
        self.assertEqual(hits[self.m_week]['snippets'][1]['line_id'], '1')           # 원문 위치로 이동용
        self.assertEqual([s['where'] for s in hits[self.m_proj]['snippets']], ['todo'])
        # u3: 요약만 공유 → 원문 발화로는 안 걸림 (요약 글에는 '수수료' 없음)
        self.assertEqual(search.search_content(actor_id='u3', q='수수료'), [])
        self.assertEqual([r['id'] for r in search.search_content(actor_id='u3', q='정산 정책')], [self.m_shared])
        self.assertNotIn(self.m_draft, [r['id'] for r in search.search_content(actor_id='u2', q='수수료')])

    def test_like_special_characters(self):
        from followup.adapters.postgres import search
        self.assertEqual([r['id'] for r in search.search_content(actor_id='u1', q='2.5%')], [self.m_week])
        self.assertEqual(search.search_content(actor_id='u1', q='%%%'), [])
        self.assertEqual(search.search_titles(actor_id='u1', q='_'), [])

    def test_people(self):
        from followup.adapters.postgres import search
        rows = search.search_people(actor_id='u1', people={'u2': '서윤'})
        self.assertEqual([(r['id'], r['role']) for r in rows], [(self.m_week, '참석'), (self.m_proj, '참석')])
        self.assertEqual(rows[0]['said'], ['정산은 D+1'])
        self.assertNotIn(self.m_draft, [r['id'] for r in search.search_people(actor_id='u2', people={'u2': '서윤'})])
        self.assertEqual([r['role'] for r in search.search_people(actor_id='u2', people={'u1': '팀장'})], ['주관', '주관'])
        self.assertEqual(search.search_people(actor_id='u1', people={}), [])

    def test_search_all_counts(self):
        from followup.adapters.postgres import search
        res = search.search_all(actor_id='u1', q='PG')
        self.assertEqual(res['counts'], {'title': 1, 'folder': 0, 'people': 0, 'content': 2})

    def test_rls_default_flow_author_edits(self):
        """009: 검토자 없는 회의는 작성자 · 주관자가 고침 (RLS 에서도)."""
        def upd(user):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                cur.execute('UPDATE meetings SET title = title WHERE id = %s', (self.m_week,))
                return cur.rowcount
        self.assertEqual((upd('u1'), upd('u2')), (1, 0))


if __name__ == '__main__':
    unittest.main()