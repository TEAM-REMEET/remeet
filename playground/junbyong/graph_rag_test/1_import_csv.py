"""Five-table CSV import into Neo4j.

python 1_import_csv.py --dry-run
python 1_import_csv.py

Questions over the imported graph: 2_ask_csv_graph.py.
Required: neo4j, python-dotenv (already in requirements.txt).
Default CSV folder: this script's folder. Default .env: src/.env then project/.env.
The five CSVs describe test data: levels are unverified inferences; active flags
are fixture values. This script deliberately preserves that provenance.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
import getpass
import os
from pathlib import Path
import sys


# 2_ask_csv_graph.py reads relationships by this value; keep the two in sync.
SOURCE = "user_db_csv"
HEADERS = {
    "users": "user_id,name,is_active,position,team_id,role,email,created_at",
    "teams": "team_id,name,is_active",
    "roles": "role_id,name",
    "skills": "skill_id,name,aliases",
    "user_skills": "user_id,skill_id,level,updated_at",
}
NODE_TYPES = {"teams": "Team", "roles": "Role", "skills": "Skill", "users": "Person"}
ID_COLUMNS = {"teams": "team_id", "roles": "role_id", "skills": "skill_id", "users": "user_id"}
CONSTRAINT = "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE"

NODE_QUERIES = {
    "teams": """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
        SET n:Team, n += row, n.type = 'Team', n.source_type = $source,
            n.is_test_data = true, n.active_status_basis = 'test_fixture'
        RETURN count(n) AS written""",
    "roles": """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
        SET n:Role, n += row, n.type = 'Role', n.source_type = $source,
            n.is_test_data = true RETURN count(n) AS written""",
    "skills": """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
        SET n:Skill, n += row, n.type = 'Skill', n.source_type = $source,
            n.is_test_data = true RETURN count(n) AS written""",
    "users": """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
        SET n:Person:User:TeamMember, n += row, n.type = 'Person',
            n.created_at = date(row.created_at), n.active = row.is_active,
            n.source_type = $source, n.is_test_data = true,
            n.active_status_basis = 'test_fixture'
        RETURN count(n) AS written""",
}

TEAM_LINK = """UNWIND $rows AS row
    MATCH (u:Entity:User {id: row.id}), (t:Entity:Team {id: row.team_id})
    MERGE (u)-[r:MEMBER_OF {source_type: $source}]->(t)
    RETURN count(r) AS written"""
ROLE_LINK = """UNWIND $rows AS row
    MATCH (u:Entity:User {id: row.id}), (r:Entity:Role {id: row.role})
    MERGE (u)-[e:HAS_ROLE {source_type: $source}]->(r)
    RETURN count(e) AS written"""
SKILL_LINK = """UNWIND $rows AS row
    MATCH (u:Entity:User {id: row.user_id}), (s:Entity:Skill {id: row.skill_id})
    MERGE (u)-[r:HAS_SKILL {source_type: $source}]->(s)
    SET r.level = row.level, r.proficiency = row.level,
        r.updated_at = date(row.updated_at), r.is_test_data = true,
        r.verification_status = 'inferred_unverified',
        r.source_file = 'user_skills.csv'
    RETURN count(r) AS written"""


def read_table(folder: Path, name: str) -> list[dict]:
    path = folder / f"{name}.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        expected = HEADERS[name].split(",")
        if reader.fieldnames != expected:
            raise ValueError(f"{path.name}: 헤더는 {HEADERS[name]} 순서여야 합니다.")
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{path.name}:{reader.line_num}: 열 개수가 다릅니다.")
            rows.append({k: v.strip() for k, v in row.items()})
        return rows


def require_date(value: str, location: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValueError(f"{location}: YYYY-MM-DD 날짜가 필요합니다.") from None
    return value


def read_dataset(folder: Path) -> dict[str, list[dict]]:
    data = {name: read_table(folder, name) for name in HEADERS}
    ids = {}
    global_ids = set()
    for table, column in ID_COLUMNS.items():
        ids[table] = set()
        for row in data[table]:
            key = row[column]
            if not key or not row["name"]:
                raise ValueError(f"{table}.csv: ID와 이름은 필수입니다.")
            if key in global_ids:
                raise ValueError(f"{table}.csv: 중복 ID 또는 테이블 간 ID 충돌: {key}")
            global_ids.add(key)
            ids[table].add(key)
            row["id"] = key
            if "is_active" in row:
                if row["is_active"].lower() not in {"true", "false"}:
                    raise ValueError(f"{table}.csv: is_active는 true/false여야 합니다.")
                row["is_active"] = row["is_active"].lower() == "true"
    if not data["users"]:
        raise ValueError("users.csv에 최소 한 명이 필요합니다.")
    for row in data["users"]:
        for field, table in (("team_id", "teams"), ("role", "roles")):
            if row[field] not in ids[table]:
                raise ValueError(f"users.csv: {row['id']}의 {field} 참조가 없습니다: {row[field]}")
        require_date(row["created_at"], "users.created_at")
        for field in ("position", "email"):
            row[field] = row[field] or None
    for row in data["skills"]:
        row["aliases"] = list(dict.fromkeys(a.strip() for a in row["aliases"].split("|") if a.strip()))
    pairs = set()
    for row in data["user_skills"]:
        pair = (row["user_id"], row["skill_id"])
        if pair in pairs:
            raise ValueError(f"user_skills.csv: 중복 사용자·스킬 연결: {pair}")
        pairs.add(pair)
        if row["user_id"] not in ids["users"] or row["skill_id"] not in ids["skills"]:
            raise ValueError(f"user_skills.csv: 존재하지 않는 ID 참조: {pair}")
        if row["level"] not in {"1", "2", "3", "4"}:
            raise ValueError("user_skills.csv: level은 1~4 정수여야 합니다.")
        row["level"] = int(row["level"])
        require_date(row["updated_at"], "user_skills.updated_at")
    return data


def import_dataset(tx, data: dict, batch_size: int) -> None:
    expected = {row["id"]: kind for table, kind in NODE_TYPES.items() for row in data[table]}
    known = {"Person", "User", "TeamMember", "Team", "Role", "Skill", "Project", "Metric", "System", "Unknown"}
    records = tx.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.type AS type, labels(n) AS labels", ids=list(expected))
    for record in records:
        wanted = expected[record["id"]]
        allowed = {"Person", "User", "TeamMember"} if wanted == "Person" else {wanted}
        if record["type"] not in (None, wanted) or (set(record["labels"]) & known) - allowed:
            raise ValueError(f"기존 Entity의 타입이 CSV ID와 충돌합니다: {record['id']}")

    def write(query, rows):
        for start in range(0, len(rows), batch_size):
            batch = rows[start:start + batch_size]
            result = tx.run(query, rows=batch, source=SOURCE).single(strict=True)
            if result["written"] != len(batch):
                raise ValueError("노드/관계 저장 개수가 다릅니다. 트랜잭션을 취소합니다.")

    for table in NODE_TYPES:
        write(NODE_QUERIES[table], data[table])
    # Replace only this importer's relationships for the users in this CSV.
    # Nodes and relationships from other sources are retained.
    tx.run("""MATCH (u:Entity:User)-[r:MEMBER_OF|BELONGS_TO|HAS_ROLE|HAS_SKILL]->()
        WHERE u.id IN $ids AND r.source_type = $source DELETE r""",
        ids=[u["id"] for u in data["users"]], source=SOURCE).consume()
    write(TEAM_LINK, data["users"])
    write(ROLE_LINK, data["users"])
    write(SKILL_LINK, data["user_skills"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="5개 CSV → Neo4j ID 연결 등록")
    parser.add_argument("--csv-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="CSV 형식·ID 참조만 검사")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size는 1 이상이어야 합니다.")
    try:
        data = read_dataset(args.csv_dir)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print(f"CSV 검증 실패: {exc}", file=sys.stderr)
        return 1
    print("CSV 검증 완료: " + ", ".join(f"{k} {len(v)}행" for k, v in data.items()))
    if args.dry_run:
        return 0
    try:
        from dotenv import load_dotenv
        from neo4j import GraphDatabase
        from neo4j.exceptions import DriverError, Neo4jError
    except ImportError:
        print("neo4j와 python-dotenv가 필요합니다. 프로젝트 requirements.txt를 설치하세요.", file=sys.stderr)
        return 1
    if args.env_file:
        if not args.env_file.is_file():
            print("지정한 .env 파일이 없습니다.", file=sys.stderr)
            return 1
        load_dotenv(args.env_file, override=False)
    else:
        script_dir = Path(__file__).resolve().parent
        load_dotenv(script_dir / ".env", override=False)
        load_dotenv(script_dir.parent / ".env", override=False)
        load_dotenv(script_dir.parents[2] / ".env", override=False)  # repo root
    password = os.getenv("NEO4J_PASSWORD")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Neo4j 비밀번호: ")
    if not password:
        print("NEO4J_PASSWORD 설정이 필요합니다.", file=sys.stderr)
        return 1
    try:
        with GraphDatabase.driver(os.getenv("NEO4J_URI") or "neo4j://localhost:7687",
                                  auth=(os.getenv("NEO4J_USERNAME") or "neo4j", password)) as driver:
            driver.verify_connectivity()
            with driver.session(database=os.getenv("NEO4J_DATABASE") or "neo4j") as session:
                session.run(CONSTRAINT).consume()
                session.execute_write(import_dataset, data, args.batch_size)
    except (DriverError, Neo4jError, ValueError, OSError) as exc:
        print(f"Neo4j 처리 실패 ({type(exc).__name__}). 접속 설정·ID 충돌·DB 권한을 확인하세요.", file=sys.stderr)
        if isinstance(exc, ValueError):
            print(str(exc), file=sys.stderr)
        return 1
    print("Neo4j 저장 완료: 사용자·팀·역할·스킬 및 ID 관계를 반영했습니다.")
    print("질문은 python 2_ask_csv_graph.py 로 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
