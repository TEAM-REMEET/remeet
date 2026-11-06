"""목업(JSON)만 쓸 때와 PostgreSQL 을 붙였을 때 화면에 가는 데이터가 똑같은지 비교.

    python seed.py      # 먼저 목업 데이터를 DB 에 넣고
    python check.py     # 모두 OK 면 성공

알림은 DB 에서 바뀌는 게 정상인 부분이 있어서 따로 봅니다.
  - 시간 표시("10분 전")   : DB 는 실제 시각을 저장해서, 시드 후 시간이 지나면 "1시간 전" 등으로 바뀜
  - 읽음 여부              : 앱에서 알림을 누르면 DB 에 저장됨 (목업은 그대로)
"""

import streamlit.logger

streamlit.logger.set_log_level("error")  # Streamlit 밖에서 돌릴 때 나오는 경고(missing ScriptRunContext) 숨김

import paths  # noqa: E402,F401

from data import repository as repo  # noqa: E402  web/data/repository.py

FUNCS = ("people", "people_by_short_name", "meetings", "meeting_types", "base_folders", "assignee_config",
         "integrations", "teams", "team_candidates", "received_meeting_ids", "notifications")


def snapshot() -> dict:
    return {name: getattr(repo, name)() for name in FUNCS}


def same(a, b) -> bool:
    return a == b and (not isinstance(a, dict) or list(a) == list(b))


before = snapshot()
import web_patch  # noqa: E402

web_patch.apply()
after = snapshot()

bad = 0
for name in FUNCS:
    a, b = before[name], after[name]
    if name != "notifications":
        ok = same(a, b)
        bad += not ok
        print(("OK   " if ok else "DIFF ") + name)
        continue

    # 알림: 내용(id · 종류 · 문구 · 연결된 회의/폴더)은 같아야 하고, 시간 표시 · 읽음은 달라도 정상
    def core(ns):
        return [(n.id, n.kind, n.text, n.meeting, n.folder) for n in ns]

    ok = sorted(core(a)) == sorted(core(b))
    bad += not ok
    print(("OK   " if ok else "DIFF ") + name)
    if not ok:
        print("     목업:", sorted(core(a)))
        print("     DB  :", sorted(core(b)))
    mock = {n.id: n for n in a}
    for n in b:
        m = mock.get(n.id)
        if m and m.when != n.when:
            print(f"     · {n.id} 시간 표시: 목업 '{m.when}' → DB '{n.when}'  (시드 후 시간이 지나서 · 정상)")
        if m and m.unread != n.unread:
            print(f"     · {n.id} 읽음: 목업 {'안 읽음' if m.unread else '읽음'} → DB {'안 읽음' if n.unread else '읽음'}"
                  "  (앱에서 알림을 눌러 DB 에 저장됨 · 정상)")

print("✅ 목업과 똑같아요" if not bad else f"❌ {bad}개가 달라요 — python seed.py 로 다시 넣고 확인해 보세요")