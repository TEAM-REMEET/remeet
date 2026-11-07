"""목업 JSON(web/data/mock/*.json) → PostgreSQL (개발 · 테스트용 빈 DB).

    python seed.py      # ① schema.sql 로 표를 새로 만들고 ② migrations/ 전부 적용 ③ 목업 데이터를 넣음

⚠️ 표를 전부 지우고 다시 만들어요. 쓰던 DB 에는 돌리지 말고 python migrate.py 만 쓰세요.
구성원(사람 · 팀 · 직무)은 구성원 표(팀원 담당)라 넣지 않아요 — 권한 확인용 accounts 에 id 만 넣어요.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paths  # noqa: F401  (web 폴더 찾기 · .env 읽기 — 꼭 맨 먼저)
from config import YEAR  # web/config.py
from db import database_url, transaction
from crud.detail import insert_summary

# 지금 web 은 로그인한 사람을 백엔드(Neo4j)에서 받아서 config 에 CURRENT_USER_ID 가 없어요.
# 목업 회의 · 알림 · 공유받은 회의의 "나" = 목업 기준 사용자 (예전 config.CURRENT_USER_ID 값). 바꾸려면 .env 에 SEED_USER_ID=
SEED_USER_ID = os.environ.get("SEED_USER_ID", "yun")

MOCK = paths.WEB_DIR / "data" / "mock"
SCHEMA = Path(__file__).with_name("schema.sql")


def load(name: str):
    with open(MOCK / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def md_to_date(md: str | None) -> date | None:
    """'10/27' → date(2026, 10, 27). 형식이 다르면 None."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", md or "")
    return date(YEAR, int(m[1]), int(m[2])) if m else None


def relative_to_time(label: str, now: datetime) -> datetime:
    """알림의 '10분 전' · '1시간 전' · '어제' · '10/30' → 실제 시각."""
    if m := re.fullmatch(r"(\d+)분 전", label):
        return now - timedelta(minutes=int(m[1]))
    if m := re.fullmatch(r"(\d+)시간 전", label):
        return now - timedelta(hours=int(m[1]))
    if label == "어제":
        return now - timedelta(days=1)
    if d := md_to_date(label):
        return datetime(d.year, d.month, d.day, 9, tzinfo=timezone.utc)
    return now


