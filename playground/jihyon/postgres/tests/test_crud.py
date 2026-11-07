"""DB-free validation tests. Run with python -m unittest discover -s tests -v."""
import unittest
import sys
import types
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

from crud.accounts import _hash_password, _verify_password
from crud.common import ValidationError
from crud import meetings, shares, folders, notifications

class ValidationTests(unittest.TestCase):
    def test_password_hash_and_verify(self):
        h=_hash_password('correct-password-123')
        self.assertTrue(h.startswith('pbkdf2_sha256$'))
        self.assertNotIn('correct-password-123',h)
        self.assertTrue(_verify_password('correct-password-123',h))
        self.assertFalse(_verify_password('wrong-password',h))
        self.assertFalse(_verify_password('any',None))
    def test_short_password(self):
        with self.assertRaises(ValidationError): _hash_password('short')
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
        from crud.people import person_rows
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
