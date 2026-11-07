"""외부 도구 전송 · Jira: 다시 보내기는 실패한 아이템만 (이미 만든 이슈를 또 만들지 않음). 가짜 백엔드 응답.

실행: apps/web 에서 python -m unittest discover -s tests
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import repository as repo  # noqa: E402
from state import export  # noqa: E402
from state.store import AppState, ExportRun  # noqa: E402

MID = "m1"  # 목업 회의 (액션 아이템 여러 건)


def fake_backend(sent, fail_keys=()):
    def export_jira(title, items):
        sent.append([i["key"] for i in items])
        rows = [{"key": i["key"], "ok": False, "error": "boom"} if i["key"] in fail_keys
                else {"key": i["key"], "ok": True, "issue_key": f"KAN-{n}", "assigned": True}
                for n, i in enumerate(items, 1)]
        return {"project_key": "KAN", "created": sum(r["ok"] for r in rows), "total": len(rows), "items": rows}
    return export_jira


def run(s, attempt, jira=None):
    s.export = ExportRun(meeting_id=MID, picked=("jira",), attempt=attempt, started_at=time.time() - 600,
                         fids=export.open_ids(s, MID), jira=jira)
    export.send_jira(s)
    export.sync(s)
    return export.progress(s)


class RetryOnlyFailed(unittest.TestCase):
    def test_retry_sends_only_failed_items(self):
        s, sent = AppState(), []
        keys = [i["key"] for i in export._jira_items(s, MID)]
        self.assertGreater(len(keys), 1)
        with patch.object(repo, "export_jira", fake_backend(sent, fail_keys={keys[1]})):
            p = run(s, 1)
        self.assertEqual(p.exports["jira"], "fail")
        with patch.object(repo, "export_jira", fake_backend(sent)):
            p = run(s, 2, jira=s.export.jira)
        self.assertEqual(sent, [keys, [keys[1]]])  # 두 번째는 실패한 아이템 하나만
        self.assertEqual(p.exports["jira"], "ok")
        self.assertEqual(s.export.jira["created"], len(keys))


if __name__ == "__main__":
    unittest.main()
