"""외부 도구 전송 · Jira · 메일: 다시 보내기는 실패한 아이템 · 사람만 (이미 만든 이슈 · 받은 사람에게 또 보내지 않음).
가짜 백엔드 응답.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import api, repository as repo  # noqa: E402
from state import export  # noqa: E402

from state.store import AppState, ExportRun  # noqa: E402

MID = "m1"  # 목업 회의 (액션 아이템 · 참석자 여러 명)


def rows(keys, fail_keys, ok_row):
    return [{"key": k, "ok": False, "error": "boom"} if k in fail_keys else {"key": k, "ok": True, **ok_row(n)}
            for n, k in enumerate(keys, 1)]


def fake_jira(sent, fail_keys=()):
    def export_jira(title, items):
        sent.append([i["key"] for i in items])
        return {"project_key": "KAN",
                "items": rows([i["key"] for i in items], fail_keys, lambda n: {"issue_key": f"KAN-{n}", "assigned": True})}
    return export_jira


def fake_mail(sent, fail_keys=()):
    def export_mail(title, body, to):
        sent.append(list(to))
        return {"items": rows(to, fail_keys, lambda n: {"sent": True})}
    return export_mail


def run(s, tool, attempt, prev=None):
    s.export = ExportRun(meeting_id=MID, picked=(tool,), attempt=attempt, started_at=time.time() - 600,
                         fids=export.open_ids(s, MID), results=prev or {})
    export.send(s)
    export.sync(s)
    return export.progress(s)


class RetryOnlyFailed(unittest.TestCase):
    def test_jira_retry_sends_only_failed_items(self):
        s, sent = AppState(), []
        keys = [i["key"] for i in export._jira_items(s, MID)]
        self.assertGreater(len(keys), 1)
        with patch.object(repo, "export_jira", fake_jira(sent, fail_keys={keys[1]})):
            p = run(s, "jira", 1)
        self.assertEqual(p.exports["jira"], "fail")
        with patch.object(repo, "export_jira", fake_jira(sent)):
            p = run(s, "jira", 2, prev=s.export.results)
        self.assertEqual(sent, [keys, [keys[1]]])  # 두 번째는 실패한 아이템 하나만
        self.assertEqual(p.exports["jira"], "ok")
        self.assertEqual(export.summary(s.export, "jira").split(" · ")[0], f"이슈 {len(keys)}/{len(keys)}건 만듦")

    def test_mail_retry_sends_only_failed_people(self):
        s, sent = AppState(), []
        s.final_summaries[MID] = {}  # 최종 회의록이 있어야 실제로 보냄 (본문은 아래에서 가짜로)
        to = export.mail_recipients(MID)
        self.assertEqual(to[0], repo.meeting(MID).host)  # 보내는 주관자도 사본을 받음
        self.assertGreater(len(to), 1)
        with patch("state.minutes_text.render_minutes", return_value="회의록"):
            with patch.object(repo, "export_mail", fake_mail(sent, fail_keys={to[0]})):
                p = run(s, "mail", 1)
            self.assertEqual(p.exports["mail"], "fail")
            with patch.object(repo, "export_mail", fake_mail(sent)):
                p = run(s, "mail", 2, prev=s.export.results)
        self.assertEqual(sent, [to, [to[0]]])  # 두 번째는 못 받은 사람 한 명만
        self.assertEqual(p.exports["mail"], "ok")
        self.assertEqual(export.summary(s.export, "mail"), f"메일 {len(to)}/{len(to)}명 보냄")

    def test_connection_failure_goes_to_admin_and_is_not_resent(self):
        s, calls = AppState(), []
        s.final_summaries[MID] = {}

        def broken(title, body, to):
            calls.append(to)
            raise api.ApiError("메일 서버에 연결하지 못했어요", 502)
        n_admin = len(repo.live_notifications("admin"))
        with patch("state.minutes_text.render_minutes", return_value="회의록"), \
                patch.object(repo, "export_mail", broken):
            p = run(s, "mail", 1)
            self.assertEqual(p.exports["mail"], "fail")
            rec = next(f for f in repo.export_failures(MID) if f.id == f"rt-{MID}-mail")
            self.assertEqual(rec.kind, "tech")  # 관리자 문의 (다시 보내기 없음)
            self.assertEqual(len(repo.live_notifications("admin")), n_admin + 1)
            s.export = ExportRun(meeting_id=MID, picked=("mail",), attempt=2, started_at=time.time() - 600,
                                 results=s.export.results, errors=s.export.errors)
            export.send(s)
        self.assertEqual(len(calls), 1)  # 다시 보내기에서 또 보내지 않음

    def test_mail_without_final_minutes_is_simulated(self):
        s = AppState()
        s.export = ExportRun(meeting_id=MID, picked=("mail",))
        self.assertFalse(export.live(s, "mail"))  # 목업 회의: 실제로 보내지 않음


if __name__ == "__main__":
    unittest.main()
