"""빈 개발 DB 만들기: 표를 전부 새로 만들고(schema.sql) 마이그레이션을 전부 적용.

    python seed.py                 # 회의 쪽 표 초기화 + 마이그레이션 (기준 데이터: 회의 종류 · 외부 연동 도구)
    python seed_people.py --force  # 그다음 구성원 (users.json) — 계정이 초기화됐으니 --force

⚠️ 회의 쪽 표를 전부 지우고 다시 만들어요. 쓰던 DB 에는 돌리지 말고 python migrate.py 만 쓰세요.
구성원 표(people · teams · roles · skills · person_skills)는 지우지 않아요 (migrations/011 주석 참고).
회의 · 프로젝트 데이터는 이제 프론트 목업이 아니라 백엔드(crud 함수)가 넣어요.
"""

from __future__ import annotations

import os
from pathlib import Path

import paths  # noqa: F401  (.env 읽기 — 꼭 맨 먼저)
from db import database_url, transaction

SCHEMA = Path(__file__).with_name("schema.sql")


def seed() -> None:
    url = os.environ.get("DATABASE_URL") or database_url()
    with transaction() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
    os.environ.setdefault("DATABASE_URL", url)
    import migrate
    migrate.main()

    with transaction() as cur:
        for table in ("meeting_types", "integration_tools", "integration_status", "accounts", "people"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # 표 이름은 코드에 박힌 값이라 f-string OK
            print(f"  {table:<19} {cur.fetchone()['n']:>4} 행")
        cur.execute("SELECT count(*) AS n FROM people")
        has_people = cur.fetchone()["n"]
    print("✅ 초기화 완료")
    if has_people:
        print("→ 구성원 표는 남아 있지만 계정(accounts)은 비었어요: python seed_people.py --force")
    else:
        print("→ 구성원 넣기: python seed_people.py")


if __name__ == "__main__":
    seed()