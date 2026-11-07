"""회의 원본 · 요약 회의록 · 전체/프로젝트별 목록 통합 테스트.

test_integration_pg.py 와 같은 규칙: 이름이 _test 로 끝나는 DB 에서만 돌고, 그 DB 의 표는 초기화됩니다.
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  python -m unittest discover -s tests/integration/postgres -p test_minutes_pg.py -v   (backend 폴더에서)
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')
ROOT = Path(__file__).resolve().parents[3] / 'src' / 'followup' / 'adapters' / 'postgres'  # schema.sql · migrations/
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'src'))


def _safe_test_database(url: str) -> bool:
    if not url:
        return False
    from psycopg.conninfo import conninfo_to_dict
    dbname = conninfo_to_dict(url).get('dbname', '')
    return dbname.endswith('_test') and dbname != 'remeet'


def _at(day: int) -> datetime:
    return datetime(2026, 11, day, 10, tzinfo=timezone.utc)


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class MinutesIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _safe_test_database(TEST_URL):
            raise RuntimeError('안전을 위해 이름이 _test 로 끝나는 별도 데이터베이스만 허용합니다.')
        os.environ['DATABASE_URL'] = TEST_URL
        from followup.adapters.postgres.db import pool, transaction
        pool.cache_clear()
        cls.transaction = staticmethod(transaction)
        # 새 DB 순서 그대로: schema.sql → migrate.py (001~004)
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
            cur.execute('''TRUNCATE notifications, meeting_shares, transcript_lines, meeting_keywords,
                        meeting_speakers, meeting_analyses, meeting_sources, meeting_summaries,
                        meetings, folder_briefs, folder_members, folders, meeting_types, accounts CASCADE''')
            cur.execute("""INSERT INTO meeting_types (id,name,meta,sort_order) VALUES
                       ('monthly','월간 회의','',0), ('weekly','주간 회의','',1), ('normal','일반 회의','',2)""")
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@test.local','USER'), ('u2','u2@test.local','USER'),
                       ('u3','u3@test.local','USER'), ('u4','u4@test.local','ADMIN')""")

    # ------------------------------------------------------------ helpers
    def _folder(self, name='프로젝트A', members=()):
        from followup.adapters.postgres.folders import create_folder
        return create_folder(actor_id='u1', name=name, members=list(members))

    def _meeting(self, mid, project_id=None, day=None, title='', directory_id='normal'):
        from followup.adapters.postgres.meetings import create_meeting
        return create_meeting(actor_id='u1', meeting_id=mid, project_id=project_id, title=title,
                              held_at=_at(day) if day else None, directory_id=directory_id)

    # web 목업 meetings.json 과 같은 모양
    CONTENT = {
        'summary': {'text': '출시 일정을 12/1 로 확정', 'src': ['1', '3']},
        'discussion': [{'label': '쟁점', 'text': 'QA 기간', 'src': ['3']}],
        'decisions': [{'text': '12/1 출시', 'src': ['2']}],
        'open_issues': [{'text': 'QA 인원', 'src': []}],
        'results': [{'kind': '결정', 'text': '12/1 출시 확정', 'agendas': [1], 'src': ['2']}],
        'agendas': [
            {'title': '출시 일정', 'status': '결정', 'tone': 'ok', 'deep': True, 'src': ['1', '2'],
             'points': [{'text': '12/1 가능', 'src': ['2']}],
             'background': [{'text': '지난 회의 미정', 'src': ['1']}],
             'topics': [{'title': 'QA', 'points': [{'kind': '정보', 'text': 'QA 1주 필요', 'src': ['3', '4']},
                                                    {'kind': '반대·우려', 'text': '인원 부족', 'src': ['4']}]},
                        {'title': '배포', 'points': [{'kind': '제안', 'text': '야간 배포', 'src': []}]}],
             'decisions': [{'text': '12/1 출시', 'src': ['2']}], 'open_issues': [], 'next': [{'text': '마케팅', 'src': []}]},
            {'title': '예산', 'status': '보류', 'tone': 'hold', 'deep': False, 'src': [], 'points': []},
        ],
    }
    TODOS = [{'text': 'QA 계획서', 'owners': ['u2'], 'due': '11/20', 'due_date': '2026-11-20', 'src': ['3'], 'urgent': True},
             {'text': '회의록 공유', 'owners': [], 'due': '', 'src': []}]

    # ------------------------------------------------------------ 원본
    def test_source_versions_and_raw_permission(self):
        from followup.adapters.postgres import minutes, shares
        from followup.adapters.postgres.common import PermissionDenied, NotFoundError
        self._meeting('m1')
        with self.assertRaises(NotFoundError):
            minutes.get_source(actor_id='u1', meeting_id='m1')
        v1 = minutes.add_source(actor_id='u1', meeting_id='m1', file_name='a.txt', content_text='A: 첫 원본')
        v2 = minutes.add_source(actor_id='u1', meeting_id='m1', file_name='b.txt', content_text='A: 고친 원본')
        self.assertEqual((v1['version'], v2['version']), (1, 2))
        self.assertEqual(minutes.get_source(actor_id='u1', meeting_id='m1')['content_text'], 'A: 고친 원본')
        self.assertEqual(minutes.get_source(actor_id='u1', meeting_id='m1', version=1)['content_text'], 'A: 첫 원본')
        self.assertEqual([s['version'] for s in minutes.list_sources(actor_id='u1', meeting_id='m1')], [2, 1])
        # 요약만 공유받은 사람은 원본 못 봄 · 수정 못 함
        sh = shares.create_share(actor_id='u1', meeting_id='m1', recipient_user_id='u2', scope='summary')
        with self.assertRaises(PermissionDenied):
            minutes.get_source(actor_id='u2', meeting_id='m1')
        with self.assertRaises(PermissionDenied):
            minutes.add_source(actor_id='u2', meeting_id='m1', file_name='x', content_text='x')
        shares.update_share(actor_id='u1', share_id=sh['id'], scope='full', lock_raw=False)
        self.assertEqual(minutes.get_source(actor_id='u2', meeting_id='m1')['version'], 2)

    # ------------------------------------------------------------ 요약 (adapters/postgres/detail.py)
    def test_summary_save_versions_and_permission(self):
        from followup.adapters.postgres import detail, minutes, shares
        from followup.adapters.postgres.common import PermissionDenied
        self._meeting('m1')
        src = minutes.add_source(actor_id='u1', meeting_id='m1', file_name='a.txt', content_text='원본')
        detail.save_summary(actor_id='u1', meeting_id='m1', content=self.CONTENT, todos=self.TODOS,
                            origin='ai', source_id=src['id'])
        d = detail.get_meeting_detail(actor_id='u1', meeting_id='m1')
        self.assertEqual(d['summary_version'], 1)
        for key in ('summary', 'discussion', 'decisions', 'open_issues', 'results', 'agendas'):
            with self.subTest(key=key):
                self.assertEqual(d[key], self.CONTENT[key])
        self.assertEqual([(t['text'], t['owners'], t['due'], t['urgent']) for t in d['todos']],
                         [('QA 계획서', ['u2'], '11/20', True), ('회의록 공유', [], '', False)])

        # 편집 저장 = 새 버전. 액션 아이템은 안 줬으니 그대로
        detail.save_summary(actor_id='u1', meeting_id='m1', content={'summary': {'text': '수정본', 'src': []}})
        d2 = detail.get_meeting_detail(actor_id='u1', meeting_id='m1')
        self.assertEqual((d2['summary']['text'], d2['agendas'], len(d2['todos'])), ('수정본', [], 2))
        self.assertEqual(detail.get_meeting_detail(actor_id='u1', meeting_id='m1', version=1)['agendas'],
                         self.CONTENT['agendas'])
        self.assertEqual([(v['version'], v['origin']) for v in detail.list_summary_versions(actor_id='u1', meeting_id='m1')],
                         [(2, 'edited'), (1, 'ai')])

        # 요약 공유받은 사람: 요약 O, 원문 X, 수정 X / 관계없는 사람: X
        shares.create_share(actor_id='u1', meeting_id='m1', recipient_user_id='u2', scope='summary')
        transcript_set = __import__('followup.adapters.postgres.meetings', fromlist=['x']).replace_transcript
        transcript_set(actor_id='u1', meeting_id='m1', lines=[{'line_id': '1', 'speaker': 'A', 'text': '비공개'}])
        d_u2 = detail.get_meeting_detail(actor_id='u2', meeting_id='m1')
        self.assertEqual((d_u2['paragraphs'], d_u2['can_view_raw'], d_u2['shared']['scope']), ([], False, 'summary'))
        self.assertEqual(len(detail.get_meeting_detail(actor_id='u1', meeting_id='m1')['paragraphs']), 1)
        with self.assertRaises(PermissionDenied):
            detail.save_summary(actor_id='u2', meeting_id='m1', content={})
        with self.assertRaises(PermissionDenied):
            detail.get_meeting_detail(actor_id='u3', meeting_id='m1')

    def test_bad_summary_inserts_nothing(self):
        from followup.adapters.postgres import detail, minutes
        from followup.adapters.postgres.common import ValidationError
        self._meeting('m1')
        self._meeting('m2')
        bad = [
            dict(content={'results': [{'kind': '결정', 'text': 'x', 'agendas': [3]}]}),
            dict(content={'agendas': [{'title': ''}]}),
            dict(content={'agendas': [{'title': 't', 'tone': 'red'}]}),
            dict(content={'decisions': [{'text': 'x', 'src': '1,2'}]}),
            dict(content={'agendas': [{'title': 't', 'topics': [{'title': 'a', 'points': [{'text': ''}]}]}]}),
            dict(content={}, todos=[{'text': 'a', 'due_date': '11월 20일'}]),
        ]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                detail.save_summary(actor_id='u1', meeting_id='m1', **kw)
        other_src = minutes.add_source(actor_id='u1', meeting_id='m2', file_name='b', content_text='b')
        with self.assertRaises(ValidationError):  # 다른 회의의 원본을 가리키면 안 됨
            detail.save_summary(actor_id='u1', meeting_id='m1', content={}, source_id=other_src['id'])
        with self.transaction() as cur:
            cur.execute("SELECT count(*) AS n FROM meeting_summaries WHERE meeting_id='m1'")
            self.assertEqual(cur.fetchone()['n'], 0)

    # ------------------------------------------------------------ 회의 상세: 참석자 · 액션 아이템 · 분리 요청
    def test_attendees_todos_and_branch_requests(self):
        from followup.adapters.postgres import detail, minutes
        from followup.adapters.postgres.common import PermissionDenied, ValidationError
        self._meeting('m1', day=3, title='회의')
        detail.save_summary(actor_id='u1', meeting_id='m1', content=self.CONTENT, todos=self.TODOS, origin='ai')
        # u2 는 아직 관계없음 → 참석자로 넣으면 목록 · 상세 · 원문 모두 보임
        self.assertEqual(minutes.list_all_meetings(actor_id='u2'), [])
        self.assertEqual(detail.set_attendees(actor_id='u1', meeting_id='m1', user_ids=['u2']), ['u1', 'u2'])
        self.assertEqual([r['id'] for r in minutes.list_all_meetings(actor_id='u2')], ['m1'])
        d = detail.get_meeting_detail(actor_id='u2', meeting_id='m1')
        self.assertEqual((d['people'], d['can_view_raw'], d['is_host']), (['u1', 'u2'], True, False))
        with self.assertRaises(PermissionDenied):
            detail.set_attendees(actor_id='u2', meeting_id='m1', user_ids=['u3'])

        # 액션 아이템: 추가 · 담당자 지정 · 삭제 · 되돌리기 (주관자만)
        t = detail.add_todo(actor_id='u1', meeting_id='m1', text='디자인 시안', owners=['u2', 'u3'], due='오늘',
                            src=['2'])
        self.assertEqual((t['origin'], t['owners']), ('manual', ['u2', 'u3']))
        first = d['todos'][0]['id']
        self.assertEqual(detail.set_todo_owners(actor_id='u1', todo_id=first, owners=['u3', 'u3', 'u1']), ['u3', 'u1'])
        detail.delete_todo(actor_id='u1', todo_id=first)
        self.assertEqual([x['text'] for x in detail.list_todos(actor_id='u1', meeting_id='m1')], ['회의록 공유', '디자인 시안'])
        self.assertEqual([x['id'] for x in detail.list_todos(actor_id='u1', meeting_id='m1', deleted=True)], [first])
        with self.assertRaises(ValidationError):
            detail.set_todo_owners(actor_id='u1', todo_id=first, owners=['u2'])
        detail.restore_todo(actor_id='u1', todo_id=first)
        self.assertEqual(detail.get_meeting_detail(actor_id='u1', meeting_id='m1')['todos'][0]['owners'], ['u3', 'u1'])
        for call in (lambda: detail.add_todo(actor_id='u2', meeting_id='m1', text='x'),
                     lambda: detail.delete_todo(actor_id='u2', todo_id=first),
                     lambda: detail.set_todo_owners(actor_id='u2', todo_id=first, owners=[])):
            with self.subTest(call=call), self.assertRaises(PermissionDenied):
                call()

        # AI 재요약: AI 액션 아이템만 바뀌고 직접 추가한 건 남음
        detail.save_summary(actor_id='u1', meeting_id='m1', content={}, origin='ai',
                            todos=[{'text': '새 AI 할 일', 'src': []}])
        self.assertEqual([x['text'] for x in detail.list_todos(actor_id='u1', meeting_id='m1')],
                         ['디자인 시안', '새 AI 할 일'])

        # 안건 분리 요청
        detail.request_branch(actor_id='u2', meeting_id='m1')
        detail.request_branch(actor_id='u2', meeting_id='m1')
        self.assertEqual([r['requested_by'] for r in detail.list_branch_requests(actor_id='u1', meeting_id='m1')], ['u2'])
        with self.assertRaises(ValidationError):
            detail.request_branch(actor_id='u1', meeting_id='m1')
        with self.assertRaises(PermissionDenied):
            detail.request_branch(actor_id='u3', meeting_id='m1')

    # ------------------------------------------------------------ 목록
    def test_all_meetings_latest_first(self):
        from followup.adapters.postgres import minutes, shares
        from followup.adapters.postgres.meetings import delete_meeting
        f = self._folder()
        self._meeting('old', f['id'], day=1, title='옛 회의')
        self._meeting('new', f['id'], day=5, title='새 회의')
        self._meeting('mid', None, day=3)
        self._meeting('gone', None, day=9)
        delete_meeting(actor_id='u1', meeting_id='gone')
        from followup.adapters.postgres import detail
        detail.save_summary(actor_id='u1', meeting_id='new', content={'summary': {'text': '요약 미리보기', 'src': []}})
        rows = minutes.list_all_meetings(actor_id='u1')
        self.assertEqual([r['id'] for r in rows], ['new', 'mid', 'old'])
        self.assertEqual(rows[0]['project_name'], '프로젝트A')
        self.assertEqual(rows[0]['summary_preview'], '요약 미리보기')
        self.assertIsNone(rows[1]['project_name'])
        self.assertEqual([r['id'] for r in minutes.list_all_meetings(actor_id='u1', limit=1, offset=1)], ['mid'])
        # u2 는 공유받은 것만
        self.assertEqual(minutes.list_all_meetings(actor_id='u2'), [])
        shares.create_share(actor_id='u1', meeting_id='old', recipient_user_id='u2', scope='summary')
        self.assertEqual([r['id'] for r in minutes.list_all_meetings(actor_id='u2')], ['old'])

    def test_project_folders_and_meetings(self):
        """프로젝트 목록 · 프로젝트 안 회의 (최신순). 멤버라는 것만으로는 회의가 안 보임 — 참석 · 공유받아야 (web can_see)."""
        from followup.adapters.postgres import detail, minutes, shares
        from followup.adapters.postgres.common import PermissionDenied
        a = self._folder('프로젝트A', members=['u2'])
        b = self._folder('프로젝트B')
        self._meeting('a1', a['id'], day=2)
        self._meeting('a2', a['id'], day=4)
        self._meeting('a0', a['id'])          # 날짜 모름 → 만든 시각(지금)으로 정렬 → 맨 위
        self._meeting('b1', b['id'], day=3)
        folders = minutes.list_project_folders(actor_id='u1')
        self.assertEqual([(x['name'], x['meeting_count']) for x in folders], [('프로젝트A', 3), ('프로젝트B', 1)])
        self.assertEqual([r['id'] for r in minutes.list_project_meetings(actor_id='u1', project_id=a['id'])],
                         ['a0', 'a2', 'a1'])
        # u2 는 A 멤버지만 참석 · 공유가 없으면 회의는 안 보임
        self.assertEqual([(x['name'], x['meeting_count']) for x in minutes.list_project_folders(actor_id='u2')],
                         [('프로젝트A', 0)])
        self.assertEqual(minutes.list_project_meetings(actor_id='u2', project_id=a['id']), [])
        with self.assertRaises(PermissionDenied):
            detail.get_meeting_detail(actor_id='u2', meeting_id='a1')
        detail.set_attendees(actor_id='u1', meeting_id='a1', user_ids=['u2'])                       # 참석
        shares.create_share(actor_id='u1', meeting_id='a2', recipient_user_id='u2', scope='summary')  # 미참석 공유
        self.assertEqual([r['id'] for r in minutes.list_project_meetings(actor_id='u2', project_id=a['id'])],
                         ['a2', 'a1'])
        with self.assertRaises(PermissionDenied):
            minutes.list_project_meetings(actor_id='u2', project_id=b['id'])
        with self.assertRaises(PermissionDenied):
            minutes.list_project_folders(actor_id='u4')    # 관리자는 회의 기능 접근 불가 (기존 규칙)

    def test_regular_tree_and_project_tree(self):
        """web sidebar 와 같은 트리. 같은 회의가 정기 회의(종류별) · 프로젝트 두 트리에 다 나옴 (토글).
        정기: 월간 ─ 년 ─ 「9월 (날짜)」 / 주간 ─ 년 ─ 월 ─ 「4주차 (날짜)」 / 일반 ─ 「날짜」 · 프로젝트: 프로젝트 ─ 폴더 ─ 「날짜」."""
        from followup.adapters.postgres import minutes
        from followup.adapters.postgres.common import ValidationError
        from followup.adapters.postgres.meetings import update_meeting
        p = self._folder('결제 프로젝트')
        q = self._folder('기획')
        self._meeting('w-thu', p['id'], day=5, directory_id='weekly')    # 11/5(목) → 11/2 주 → 11월 1주차
        self._meeting('w-sun', q['id'], day=1, directory_id='weekly')    # 11/1(일) → 10/26 주 → 2026년 10월 4주차
        self._meeting('mon', p['id'], day=2, directory_id='monthly')
        self._meeting('n-1', p['id'], day=3)
        self._meeting('solo', None, day=4)                               # 프로젝트 없는 회의 → 트리에 안 나옴

        tree = {t['node']: t for t in minutes.list_regular_tree(actor_id='u1')}
        self.assertEqual(list(tree), ['t:monthly', 't:weekly', 't:normal'])

        def flat(n):
            return [(c['node'], c['label'], c.get('count')) for c in n['children']] + \
                [x for c in n['children'] for x in flat(c)]

        self.assertEqual(flat(tree['t:monthly']), [('y:monthly:2026', '2026년', 1), ('m:mon', '11월 (2026.11.02)', None)])
        self.assertEqual(flat(tree['t:weekly']),
                         [('y:weekly:2026', '2026년', 2),
                          ('ym:weekly:2026:11', '11월', 1), ('ym:weekly:2026:10', '10월', 1),
                          ('m:w-thu', '1주차 (2026.11.05)', None), ('m:w-sun', '4주차 (2026.11.01)', None)])
        self.assertEqual(flat(tree['t:normal']), [('m:n-1', '2026.11.03', None)])
        self.assertEqual([t['count'] for t in tree.values()], [1, 2, 1])
        leaf = tree['t:normal']['children'][0]
        self.assertEqual((leaf['meeting_id'], leaf['project_id'], leaf['date']), ('n-1', p['id'], '2026.11.03'))

        pt = minutes.list_project_tree(actor_id='u1')
        self.assertEqual((pt['node'], pt['label'], pt['count']), ('proj', '프로젝트', 2))
        self.assertEqual([(c['node'], c['label'], [x['label'] for x in c['children']]) for c in pt['children']],
                         [(f"pf:{p['id']}", '결제 프로젝트', ['2026.11.05', '2026.11.03', '2026.11.02']),
                          (f"pf:{q['id']}", '기획', ['2026.11.01'])])

        rows = minutes.list_regular_meetings(actor_id='u1', directory_id='weekly')
        self.assertEqual([(r['id'], r['date_label'], r['project_name'], r['directory_name']) for r in rows],
                         [('w-thu', '2026.11.05', '결제 프로젝트', '주간 회의'), ('w-sun', '2026.11.01', '기획', '주간 회의')])
        self.assertEqual([r['id'] for r in minutes.list_all_meetings(actor_id='u1')],
                         ['w-thu', 'solo', 'n-1', 'mon', 'w-sun'])

        # 종류 바꾸기 · 프로젝트에서 빼기
        update_meeting(actor_id='u1', meeting_id='n-1', directory_id='monthly')
        self.assertEqual([r['id'] for r in minutes.list_regular_meetings(actor_id='u1', directory_id='monthly')],
                         ['n-1', 'mon'])
        update_meeting(actor_id='u1', meeting_id='n-1', clear_project=True)
        self.assertEqual([r['id'] for r in minutes.list_regular_meetings(actor_id='u1', directory_id='monthly')], ['mon'])
        with self.assertRaises(ValidationError):
            self._meeting('bad', directory_id='daily')

    # ------------------------------------------------------------ RLS (migration 004)
    def test_rls_on_sources_and_summaries(self):
        from followup.adapters.postgres import detail, minutes, shares
        self._meeting('m1')
        minutes.add_source(actor_id='u1', meeting_id='m1', file_name='a.txt', content_text='비밀 원본')
        detail.save_summary(actor_id='u1', meeting_id='m1', content=self.CONTENT, todos=self.TODOS)
        shares.create_share(actor_id='u1', meeting_id='m1', recipient_user_id='u2', scope='summary')
        tables = ('meeting_sources', 'meeting_summaries', 'summary_agendas', 'summary_topics', 'summary_points',
                  'meeting_todos', 'meeting_todo_owners', 'meeting_attendees')

        def visible(user):
            with self.transaction(user) as cur:
                cur.execute('SET LOCAL ROLE remeet_app')
                out = {}
                for t in tables:
                    cur.execute(f'SELECT count(*) AS n FROM {t}')
                    out[t] = cur.fetchone()['n']
                return out

        self.assertEqual(visible('u1'), {'meeting_sources': 1, 'meeting_summaries': 1, 'summary_agendas': 2,
                                         'summary_topics': 2, 'summary_points': 11, 'meeting_todos': 2,
                                         'meeting_todo_owners': 1, 'meeting_attendees': 1})
        u2 = visible('u2')
        self.assertEqual(u2['meeting_sources'], 0)                   # 요약 공유 → 원본 X
        self.assertEqual((u2['summary_points'], u2['meeting_todos']), (11, 2))   # 요약 · 할 일 O
        self.assertEqual(set(visible('u3').values()), {0})
        # 참석자로 넣으면 원본도 보임 (migration 006 의 raw_visible)
        detail.set_attendees(actor_id='u1', meeting_id='m1', user_ids=['u3'])
        self.assertEqual(visible('u3')['meeting_sources'], 1)
        import psycopg
        for sql in ("INSERT INTO meeting_summaries (meeting_id, version, overview) VALUES ('m1', 9, 'x')",
                    "UPDATE meeting_todos SET text = 'x'"):
            with self.subTest(sql=sql):
                try:
                    with self.transaction('u2') as cur:
                        cur.execute('SET LOCAL ROLE remeet_app')
                        cur.execute(sql)
                        changed = cur.rowcount
                except psycopg.errors.InsufficientPrivilege:
                    changed = 0
                self.assertEqual(changed, 0)

    # ------------------------------------------------------------ import_summary.py
    def test_import_summary_fills_title_source_summary(self):
        from followup.entrypoints import import_summary
        from followup.adapters.postgres import minutes
        with tempfile.TemporaryDirectory() as d:
            mdir = Path(d) / 'TS9001a'
            mdir.mkdir()
            (mdir / 'TS9001a.txt').write_text('A: 안녕하세요\nB: 시작하죠\n', encoding='utf-8')
            doc = {
                'meeting_id': 'TS9001a', 'created_at': '2026-11-01T09:00:00+09:00',
                'config': {'model': 'test'},
                'source_document': {'input_path': 'C:\\data\\TS9001a.txt'},
                'meeting_info': {'title': '킥오프', 'date': '2026-10-30', 'speakers': {'A': 'PM'}},
                'summary': '킥오프 진행',
                'agendas': [{'title': '범위', 'decisions': ['MVP 먼저'],
                             'subtopics': [{'subtopic': '기능', 'discussions': ['로그인', '목록'],
                                            'evidence': '1,2'}],
                             'action_items': [{'description': '와이어프레임', 'owner': 'A', 'due': '2026-11-10'}]}],
                'transcript': [{'utterance_index': 1, 'speaker': 'A', 'text': '안녕하세요'},
                               {'utterance_index': 2, 'speaker': 'B', 'text': '시작하죠'}],
            }
            path = mdir / 'TS9001a_summary.json'
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                import_summary.import_one(path, folder=None, host='u1', speakers={'A': 'u1'})
                import_summary.import_one(path, folder=None, host='u1', speakers={'A': 'u1'})  # 두 번째
        with self.transaction() as cur:
            cur.execute("SELECT title, held_at::date AS d FROM meetings WHERE id='TS9001a'")
            m = cur.fetchone()
        self.assertEqual((m['title'], str(m['d'])), ('킥오프', '2026-10-30'))
        self.assertEqual(len(minutes.list_sources(actor_id='u1', meeting_id='TS9001a')), 1)  # 같은 원본은 1번만
        from followup.adapters.postgres import detail
        d = detail.get_meeting_detail(actor_id='u1', meeting_id='TS9001a')
        self.assertEqual(d['summary_version'], 2)
        self.assertEqual(d['summary']['text'], '킥오프 진행')
        self.assertEqual(d['agendas'][0]['decisions'], [{'text': 'MVP 먼저', 'src': []}])
        self.assertEqual(d['agendas'][0]['topics'], [{'title': '기능', 'points': [
            {'kind': '정보', 'text': '로그인\n목록', 'src': ['1', '2']}]}])
        self.assertEqual([(t['text'], t['due']) for t in d['todos']], [('와이어프레임', '11/10')])  # 재적재해도 1개
        self.assertEqual(d['people'], ['u1'])
        self.assertEqual(len(d['paragraphs']), 2)
        with self.transaction() as cur:
            cur.execute("SELECT owner_raw, due_date::text AS d FROM meeting_todos WHERE meeting_id='TS9001a'")
            self.assertEqual(cur.fetchone(), {'owner_raw': 'A', 'd': '2026-11-10'})


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class MigrationPathTests(unittest.TestCase):
    """이미 쓰던 DB 에 migrate.py 로 004 → 005 → 006 을 차례로 얹을 수 있는지 (데이터 보존 포함)."""

    def _migrate(self, upto: str | None = None) -> str:
        """upto 보다 뒤 번호 파일은 잠깐 숨기고 마이그레이션."""
        from followup.adapters.postgres import migrate
        held = [f for f in sorted((ROOT / 'migrations').glob('[0-9][0-9][0-9]_*.sql')) if upto and f.name[:3] > upto]
        for f in held:
            f.rename(f.with_suffix('.hold'))
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                migrate.main()
            return out.getvalue()
        finally:
            for f in held:
                f.with_suffix('.hold').rename(f)

    def test_existing_db_upgrade_and_reset(self):
        import psycopg
        if not _safe_test_database(TEST_URL):
            raise RuntimeError('_test DB 만 허용')
        os.environ['DATABASE_URL'] = TEST_URL
        schema = (ROOT / 'schema.sql').read_text(encoding='utf-8')
        # 1) 예전 스키마 흉내: 004 ~ 006 이 만든 것 제거 + 예전 목업 데이터 (period- 폴더, 폴더에만 종류가 있음)
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(schema)
            conn.execute('''DROP TABLE branch_requests, meeting_todo_owners, meeting_todos, meeting_attendees,
                            summary_points, summary_topics, summary_agendas, meeting_summaries, meeting_sources;
                            ALTER TABLE meetings DROP COLUMN title, DROP COLUMN held_at, DROP COLUMN type_id;
                            DELETE FROM meeting_types;
                            ALTER TABLE folders ALTER COLUMN type_id SET NOT NULL;
                            DROP TABLE failed_exports, meeting_status_history;
                            ALTER TABLE meetings DROP COLUMN reviewer_id, DROP COLUMN submitted_at,
                                DROP COLUMN confirmed_at, DROP COLUMN confirmed_by;
                            ALTER TABLE meetings DROP CONSTRAINT meetings_status_check;
                            ALTER TABLE meetings ADD CONSTRAINT meetings_status_check
                                CHECK (status IN ('review', 'confirmed', 'sending', 'done', 'fail'));
                            ALTER TABLE integration_tools DROP COLUMN last_sync_at;
                            ALTER TABLE integration_status DROP COLUMN updated_by;
                            ALTER TABLE notifications DROP CONSTRAINT notifications_kind_check;
                            ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
                                CHECK (kind IN ('review', 'shared', 'integration', 'member'));
                            CREATE TABLE failed_exports (   -- 예전 모양: 도구 · 회의 한 쌍만
                                tool_id TEXT NOT NULL REFERENCES integration_tools(id) ON DELETE CASCADE,
                                meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                                PRIMARY KEY (tool_id, meeting_id))''')
            conn.execute('''INSERT INTO meeting_types (id,name,meta) VALUES
                            ('weekly','주간 회의','목업'), ('monthly','월간 회의','목업')''')
            conn.execute("INSERT INTO accounts (user_id,email) VALUES ('u1','u1@test.local')")
            conn.execute('''INSERT INTO folders (id,name,type_id,host_user_id) VALUES
                            ('rel','릴리즈','weekly','u1'), ('period-weekly','주차별','weekly','u1')''')
            conn.execute('''INSERT INTO meetings (id, folder_id, host_user_id) VALUES
                            ('in-proj','rel','u1'), ('in-period','period-weekly','u1'), ('no-folder',NULL,'u1')''')
            conn.execute("INSERT INTO integration_tools (id,mark,name,description) VALUES ('slack','S','Slack','')")
            conn.execute("INSERT INTO failed_exports VALUES ('slack','in-proj')")
        self._migrate(upto='003')
        out = self._migrate(upto='005')
        self.assertIn('APPLY 004_meeting_sources_summaries.sql', out)
        self.assertIn('APPLY 005_meeting_type_on_meeting.sql', out)
        with psycopg.connect(TEST_URL) as conn:
            rows = dict((r[0], r[1:]) for r in conn.execute(
                'SELECT id, type_id, folder_id, title FROM meetings').fetchall())
            self.assertEqual(rows, {'in-proj': ('weekly', 'rel', ''), 'in-period': ('weekly', None, ''),
                                    'no-folder': ('normal', None, '')})
            self.assertIsNotNone(conn.execute(
                "SELECT deleted_at FROM folders WHERE id='period-weekly'").fetchone()[0])
            self.assertEqual(conn.execute('SELECT count(*) FROM meeting_types').fetchone(), (3,))
            self.assertEqual(conn.execute(
                "SELECT name FROM meeting_types WHERE id='weekly'").fetchone(), ('주간 회의',))  # 기존 문구 유지

        # 2) 004 모양 요약 데이터 → 006 이 새 표로 옮기는지
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            sid = conn.execute("""INSERT INTO meeting_summaries (meeting_id, version, overview)
                                  VALUES ('in-proj', 1, '요약') RETURNING id""").fetchone()[0]
            conn.execute('''INSERT INTO summary_agendas (summary_id, position, title, background, decisions,
                                unresolved_issues, next_topics)
                            VALUES (%s, 0, '안건', '배경', '{결정1,결정2}', '{미해결}', '{}'),
                                   (%s, 1, '빈 안건', '', '{}', '{}', '{}')''', (sid, sid))
            conn.execute('''INSERT INTO summary_discussions (summary_id, agenda_position, position, subtopic,
                                content, evidence_line_ids) VALUES (%s, 0, 0, '소주제', '논의 내용', '{3,4}')''', (sid,))
            conn.execute('''INSERT INTO summary_action_items (summary_id, position, description, owner_raw, due_date)
                            VALUES (%s, 0, '할 일', 'A', '2026-11-20')''', (sid,))
        out = self._migrate()
        self.assertIn('APPLY 006_meeting_detail.sql', out)
        self.assertIn('APPLY 007_exports_status_history.sql', out)
        self.assertIn('APPLY 008_draft_review_flow.sql', out)
        with psycopg.connect(TEST_URL) as conn:   # 007: 예전 실패 기록에 id · 종류가 붙고, 상태 이력 시작점이 생김
            self.assertEqual(conn.execute("SELECT id LIKE 'f-%', kind, resolved_at FROM failed_exports").fetchall(),
                             [(True, 'simple', None)])
            self.assertEqual(conn.execute("SELECT count(*) FROM meeting_status_history "
                                          "WHERE from_status IS NULL AND to_status = 'review'").fetchone(), (3,))
        from followup.adapters.postgres.db import pool
        pool.cache_clear()
        from followup.adapters.postgres import detail
        d = detail.get_meeting_detail(actor_id='u1', meeting_id='in-proj')
        self.assertEqual(d['agendas'][0]['background'], [{'text': '배경', 'src': []}])
        self.assertEqual([x['text'] for x in d['agendas'][0]['decisions']], ['결정1', '결정2'])
        self.assertEqual(d['agendas'][0]['open_issues'], [{'text': '미해결', 'src': []}])
        self.assertEqual(d['agendas'][0]['topics'],
                         [{'title': '소주제', 'points': [{'kind': '정보', 'text': '논의 내용', 'src': ['3', '4']}]}])
        self.assertNotIn('topics', d['agendas'][1])                     # 내용 없는 안건은 상세 없음
        self.assertEqual([(t['text'], t['due']) for t in d['todos']], [('할 일', '11/20')])
        self.assertEqual(d['people'], ['u1'])                           # 주관자 = 참석자
        pool().close()
        pool.cache_clear()

        # 3) schema.sql 로 초기화한 뒤 migrate.py 를 다시 돌리면 001~006 이 다시 적용돼야 함
        with psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(schema)
        n_files = len(list((ROOT / 'migrations').glob('[0-9][0-9][0-9]_*.sql')))
        self.assertEqual(self._migrate().count('APPLY'), n_files)
        with psycopg.connect(TEST_URL) as conn:
            n = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgname='meetings_status_transition'").fetchone()
            self.assertEqual(n, (1,))
            self.assertIsNone(conn.execute("SELECT to_regclass('summary_discussions')").fetchone()[0])


if __name__ == '__main__':
    unittest.main()