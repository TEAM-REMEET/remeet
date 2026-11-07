"""PostgreSQL 표 만들기 · 구성원 넣기. Neo4j 의 seed_people.py 와 같은 users.json · skills.json 을 씀.

실행 (backend 폴더에서, DATABASE_URL 은 remeet/.env 또는 환경 변수):
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres migrate         # 마이그레이션만 (쓰던 DB 는 이것만)
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres reset           # ⚠️ 회의 쪽 표 전부 지우고 새로 + 마이그레이션
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres people --check  # DB 없이 파일만 검사
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres people          # 구성원 넣기 (이미 있으면 거부)
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres people --force  # 이미 있어도 파일 값으로 덮어씀
    PYTHONPATH=src python -m followup.entrypoints.seed_postgres sync-from-neo4j # Neo4j 구성원 전체로 다시 맞춤

처음: migrate → people.  reset 뒤에는 계정(accounts)이 비므로 people --force.
구성원 표(people · teams · roles · skills · person_skills)는 reset 에서도 지우지 않아요 (migrations/011 주석).
이후 사람 추가 · 수정은 POST/PATCH /api/people 이 Neo4j 저장 뒤 sync_people() 로 반영해요.
그 반영이 실패해서 어긋났으면 sync-from-neo4j (Neo4j 가 원본, 활성 · 비활성 전원).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from followup.adapters.postgres import seed_people
from followup.adapters.postgres.db import _masked, database_url

SCHEMA = Path(seed_people.__file__).with_name("schema.sql")


def migrate() -> None:
    from followup.adapters.postgres import migrate as m
    os.environ.setdefault("DATABASE_URL", database_url())  # migrate.py 는 DATABASE_URL 을 직접 읽음
    m.main()


def reset() -> None:
    from followup.adapters.postgres.db import transaction
    with transaction() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
    migrate()
    with transaction() as cur:
        for table in ("meeting_types", "integration_tools", "integration_status", "accounts", "people"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # 표 이름은 코드에 박힌 값이라 f-string OK
            print(f"  {table:<19} {cur.fetchone()['n']:>4} 행")
    print("✅ 초기화 완료 → 구성원: people --force")


def people(seed_dir: Path, check: bool, force: bool) -> int:
    org = seed_people.read_org(seed_dir)
    errors = seed_people.check_org(org)
    if errors:
        print("\n".join(errors))
        return 1
    rows = seed_people.build_rows(org)
    counts = ", ".join(f"{t} {len(r)}" for t, r in rows.items())
    if check:
        print(f"[검사 통과] {counts}")
        return 0
    seed_people.write(rows, force)
    print(f"[적재 완료] {counts}")
    return 0


def sync_from_neo4j() -> None:
    from followup.adapters.neo4j import people as neo4j_people
    from followup.adapters.postgres.people import sync_people
    everyone = neo4j_people.list_people()
    sync_people(everyone)
    print(f"[반영 완료] Neo4j 구성원 {len(everyone)}명 → PostgreSQL")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="마이그레이션만 적용")
    sub.add_parser("reset", help="⚠️ 회의 쪽 표 초기화 + 마이그레이션")
    p = sub.add_parser("people", help="users.json · skills.json → 구성원 표")
    p.add_argument("--seed-dir", type=Path, default=seed_people.SEED_DIR)
    p.add_argument("--check", action="store_true", help="DB 에 쓰지 않고 파일만 검사")
    p.add_argument("--force", action="store_true", help="이미 있어도 파일 값으로 덮어씀 (웹 수정분 유실)")
    sub.add_parser("sync-from-neo4j", help="Neo4j 구성원 전체 → PostgreSQL (어긋났을 때)")
    args = parser.parse_args(argv)

    print("DB:", _masked(database_url()))
    if args.command == "migrate":
        migrate()
    elif args.command == "reset":
        reset()
    elif args.command == "sync-from-neo4j":
        sync_from_neo4j()
    else:
        return people(args.seed_dir, args.check, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())