"""POST /api/exports/mail · 외부 도구 전송 메일 (가짜 SMTP · 실제 메일 없음)"""
import smtplib
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.adapters import smtp_mail
from followup.application import export_mail
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user

ENV = {"SMTP_HOST": "smtp.x.com", "SMTP_USERNAME": "bot@x.com", "SMTP_PASSWORD": "p", "MAIL_FROM": "bot@x.com",
       "SMTP_PORT": ""}
EMAILS = {"u1": "a@x.com", "u2": "b@x.com"}


class FakeSmtp:
    def __init__(self, fail_to=()):
        self.fail_to, self.sent, self.closed = set(fail_to), [], False

    def send_message(self, msg):
        if msg["To"] in self.fail_to:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no")})
        self.sent.append(msg)

    def quit(self):
        self.closed = True


def send(fake, recipients, title="주간 회의"):
    return export_mail.export(title, "회의록 본문", recipients, connect=lambda cfg: fake, emails=lambda ids: EMAILS)


class ExportMailTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict("os.environ", ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_not_configured(self):
        with patch.dict("os.environ", {"MAIL_FROM": ""}):
            with self.assertRaises(export_mail.MailNotConfigured):
                send(FakeSmtp(), ["u1"])

    def test_per_person_results(self):
        fake = FakeSmtp(fail_to={"b@x.com"})
        res = send(fake, ["u1", "u2", "u9", "u1"])  # u9: 메일 주소 없음 · u1 중복은 한 번만
        self.assertEqual(res["items"], [{"key": "u1", "ok": True, "sent": True},
                                        {"key": "u2", "ok": False, "error": res["items"][1]["error"]},
                                        {"key": "u9", "ok": True, "sent": False}])
        self.assertEqual([m["To"] for m in fake.sent], ["a@x.com"])
        self.assertTrue(fake.closed)
        self.assertEqual(fake.sent[0]["Subject"], "[RE:MEET] 회의 요약: 주간 회의")

    def test_subject_newline_cannot_inject_headers(self):
        fake = FakeSmtp()
        send(fake, ["u1"], title="회의\r\nBcc: evil@x.com")
        self.assertIsNone(fake.sent[0]["Bcc"])
        self.assertNotIn("\n", fake.sent[0]["Subject"])

    def test_login_failure_is_unavailable(self):
        def broken(cfg):
            raise smtplib.SMTPAuthenticationError(535, b"bad password")
        with self.assertRaises(export_mail.MailUnavailable):
            export_mail.export("회의", "본문", ["u1"], connect=broken, emails=lambda ids: EMAILS)

    def test_over_budget_leaves_rest_for_retry(self):
        fake = FakeSmtp()
        with patch("followup.settings.EXPORT_BUDGET_SECONDS", -1):
            res = send(fake, ["u1", "u2"])
        self.assertEqual(fake.sent, [])
        self.assertTrue(all(not r["ok"] and "시간 초과" in r["error"] for r in res["items"]))


class MailRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def login(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}

    def test_requires_login(self):
        self.assertEqual(self.client.post("/api/exports/mail", json={}).status_code, 401)

    def test_bad_input(self):
        self.login()
        r = self.client.post("/api/exports/mail", json={"meeting_title": "회의", "body": "본문", "recipients": []})
        self.assertEqual(r.status_code, 422)

    def test_ok_and_errors_hide_internals(self):
        self.login()
        body = {"meeting_title": "회의", "body": "본문", "recipients": ["u1"]}
        with patch.object(export_mail, "export", return_value={"items": [{"key": "u1", "ok": True, "sent": True}]}):
            self.assertEqual(self.client.post("/api/exports/mail", json=body).json()["items"][0]["sent"], True)
        with patch.object(export_mail, "export", side_effect=export_mail.MailNotConfigured("x")):
            self.assertEqual(self.client.post("/api/exports/mail", json=body).status_code, 503)
        with patch.object(export_mail, "export", side_effect=export_mail.MailUnavailable("password=secret")), \
                self.assertLogs("followup.entrypoints.api.export_routes", "ERROR"):
            r = self.client.post("/api/exports/mail", json=body)
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("secret", r.text)


class MessageTests(unittest.TestCase):
    def test_connect_closes_on_login_failure(self):
        smtp = unittest.mock.MagicMock()
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
        with patch("smtplib.SMTP", return_value=smtp):
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                smtp_mail.connect(ENV | {"SMTP_PORT": "587"})
        smtp.starttls.assert_called_once()
        smtp.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
