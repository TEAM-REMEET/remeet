"""목업 JSON(data/mock/*.json) 중 PostgreSQL 담당 부분 → PostgreSQL.

    python seed.py               # 표를 새로 만들고(schema.sql) 목업 데이터를 넣음

Neo4j 담당(사람 · 팀 · 스킬 · 담당자 추천, 회의 제목/날짜/참석자/안건/논의/결정/할 일, 위키 그래프)은 넣지 않습니다.
그 부분은 Neo4j 연결이 붙기 전까지 앱이 web/data/mock/*.json 에서 그대로 읽어요 (repository_pg.py 참고).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paths  # noqa: F401  (web 폴더 찾기 · .env 읽기 — 꼭 맨 먼저)
from config import CURRENT_USER_ID, DEMO_EMAIL, YEAR  # web/config.py
from db import transaction

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
    people = load("people")
    folders = load("folders")
    meetings = load("meetings")
    cands = load("candidates")
    integ = load("integrations")
    notis = load("notifications")
    uid_by_name = {p["name"]: pid for pid, p in people.items()}

    with transaction() as cur:
        # 0) 표 새로 만들기
        cur.execute(SCHEMA.read_text(encoding="utf-8"))

        # 1) 로그인 계정 (일반 사용자 3명 + 관리자 1명)
        demo_accounts = [
            ("u1", "user1@remeet.test", "USER"),
            ("u2", "user2@remeet.test", "USER"),
            ("u3", "user3@remeet.test", "USER"),
            ("admin1", "admin@remeet.test", "ADMIN"),
        ]

        for user_id, email, role in demo_accounts:
            cur.execute(
                """
                INSERT INTO accounts (user_id, email, role)
                VALUES (%s, %s, %s)
                """,
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

        # 3) 회의 종류 · 폴더
        for i, (tid, t) in enumerate(folders["types"].items()):
            cur.execute("INSERT INTO meeting_types (id, name, meta, hidden_count, sort_order) VALUES (%s, %s, %s, %s, %s)",
                        (tid, t["name"], t["meta"], t["hidden_count"], i))
        for i, (fid, f) in enumerate(folders["folders"].items()):
            cur.execute("INSERT INTO folders (id, name, type_id, host_user_id, sort_order) VALUES (%s, %s, %s, %s, %s)",
                        (fid, f["name"], f["type"], f["host"], i))
            for j, uid in enumerate(f["members"]):
                cur.execute("INSERT INTO folder_members (folder_id, user_id, position) VALUES (%s, %s, %s)", (fid, uid, j))
            for j, b in enumerate(f.get("brief", [])):
                cur.execute("INSERT INTO folder_briefs (folder_id, position, label, text) VALUES (%s, %s, %s, %s)",
                            (fid, j, b["label"], b["text"]))

        note_order = {mid: j for f in folders["folders"].values() for j, mid in enumerate(f["notes"])}
        note_folder = {mid: fid for fid, f in folders["folders"].items() for mid in f["notes"]}

        # 4) 회의 관리 정보 · 원문 · 요약 · 키워드 · 공유
        for i, m in enumerate(meetings):
            mid = m["id"]
            cur.execute(
                """INSERT INTO meetings (id, folder_id, host_user_id, time_label, is_deep, file_name, status,
                                         summary_text, summary_src, position, folder_position)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (mid, m.get("topic") or note_folder.get(mid), m["host"], m["time"], m["deep"], m["file"],
                 m["initial_status"], m["summary"]["text"], m["summary"]["src"], i, note_order.get(mid, 1000 + i)),
            )
            for j, p in enumerate(m["paragraphs"]):
                cur.execute(
                    "INSERT INTO transcript_lines (meeting_id, line_id, position, speaker, text) VALUES (%s, %s, %s, %s, %s)",
                    (mid, p["id"], j, p["speaker"], p["text"]),
                )
            for j, kw in enumerate(m["keywords"]):
                cur.execute("INSERT INTO meeting_keywords (meeting_id, position, keyword) VALUES (%s, %s, %s)", (mid, j, kw))
            if sh := m.get("shared"):  # 공유받은 회의: 보낸 사람 "강수현 · 재무팀" → 사람 id
                cur.execute(
                    """INSERT INTO meeting_shares (meeting_id, sender_id, recipient_user_id, scope, lock_raw, shared_on)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (mid, uid_by_name[sh["from"].split("·")[0].strip()], CURRENT_USER_ID,
                     sh["scope"], sh["lock_raw"], md_to_date(sh["received"])),
                )
        for m in meetings:  # 안건 분리 (다른 회의를 가리키니까 회의를 다 넣은 뒤에)
            if b := m.get("branch_of"):
                cur.execute("UPDATE meetings SET branch_of_id = %s, branch_agenda_index = %s WHERE id = %s",
                            (b["meeting"], b.get("agenda_index"), m["id"]))

        # 5) 외부 연동
        for i, t in enumerate(integ["tools"]):
            cur.execute(
                """INSERT INTO integration_tools (id, mark, name, description, target, is_phase2, sort_order)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (t["id"], t["mark"], t["name"], t["desc"], t["target"], t["phase2"], i),
            )
        for tid, status in integ["initial_status"].items():
            cur.execute("INSERT INTO integration_status (tool_id, status) VALUES (%s, %s)", (tid, status))
        for tid, mids in integ.get("failed_exports", {}).items():
            for mid in mids:
                cur.execute("INSERT INTO failed_exports (tool_id, meeting_id) VALUES (%s, %s)", (tid, mid))

        # 6) 알림
        now = datetime.now(timezone.utc)
        for n in notis:
            cur.execute(
                """INSERT INTO notifications (id, user_id, kind, text, meeting_id, folder_id, is_read, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (n["id"], CURRENT_USER_ID, n["kind"], n["text"], n.get("meeting"), n.get("folder"),
                 not n["unread"], relative_to_time(n["when"], now)),
            )

        for table in ("accounts", "folders", "folder_members", "meetings", "transcript_lines", "meeting_shares",
                      "notifications"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # 표 이름은 코드에 박힌 값이라 f-string OK
            print(f"  {table:<17} {cur.fetchone()['n']:>4} 행")
    print("✅ 시드 완료")


if __name__ == "__main__":
    seed()
