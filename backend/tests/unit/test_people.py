import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from fastapi.testclient import TestClient

from followup import settings
from followup.adapters.neo4j import people
from followup.domain.passwords import hash_password, verify_password
from followup.domain.tokens import make_token, read_token
from followup.entrypoints.api.main import app
from followup.entrypoints.seed_people import SEED_DIR, read_org

PERSON = {"user_id": "u1", "name": "바트", "email": None, "permission": "member",
          "position": "senior", "is_active": True, "team_id": "t-pm",
          "team": "프로젝트 관리팀", "role_id": "r-pm", "job": "프로젝트 매니저",
          "skills": [{"skill_id": "PM-01", "name": "일정·마일스톤 계획", "level": 4}]}


SECRET = "test-secret"
settings.AUTH_SECRET = SECRET  # 테스트 전용 서명 키


def auth(permission: str) -> dict:
    token = make_token({"user_id": "x", "name": "x", "email": "x@x.kr", "permission": permission}, SECRET, 60)
    return {"Authorization": f"Bearer {token}"}


ADMIN = auth("admin")


class CheckOrgTests(unittest.TestCase):
    def test_seed_files_are_consistent(self):
        self.assertEqual(people.check_org(read_org(SEED_DIR)), [])

    def test_reports_missing_references_and_duplicates(self):
        org = {"teams": [{"team_id": "t"}], "roles": [{"role_id": "r"}], "skills": [{"skill_id": "s"}],
               "people": [{"user_id": "u1", "team_id": "t", "role_id": "x", "skills": [{"skill_id": "nope"}]},
                          {"user_id": "u1", "team_id": "t", "role_id": "r", "skills": []}]}
        self.assertEqual(people.check_org(org), ["u1: 없는 직무 x", "u1: 없는 스킬 nope", "u1: user_id 중복"])

    def test_upsert_refuses_bad_data_before_touching_db(self):
        org = {"teams": [], "roles": [], "skills": [], "people": [{"user_id": "u1", "skills": []}]}
        with patch.object(people, "get_driver") as driver:
            with self.assertRaises(ValueError):
                people.upsert_org(org)
            driver.assert_not_called()


class PasswordTests(unittest.TestCase):
    def test_hash_verifies_only_the_right_password(self):
        h = hash_password("s3cret!")
        self.assertTrue(verify_password("s3cret!", h))
        self.assertFalse(verify_password("s3cret", h))
        self.assertNotIn("s3cret", h)
        self.assertNotEqual(h, hash_password("s3cret!"))  # salt가 달라 같은 비밀번호도 해시가 다름

    def test_broken_or_missing_hash_is_rejected(self):
        for stored in (None, "", "plain", "md5$1$2$3$aa$bb", "scrypt$x$8$1$zz$zz"):
            self.assertFalse(verify_password("x", stored))

    def test_seed_accounts_are_hashed_and_unique(self):
        rows = read_org(SEED_DIR)["people"]
        self.assertTrue(all(p["password_hash"].startswith("scrypt$") for p in rows))
        self.assertEqual(len({p["email"] for p in rows}), len(rows))

    def test_password_hash_never_leaves_the_api(self):
        self.assertNotIn("password_hash", people.READ_PEOPLE)
        with patch.object(people, "list_people", return_value=[{**PERSON, "password_hash": "scrypt$leak"}]):
            self.assertNotIn("password_hash", TestClient(app).get("/api/people").text)


class TokenTests(unittest.TestCase):
    def test_roundtrip_tamper_and_expiry(self):
        t = make_token({"user_id": "u1", "permission": "member"}, SECRET, 60, now=1000)
        self.assertEqual(read_token(t, SECRET, now=1000)["user_id"], "u1")
        self.assertIsNone(read_token(t, "other-secret", now=1000))
        self.assertIsNone(read_token(t, SECRET, now=1061))  # 만료
        body, sig = t.split(".")
        forged = make_token({"user_id": "u1", "permission": "admin"}, "guess", 60, now=1000).split(".")[0]
        self.assertIsNone(read_token(f"{forged}.{sig}", SECRET, now=1000))  # 권한을 바꿔 치면 서명이 안 맞음
        for junk in ("", "abc", "a.b.c", "!!.!!"):
            self.assertIsNone(read_token(junk, SECRET))

    def test_no_secret_refuses(self):
        with self.assertRaises(RuntimeError):
            make_token({}, "", 60)
        self.assertIsNone(read_token(make_token({}, SECRET, 60), ""))


