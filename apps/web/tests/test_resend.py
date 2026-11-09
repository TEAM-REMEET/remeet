"""외부 도구 전송 「다시 보내기」 (state/export.resend_failure): 실패한 것만 · 저장 안 된 할 일은 안 보냄 · 기록 순서.
목업 회의 대신 가짜 회의 · 할 일 · 백엔드 응답을 넣어요 (서버 없이).

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402
from data.models import ExportFailure, Todo  # noqa: E402
from state import export  # noqa: E402
from state import selectors as sel  # noqa: E402
from state.store import AppState  # noqa: E402

MEETING = SimpleNamespace(title="주간 회의", host="u1", people=("u1", "u2", "u3"))
TODOS = [(0, Todo("견적 비교", ("u2",), "11/10", (), todo_id=41)), (1, Todo("디자인 검토", (), "", (), todo_id=42)),
         (2, Todo("아직 저장 안 됨", (), "", ()))]  # todo_id 0


def failure(tool, keys=None):
    return ExportFailure("f-1", tool, "M_1", "simple", "사유", "", "11/09 10:00", "주간 회의", keys)


class ResendTests(unittest.TestCase):
    def setUp(self):
        self.calls = []  # (무엇을, 인자) 순서대로
        for target, name, fn in [
            (repo, "meeting", lambda mid: MEETING),
            (sel, "todo_items", lambda state, mid: TODOS),
            (sel, "todo_owner_ids", lambda state, key, t: t.owners),
            (repo, "record_failure", lambda *a: self.calls.append(("record", a))),
            (repo, "resolve_failure", lambda fid: self.calls.append(("resolve", fid))),
        ]:
            p = patch.object(target, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def jira(self, fail_keys=()):
        def export_jira(title, items):
            self.calls.append(("jira", [i["key"] for i in items]))
            return {"project_key": "KAN", "items": [{"key": i["key"], "ok": i["key"] not in fail_keys, "error": "boom"}
                                                    for i in items]}
        return patch.object(repo, "export_jira", export_jira)

    def test_sends_only_failed_keys_then_resolves(self):
        with self.jira():
            left = export.resend_failure(AppState(), failure("jira", ("42",)))
        self.assertEqual(left, "")
        self.assertEqual(self.calls, [("jira", ["42"]), ("resolve", "f-1")])  # 41 은 다시 보내지 않음

    def test_still_failing_records_new_before_resolving_old(self):
        with self.jira(fail_keys={"42"}):
            left = export.resend_failure(AppState(), failure("jira"))  # keys 없음 → 전부 (중복은 서버가 라벨로 막음)
        self.assertIn("2건을", left)  # 42 + 저장 안 된 할 일
        self.assertEqual(self.calls[0], ("jira", ["41", "42"]))  # 저장 안 된 할 일(id 0)은 보내지 않음
        record = self.calls[1]
        self.assertEqual((record[0], record[1][-1]), ("record", ["42", "unsaved-2"]))  # 남은 것만 새 기록
        self.assertEqual(self.calls[2], ("resolve", "f-1"))  # 새 기록 뒤에 해결 → 회의가 잠깐도 done 이 되지 않음

    def test_mail_uses_saved_minutes_when_session_has_none(self):
        sent = []
        with patch.object(repo, "summary_json", lambda mid: {"saved": True}) as _, \
                patch("state.minutes_text.render_minutes", lambda m: "회의록" if m == {"saved": True} else "?"), \
                patch.object(repo, "export_mail", lambda title, body, to: sent.append((body, to)) or
                             {"items": [{"key": u, "ok": True, "sent": True} for u in to]}):
            self.assertEqual(export.resend_failure(AppState(), failure("mail", ("u3",))), "")
        self.assertEqual(sent, [("회의록", ["u3"])])  # 못 받은 사람만 · 본문은 서버에 저장된 최종 회의록


if __name__ == "__main__":
    unittest.main()