def seed() -> None:
    folders = load("folders")
    meetings = load("meetings")
    cands = load("candidates")
    integ = load("integrations")
    notis = load("notifications")
    # 목업에 나오는 사람 id 전부 (사람 정보 자체는 Neo4j 몫이라 여기서는 id 만)
    member_ids = list(dict.fromkeys(
        [SEED_USER_ID]
        + [u for f in folders["folders"].values() for u in (f["host"], *f["members"])]
        + [u for m in meetings for u in (m["host"], *m.get("people", []))]
    ))

    # 0) 표 새로 만들기 → 마이그레이션 전부 (010 에서 이름이 project_id · directory_id 로 바뀜)
    with transaction() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
    os.environ.setdefault("DATABASE_URL", database_url())
    import migrate
    migrate.main()

    with transaction() as cur:
        # 목업을 넣는 동안만 「멤버 추가됨」 · 「공유받음」 자동 알림 트리거를 끔 (목업 알림은 notifications.json 으로)
        cur.execute("ALTER TABLE folder_members DISABLE TRIGGER folder_member_auto_notification")
        cur.execute("ALTER TABLE meeting_shares DISABLE TRIGGER meeting_share_auto_notification")

        # 1) 권한 확인용 계정: 목업에 나오는 사람 전부(일반) + 관리자 1명
        # password_hash 는 NULL (로그인은 백엔드 담당). 권한 검사(crud/common.py)가 accounts 를 보기 때문에 필요해요.
        admin_id = "admin1"
        if admin_id in member_ids:
            raise ValueError("admin1 ID가 구성원 ID와 중복됩니다. 관리자 ID를 변경하세요.")
        demo_accounts = [(uid, f"{uid}@remeet.test", "USER") for uid in member_ids]
        demo_accounts.append((admin_id, "admin@remeet.test", "ADMIN"))
        for user_id, email, role in demo_accounts:
            cur.execute(
                "INSERT INTO accounts (user_id, email, role) VALUES (%s, %s, %s)",
                (user_id, email, role),
            )

        # 2) 설정값 (JSONB)
        settings = {
            "organization": integ["organization"],
            "ai_toggles": integ.get("ai_toggles", []),
            "ai_default_folders": integ.get("ai_default_folders", {}),
            "assign_reasons": cands["reasons"],  # 담당자 지정 사유 선택지 (화면 문구)
        }
        for k, v in settings.items():
            cur.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s::jsonb)",
                        (k, json.dumps(v, ensure_ascii=False)))

        # 3) 회의 종류(directory) · 프로젝트(folders)
        # schema.sql 이 기본 3종을 넣어 두므로 목업 문구로 덮어씀. 순서는 화면(사이드바)과 같게 월간 → 주간 → 일반
        type_order = {"monthly": 0, "weekly": 1, "normal": 2}
        for tid, t in folders["types"].items():
            cur.execute(
                """INSERT INTO meeting_types (id, name, meta, hidden_count, sort_order) VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, meta = EXCLUDED.meta,
                       hidden_count = EXCLUDED.hidden_count, sort_order = EXCLUDED.sort_order""",
                (tid, t["name"], t["meta"], t["hidden_count"], type_order.get(tid, len(type_order))))
        for i, (fid, f) in enumerate(folders["folders"].items()):
            cur.execute("INSERT INTO folders (id, name, host_user_id, sort_order) VALUES (%s, %s, %s, %s)",
                        (fid, f["name"], f["host"], i))
            for j, uid in enumerate(f["members"]):
                cur.execute("INSERT INTO folder_members (folder_id, user_id, position) VALUES (%s, %s, %s)", (fid, uid, j))
            for j, b in enumerate(f.get("brief", [])):
                cur.execute("INSERT INTO folder_briefs (folder_id, position, label, text) VALUES (%s, %s, %s, %s)",
                            (fid, j, b["label"], b["text"]))

        note_order = {mid: j for f in folders["folders"].values() for j, mid in enumerate(f["notes"])}

        # 4) 회의 관리 정보 · 원문 · 요약 · 키워드 · 공유
        for i, m in enumerate(meetings):
            mid = m["id"]
            cur.execute(
                """INSERT INTO meetings (id, project_id, directory_id, host_user_id, title, held_at, time_label,
                                         is_deep, file_name, status, summary_text, summary_src, position,
                                         folder_position, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (mid, m.get("project_id"), m.get("directory_id", "normal"),
                 m["host"], m.get("title", ""),
                 md_to_date(m.get("date")),  # 목업 날짜 '10/27' → 목록 최신순 정렬용 (없으면 NULL)
                 m["time"], m["deep"], m["file"],
                 m["initial_status"], m["summary"]["text"], m["summary"]["src"], i, note_order.get(mid, 1000 + i),
                 m["host"]),
            )
            for j, p in enumerate(m["paragraphs"]):
                cur.execute(
                    "INSERT INTO transcript_lines (meeting_id, line_id, position, speaker, text) VALUES (%s, %s, %s, %s, %s)",
                    (mid, p["id"], j, p["speaker"], p["text"]),
                )
            # 회의 상세: 참석자 · 요약(안건 · 논의 · 결정 · 결과) · 액션 아이템  — 목업 모양 그대로 넣음
            for j, uid in enumerate(dict.fromkeys([m["host"], *m.get("people", [])])):
                cur.execute("INSERT INTO meeting_attendees (meeting_id, user_id, position) VALUES (%s, %s, %s)",
                            (mid, uid, j))
            insert_summary(cur, meeting_id=mid, content=m, todos=m.get("todos", []), origin="ai")
            for j, kw in enumerate(m["keywords"]):
                cur.execute("INSERT INTO meeting_keywords (meeting_id, position, keyword) VALUES (%s, %s, %s)", (mid, j, kw))
            if sh := m.get("shared"):  # 공유받은 회의: 보낸 사람 "강수현 · 재무팀"
                # 사람 이름 → id 는 Neo4j 몫이라 목업에서는 알 수 없음 → 주관자 id 를 보낸 사람으로
                sender = m["host"] if m["host"] != SEED_USER_ID else sh["from"].split("·")[0].strip()
                cur.execute(
                    """INSERT INTO meeting_shares (meeting_id, sender_id, recipient_user_id, scope, lock_raw, shared_on)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (mid, sender, SEED_USER_ID,
                     sh["scope"], sh["lock_raw"], md_to_date(sh["received"])),
                )
        # web AppState.absent_shares 기본값: 미참석한 회의를 주관자가 공유해 줌 (m5 → yun) → 그 사람에게 보임
        for mid, uids in {"m5": [SEED_USER_ID]}.items():
            host = next((m["host"] for m in meetings if m["id"] == mid), None)
            for uid in uids if host else []:
                cur.execute("""INSERT INTO meeting_shares (meeting_id, sender_id, recipient_user_id, scope)
                               VALUES (%s, %s, %s, 'summary')""", (mid, host, uid))
        for m in meetings:  # 안건 분리 (다른 회의를 가리키니까 회의를 다 넣은 뒤에)
            if b := m.get("branch_of"):
                cur.execute("UPDATE meetings SET branch_of_id = %s, branch_agenda_index = %s WHERE id = %s",
                            (b["meeting"], b.get("agenda_index"), m["id"]))

        now = datetime.now(timezone.utc)
        # 5) 외부 연동
        for i, t in enumerate(integ["tools"]):
            cur.execute(
                """INSERT INTO integration_tools (id, mark, name, description, target, is_phase2, sort_order)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (t["id"], t["mark"], t["name"], t["desc"], t["target"], t["phase2"], i),
            )
        for tid, status in integ["initial_status"].items():
            cur.execute("INSERT INTO integration_status (tool_id, status) VALUES (%s, %s)", (tid, status))
        # 예전 목업: failed_exports {tool: [meeting]} · 지금 목업: failures [{tool, meeting, ...}]
        for tid, mids in integ.get("failed_exports", {}).items():
            for mid in mids:
                cur.execute("INSERT INTO failed_exports (tool_id, meeting_id) VALUES (%s, %s)", (tid, mid))
        for f in integ.get("failures", []):
            when = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?: (\d{1,2}):(\d{2}))?", f.get("when", ""))
            failed_at = (datetime(YEAR, int(when[1]), int(when[2]), int(when[3] or 0), int(when[4] or 0),
                                  tzinfo=timezone(timedelta(hours=9))) if when else now)
            cur.execute("""INSERT INTO failed_exports (id, tool_id, meeting_id, kind, reason, code, failed_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (f["id"], f["tool"], f["meeting"], f.get("kind", "simple"), f.get("reason", ""),
                         f.get("code", ""), failed_at))

        # 6) 알림
        now = datetime.now(timezone.utc)
        for n in notis:
            cur.execute(
                """INSERT INTO notifications (id, user_id, kind, text, meeting_id, folder_id, is_read, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (n["id"], SEED_USER_ID, n["kind"], n["text"], n.get("meeting"), n.get("folder"),
                 not n["unread"], relative_to_time(n["when"], now)),
            )

        cur.execute("ALTER TABLE folder_members ENABLE TRIGGER folder_member_auto_notification")
        cur.execute("ALTER TABLE meeting_shares ENABLE TRIGGER meeting_share_auto_notification")

        for table in ("accounts", "folders", "folder_members", "meetings", "meeting_attendees", "transcript_lines",
                      "meeting_summaries", "summary_agendas", "summary_points", "meeting_todos", "meeting_shares",
                      "notifications"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # 표 이름은 코드에 박힌 값이라 f-string OK
            print(f"  {table:<17} {cur.fetchone()['n']:>4} 행")
    print("✅ 시드 완료")


if __name__ == "__main__":
    seed()
