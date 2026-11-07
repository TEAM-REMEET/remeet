"""RE:MEET PostgreSQL integration tests.

ONLY targets a disposable database named *test. Never point at production.
Run from postgres/ directory:
  $env:TEST_DATABASE_URL='postgresql://remeet:remeet@127.0.0.1:5432/remeet_test'
  uv run python -m unittest discover -s tests -p test_integration_pg.py -v
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

TEST_URL = os.environ.get('TEST_DATABASE_URL', '')


def _safe_test_database(url: str) -> bool:
    if not url:
        return False
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(url)
    dbname = params.get('dbname', '')
    return dbname.endswith('_test') and dbname != 'remeet'


@unittest.skipUnless(TEST_URL, 'TEST_DATABASE_URL 환경변수가 설정되지 않았습니다.')
class PostgresCRUDIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        if not _safe_test_database(TEST_URL):
            raise RuntimeError('안전을 위해 이름이 _test 로 끝나는 별도 데이터베이스만 허용합니다.')
        os.environ['DATABASE_URL'] = TEST_URL
        from db import pool
        pool.cache_clear()
        from db import transaction
        cls.transaction = staticmethod(transaction)
        cls.psycopg = psycopg
        # Schema uses DROP TABLE: this is safe ONLY inside the guarded *test database.
        schema = Path(__file__).resolve().parents[1] / 'schema.sql'
        with transaction() as cur:
            cur.execute(schema.read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        from db import pool
        if pool.cache_info().currsize:
            pool().close()
            pool.cache_clear()

    def setUp(self):
        with self.transaction() as cur:
            cur.execute('''TRUNCATE notifications, meeting_shares, transcript_lines,
                        meeting_keywords, meeting_speakers, meeting_analyses,
                        meetings, folder_briefs, folder_members, folders,
                        meeting_types, accounts CASCADE''')
            cur.execute("""INSERT INTO meeting_types (id,name,meta) VALUES
                      ('normal','일반회의','테스트')""")
            cur.execute("""INSERT INTO accounts (user_id,email,role) VALUES
                       ('u1','u1@test.local','USER'),
                       ('u2','u2@test.local','USER'),
                       ('u3','u3@test.local','USER'),
                       ('u4','u4@test.local','ADMIN')""")

    def _folder(self, members=None):
        from crud.folders import create_folder
        return create_folder(actor_id='u1', name='주간회의', type_id='normal',
                             members=members or [])

    def _meeting(self, folder_id=None):
        from crud.meetings import create_meeting
        return create_meeting(actor_id='u1', meeting_id='m-test-001', folder_id=folder_id)

    def test_folder_lifecycle_and_membership(self):
        from crud import folders
        from crud.common import NotFoundError, PermissionDenied
        f = self._folder(['u2'])
        self.assertEqual(folders.get_folder(actor_id='u2',folder_id=f['id'])['name'],'주간회의')
        with self.assertRaises(PermissionDenied):
            folders.get_folder(actor_id='u3',folder_id=f['id'])
        modified=folders.update_folder(actor_id='u1',folder_id=f['id'],name='수정된 폴더')
        self.assertEqual(modified['name'],'수정된 폴더')
        folders.remove_member(actor_id='u1',folder_id=f['id'],user_id='u2')
        with self.assertRaises(PermissionDenied):
            folders.get_folder(actor_id='u2',folder_id=f['id'])
        folders.delete_folder(actor_id='u1',folder_id=f['id'])
        with self.assertRaises(NotFoundError):
            folders.get_folder(actor_id='u1',folder_id=f['id'])

    def test_meeting_lifecycle_and_transcript(self):
        from crud import meetings
        from crud.common import NotFoundError
        f=self._folder()
        m=self._meeting(f['id'])
        self.assertEqual(meetings.get_meeting(actor_id='u1',meeting_id=m['id'])['status'],'review')
        self.assertEqual(meetings.replace_transcript(actor_id='u1',meeting_id=m['id'],lines=[
            {'line_id':'1','speaker':'A','text':'회의 시작'},
            {'line_id':'2','speaker':'B','text':'네'}]),2)
        self.assertEqual(len(meetings.get_transcript(actor_id='u1',meeting_id=m['id'])),2)
        updated=meetings.update_meeting(actor_id='u1',meeting_id=m['id'],status='confirmed')
        self.assertEqual(updated['status'],'confirmed')
        meetings.delete_meeting(actor_id='u1',meeting_id=m['id'])
        with self.assertRaises(NotFoundError):
            meetings.get_meeting(actor_id='u1',meeting_id=m['id'])

    def test_summary_share_cannot_read_raw_and_revocation(self):
        from crud import meetings, shares
        from crud.common import PermissionDenied
        m=self._meeting()
        meetings.replace_transcript(actor_id='u1',meeting_id=m['id'],lines=[
            {'line_id':'1','text':'비공개 발화'}])
        share=shares.create_share(actor_id='u1',meeting_id=m['id'],recipient_user_id='u2',scope='summary')
        self.assertEqual(meetings.get_meeting(actor_id='u2',meeting_id=m['id'])['id'],m['id'])
        with self.assertRaises(PermissionDenied):
            meetings.get_transcript(actor_id='u2',meeting_id=m['id'])
        shares.update_share(actor_id='u1',share_id=share['id'],scope='full',lock_raw=True)
        with self.assertRaises(PermissionDenied):
            meetings.get_transcript(actor_id='u2',meeting_id=m['id'])
        shares.update_share(actor_id='u1',share_id=share['id'],scope='full',lock_raw=False)
        self.assertEqual(len(meetings.get_transcript(actor_id='u2',meeting_id=m['id'])),1)
        shares.revoke_share(actor_id='u1',share_id=share['id'])
        with self.assertRaises(PermissionDenied):
            meetings.get_meeting(actor_id='u2',meeting_id=m['id'])

    def test_summary_share_overrides_folder_membership_for_raw(self):
        """Explicit summary-only share must not be bypassed by folder membership."""
        from crud import meetings, shares
        from crud.common import PermissionDenied
        f=self._folder(['u2'])
        m=self._meeting(f['id'])
        meetings.replace_transcript(actor_id='u1',meeting_id=m['id'],lines=[
            {'line_id':'1','text':'비공개 발화'}])
        shares.create_share(actor_id='u1',meeting_id=m['id'],recipient_user_id='u2',scope='summary')
        with self.assertRaises(PermissionDenied):
            meetings.get_transcript(actor_id='u2',meeting_id=m['id'])

    def test_admin_denied_all_meeting_and_folder_access(self):
        from crud import folders, meetings
        from crud.common import PermissionDenied
        f=self._folder()
        m=self._meeting(f['id'])
        for call in (
            lambda: folders.get_folder(actor_id='u4', folder_id=f['id']),
            lambda: folders.list_folders(actor_id='u4'),
            lambda: folders.create_folder(actor_id='u4', name='관리자폴더',type_id='normal'),
            lambda: meetings.get_meeting(actor_id='u4',meeting_id=m['id']),
            lambda: meetings.list_meetings(actor_id='u4'),
            lambda: meetings.get_transcript(actor_id='u4',meeting_id=m['id']),
            lambda: meetings.create_meeting(actor_id='u4',meeting_id='admin-meeting'),
        ):
            with self.subTest(call=repr(call)):
                with self.assertRaises(PermissionDenied):
                    call()

    def test_other_user_cannot_modify_host_meeting(self):
        from crud import meetings, shares
        from crud.common import PermissionDenied
        m=self._meeting()
        shares.create_share(actor_id='u1',meeting_id=m['id'],recipient_user_id='u2',scope='full')
        for call in (
            lambda: meetings.update_meeting(actor_id='u2',meeting_id=m['id'],status='done'),
            lambda: meetings.delete_meeting(actor_id='u2',meeting_id=m['id']),
            lambda: shares.create_share(actor_id='u2',meeting_id=m['id'],recipient_user_id='u3'),
        ):
            with self.subTest(call=repr(call)):
                with self.assertRaises(PermissionDenied):
                    call()

    def test_notifications_lifecycle_and_isolation(self):
        from crud import notifications
        from crud.common import PermissionDenied
        n=notifications.create_notification(actor_id='u1',user_id='u1',kind='review',text='검토 요청')
        self.assertEqual(len(notifications.list_notifications(actor_id='u1')),1)
        self.assertEqual(len(notifications.list_notifications(actor_id='u2')),0)
        with self.assertRaises(PermissionDenied):
            notifications.set_read(actor_id='u2',notification_id=n['id'])
        changed=notifications.set_read(actor_id='u1',notification_id=n['id'])
        self.assertTrue(changed['is_read'])
        self.assertEqual(len(notifications.list_notifications(actor_id='u1',unread_only=True)),0)
        notifications.delete_notification(actor_id='u1',notification_id=n['id'])
        self.assertEqual(len(notifications.list_notifications(actor_id='u1')),0)

    def test_transcript_duplicate_ids_leave_existing_data_unchanged(self):
        from crud import meetings
        from crud.common import ValidationError
        m=self._meeting()
        meetings.replace_transcript(actor_id='u1',meeting_id=m['id'],lines=[
            {'line_id':'original','text':'기존 내용'}])
        with self.assertRaises(ValidationError):
            meetings.replace_transcript(actor_id='u1',meeting_id=m['id'],lines=[
                {'line_id':'x','text':'A'}, {'line_id':'x','text':'B'}])
        original=meetings.get_transcript(actor_id='u1',meeting_id=m['id'])
        self.assertEqual([p['line_id'] for p in original],['original'])

    def test_people_sync_add_update_deactivate(self):
        from crud.people import sync_people
        with self.transaction() as cur:
            cur.execute("INSERT INTO teams (team_id,name) VALUES ('t-pm','관리팀') ON CONFLICT DO NOTHING")
            cur.execute("""INSERT INTO skills (skill_id,name,domain) VALUES ('PM-01','일정','PM'),('PM-02','배분','PM')
                           ON CONFLICT DO NOTHING""")
        p={'user_id':'u90','name':'새사람','email':'u90@test.local','permission':'member','position':'junior',
           'is_active':True,'team_id':'t-pm','role_id':'r-new','job':'새 직무',
           'skills':[{'skill_id':'PM-01','level':2},{'skill_id':'PM-02','level':3}]}
        sync_people([p])  # 추가: 계정 · 새 직무 · 프로필 · 스킬
        sync_people([{**p,'position':'mid','is_active':False,'skills':[{'skill_id':'PM-02','level':4}]}])  # 수정
        with self.transaction() as cur:
            cur.execute("SELECT position,is_active FROM people WHERE user_id='u90'")
            self.assertEqual(cur.fetchone(),{'position':'mid','is_active':False})
            cur.execute("SELECT skill_id,level FROM person_skills WHERE user_id='u90'")
            self.assertEqual(cur.fetchall(),[{'skill_id':'PM-02','level':4}], '목록에서 빠진 스킬은 지운다')
            cur.execute("SELECT team_id FROM roles WHERE role_id='r-new'")
            self.assertEqual(cur.fetchone()['team_id'],'t-pm')

    def test_transaction_rolls_back_on_sql_error(self):
        from psycopg.errors import UniqueViolation
        with self.assertRaises(UniqueViolation):
            with self.transaction() as cur:
                cur.execute("INSERT INTO meeting_types (id,name,meta) VALUES ('new','테스트','test')")
                cur.execute("INSERT INTO meeting_types (id,name,meta) VALUES ('new','중복','test')")
        with self.transaction() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM meeting_types WHERE id='new'")
            self.assertEqual(cur.fetchone()['n'],0)


if __name__ == '__main__':
    unittest.main()
