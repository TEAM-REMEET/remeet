"""구성원·팀·직무·스킬을 Neo4j에 처음 한 번만 적재한다. 이후 추가·수정은 웹(API)에서 Neo4j에 바로 한다.

실행 (backend 폴더에서):
    PYTHONPATH=src python -m followup.entrypoints.seed_people
    PYTHONPATH=src python -m followup.entrypoints.seed_people --check   # DB에 쓰지 않고 파일만 검사
    PYTHONPATH=src python -m followup.entrypoints.seed_people --accounts  # 이미 적재된 DB에 계정만 채움 (구성원 이메일 · 비밀번호 해시, 관리 계정)

DB에 Person이 하나라도 있으면 거부한다. --force는 같은 user_id를 파일 값으로 덮어써서
웹에서 고친 팀·직무·스킬이 사라지니 DB를 새로 만들 때만 쓴다.
"""

import argparse
import json
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[3] / "seed"


def read_org(seed_dir: Path) -> dict:
    org = json.loads((seed_dir / "users.json").read_text(encoding="utf-8"))
    org["skills"] = json.loads((seed_dir / "skills.json").read_text(encoding="utf-8"))
    return org


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    parser.add_argument("--check", action="store_true", help="DB에 쓰지 않고 파일만 검사")
    parser.add_argument("--accounts", action="store_true",
                        help="이미 적재된 DB에 이메일 · 비밀번호 해시만 채움 (이메일이 비어 있는 사람만, 다른 값은 그대로)")
    parser.add_argument("--force", action="store_true", help="이미 적재된 DB에도 덮어쓴다 (웹 수정분 유실)")
    args = parser.parse_args(argv)

    org = read_org(args.seed_dir)
    from followup.adapters.neo4j import people  # --check는 Neo4j 접속 정보 없이도 돌게 여기서 import

    errors = people.check_org(org)
    if errors:
        print("\n".join(errors))
        return 1
    counts = ", ".join(f"{k} {len(org[k])}" for k in ("teams", "roles", "skills", "people"))
    if args.check:
        print(f"[검사 통과] {counts}")
        return 0

    from followup import settings
    from followup.adapters.neo4j.driver import apply_schema, close_driver

    try:
        if args.accounts:
            filled = people.fill_accounts(org["people"])
            admins = people.upsert_admins(org.get("admins", []))
            print(f"[계정 채움] {settings.NEO4J_URI} · 구성원 {len(filled)}명 (이미 이메일이 있던 사람은 건너뜀) · "
                  f"관리 계정 {', '.join(admins) or '없음'} (이미 있으면 그대로)")
            return 0
        existing = len(people.list_people())
        if existing and not args.force:
            print(f"[거부] {settings.NEO4J_URI}에 이미 구성원 {existing}명이 있습니다. "
                  "이후 추가·수정은 웹에서 합니다. 덮어쓰려면 --force (웹 수정분 유실).")
            return 1
        apply_schema()
        people.upsert_org(org)
    finally:
        close_driver()
    print(f"[적재 완료] {settings.NEO4J_URI} · {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