class AuthRouteTests(unittest.TestCase):
    client = TestClient(app)
    ACCOUNT = {"user_id": "u1", "name": "바트", "email": "bart@remeet.example",
               "password_hash": hash_password("pw-123"), "permission": "member", "is_active": True}

    def login(self, email, password, account=ACCOUNT):
        with patch.object(people, "find_login", return_value=account) as find:
            response = self.client.post("/api/auth/login", json={"email": email, "password": password})
        return response, find

    def test_login_ok_returns_token_without_hash(self):
        response, find = self.login(" Bart@Remeet.Example ", "pw-123")
        self.assertEqual(response.status_code, 200)
        find.assert_called_once_with("bart@remeet.example")
        self.assertNotIn("password_hash", response.text)
        me = self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {response.json()['token']}"})
        self.assertEqual(me.json()["user_id"], "u1")

    def test_wrong_password_and_unknown_email_look_the_same(self):
        wrong, _ = self.login("bart@remeet.example", "nope")
        unknown, _ = self.login("who@remeet.example", "pw-123", account=None)
        self.assertEqual((wrong.status_code, wrong.json()), (unknown.status_code, unknown.json()))
        self.assertEqual(wrong.status_code, 401)

    def test_inactive_account_is_refused(self):
        response, _ = self.login("bart@remeet.example", "pw-123", account={**self.ACCOUNT, "is_active": False})
        self.assertEqual(response.status_code, 403)

    def test_me_needs_valid_token(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me", headers={"Authorization": "Bearer junk"}).status_code, 401)

    def test_people_writes_need_admin(self):
        with patch.object(people, "update_person") as update, patch.object(people, "create_person") as create:
            self.assertEqual(self.client.patch("/api/people/u1", json={"is_active": False}).status_code, 401)
            self.assertEqual(self.client.patch("/api/people/u1", json={"is_active": False},
                                               headers=auth("member")).status_code, 403)
            self.assertEqual(self.client.post("/api/people", json=PeopleRouteTests.NEW,
                                              headers=auth("member")).status_code, 403)
            update.assert_not_called()
            create.assert_not_called()


class PeopleRouteTests(unittest.TestCase):
    client = TestClient(app)

    def test_list(self):
        with patch.object(people, "list_people", return_value=[PERSON]):
            response = self.client.get("/api/people")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [PERSON])

    def test_get_missing_is_404(self):
        with patch.object(people, "get_person", return_value=None):
            self.assertEqual(self.client.get("/api/people/u999").status_code, 404)

    NEW = {"name": "새사람", "email": "new@x.kr", "team_id": "t-pm", "job": "프로젝트 매니저", "position": "mid",
           "skills": [{"skill_id": "PM-01", "level": 3}]}

    def test_create_passes_validated_body(self):
        with patch.object(people, "create_person", return_value=PERSON) as create:
            response = self.client.post("/api/people", json=self.NEW, headers=ADMIN)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(create.call_args.args[0]["permission"], "member")

    def test_create_rejects_bad_input_before_db(self):
        bad = [{**self.NEW, "email": "no-at"}, {**self.NEW, "position": "boss"},
               {**self.NEW, "skills": [{"skill_id": "PM-01", "level": 5}]}, {k: v for k, v in self.NEW.items() if k != "position"}]
        with patch.object(people, "create_person") as create:
            for body in bad:
                self.assertEqual(self.client.post("/api/people", json=body, headers=ADMIN).status_code, 422, body)
            create.assert_not_called()

    def test_create_maps_errors(self):
        for error, code in ((people.Conflict("이메일 중복"), 409), (ValueError("없는 팀"), 400)):
            with patch.object(people, "create_person", side_effect=error):
                self.assertEqual(self.client.post("/api/people", json=self.NEW, headers=ADMIN).status_code, code)

    def test_update_sends_only_given_fields(self):
        with patch.object(people, "update_person", return_value=PERSON) as update:
            self.client.patch("/api/people/u1", json={"is_active": False, "name": None}, headers=ADMIN)
        update.assert_called_once_with("u1", {"is_active": False})

    def test_update_missing_is_404(self):
        with patch.object(people, "update_person", return_value=None):
            self.assertEqual(self.client.patch("/api/people/u999", json={"is_active": False}, headers=ADMIN).status_code, 404)


if __name__ == "__main__":
    unittest.main()
