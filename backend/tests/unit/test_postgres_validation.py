"""PostgreSQL 어댑터 입력 검사 (DB 없이). backend 폴더에서: python -m unittest discover -s tests/unit -p test_postgres_validation.py -v"""
import unittest
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# Unit tests do not open database connections; only stub optional driver imports.
try:
    import psycopg
except ImportError:
    pkg=types.ModuleType('psycopg')
    rows=types.ModuleType('psycopg.rows')
    rows.dict_row=object()
    pkg.rows=rows
    sys.modules['psycopg']=pkg
    sys.modules['psycopg.rows']=rows
    pool=types.ModuleType('psycopg_pool')
    pool.ConnectionPool=type('ConnectionPool',(),{})
    pool.PoolTimeout=type('PoolTimeout',(Exception,),{})
    sys.modules['psycopg_pool']=pool
from unittest.mock import MagicMock, patch
from contextlib import contextmanager

from followup.adapters.postgres.common import ValidationError
from followup.adapters.postgres import meetings, shares, notifications

class ValidationTests(unittest.TestCase):
    def test_bad_meeting_status(self):
        with self.assertRaises(ValidationError):
            meetings.create_meeting(actor_id='u1',meeting_id='m1',status='INVALID')
    def test_no_meeting_updates(self):
        with self.assertRaises(ValidationError): meetings.update_meeting(actor_id='u1',meeting_id='m1')
    def test_invalid_share_recipient(self):
        with self.assertRaises(ValidationError): shares.create_share(actor_id='u1',meeting_id='m1')
        with self.assertRaises(ValidationError):
            shares.create_share(actor_id='u1',meeting_id='m1',recipient_user_id='u2',recipient_team_id='t1')
    def test_invalid_notification_kind(self):
        with self.assertRaises(ValidationError):
            notifications.create_notification(actor_id='u1',user_id='u1',kind='bad',text='hello')
    def test_duplicate_transcript_ids(self):
        with self.assertRaises(ValidationError):
            meetings.replace_transcript(actor_id='u1',meeting_id='m1',lines=[
                {'line_id':'a','text':'first'}, {'line_id':'a','text':'second'}])
    def test_person_rows_from_backend_person(self):
        from followup.adapters.postgres.people import person_rows
        p={'user_id':'u49','name':'새사람','email':'new@remeet.example','permission':'member',
           'position':'junior','is_active':None,'team_id':'t-pm','role_id':'r-x','job':'기획자',
           'skills':[{'skill_id':'PM-01','name':'일정','level':2}]}
        r=person_rows(p)
        self.assertEqual(r['account'],('u49','new@remeet.example','USER'))
        self.assertEqual(r['role'],('r-x','기획자','t-pm'))
        self.assertTrue(r['person'][-1], 'is_active 가 없으면 활성으로')
        self.assertEqual(r['skills'],[('u49','PM-01',2)])
        self.assertEqual(person_rows({**p,'permission':'admin'})['account'][2],'ADMIN')
        for bad in ({'email':None},{'team_id':None},{'position':'intern'}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                person_rows({**p,**bad})

if __name__=='__main__': unittest.main()
