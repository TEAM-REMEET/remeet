"""목업(JSON)만 쓸 때와 PostgreSQL 을 붙였을 때 화면에 가는 데이터가 똑같은지 비교.

    python seed.py      # 먼저 목업 데이터를 DB 에 넣고
    python check.py     # 모두 OK 면 성공
"""

import paths  # noqa: F401

from data import repository as repo  # web/data/repository.py

FUNCS = ("people", "people_by_short_name", "meetings", "meeting_types", "base_folders", "assignee_config",
         "integrations", "teams", "team_candidates", "received_meeting_ids")


def snapshot() -> dict:
    out = {name: getattr(repo, name)() for name in FUNCS}
    out["notifications"] = [(n.id, n.kind, n.text, n.meeting, n.unread, n.folder, n.when) for n in repo.notifications()]
    return out


before = snapshot()
import web_patch  # noqa: E402

web_patch.apply()
after = snapshot()

bad = 0
for name in before:
    same = before[name] == after[name] and (not isinstance(before[name], dict) or list(before[name]) == list(after[name]))
    bad += not same
    print(("OK   " if same else "DIFF ") + name)
print("✅ 목업과 똑같아요" if not bad else f"❌ {bad}개가 달라요")
