"""Layered CSV import into Neo4j: user → project → meeting.

python 1_import_csv.py --dry-run              # every layer, CSV checks only
python 1_import_csv.py --layer meeting        # one layer
python 1_import_csv.py                        # every layer in one transaction

Each layer follows the graph design:
  user    (csv/01_user)     sync: nodes MERGE, this importer's user relationships rewritten
  project (csv/02_project)  MERGE: accumulates and never deletes; topic summary only moves forward
  meeting (csv/03_meeting)  CREATE: a meeting_id already in the DB is skipped, except that the
                             status of its action items and assignments moves forward (never back)
IDs referenced from another layer are checked against the DB (with --dry-run: that layer's CSV).

Questions over the imported graph: 2_ask_csv_graph.py.
Required: neo4j, python-dotenv (already in requirements.txt).
Default CSV folder: csv/ next to this script. Default .env: src/.env then project/.env.
The user CSVs describe test data: levels are unverified inferences; active flags
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


# 2_ask_csv_graph.py and 3_recommend_assignee.py read user data by the user layer's
# source value; keep them in sync.
LAYERS = {
    "user": ("01_user", "user_db_csv", {
        "teams": "team_id,name,is_active",
        "roles": "role_id,name",
        "skills": "skill_id,name,description,domain,tools,source",
        "users": "user_id,name,is_active,position,team_id,role,email,created_at",
        "user_skills": "user_id,skill_id,level,updated_at",
    }),
    "project": ("02_project", "project_csv", {
        "projects": "project_id,name,status",
        "rel_owns": "team_id,project_id",
        "topics": "topic_id,project_id,parent_topic_id,title,status,summary,last_discussed_at,embedding",
    }),
    "meeting": ("03_meeting", "meeting_csv", {
        "meetings": "meeting_id,project_id,title,held_at",
        "agenda_items": "agenda_item_id,meeting_id,order,title,background,discussion,decisions,"
                        "next_topics,embedding,topic_id,confidence,linked_by",
        "action_items": "action_item_id,agenda_item_id,follows_up_id,description,status,due,owner_raw",
        "rel_attended": "user_id,meeting_id",
        "rel_assigned_to": "user_id,action_item_id,work_status,assign_source",
        "rel_requires_skill": "action_item_id,skill_id,importance",
    }),
}
# Node tables: id column, node type, column that must not be empty.
NODES = {
    "teams": ("team_id", "Team", "name"), "roles": ("role_id", "Role", "name"),
    "skills": ("skill_id", "Skill", "name"), "users": ("user_id", "Person", "name"),
    "projects": ("project_id", "Project", "name"), "topics": ("topic_id", "Topic", "title"),
    "meetings": ("meeting_id", "Meeting", "title"),
    "agenda_items": ("agenda_item_id", "AgendaItem", "title"),
    "action_items": ("action_item_id", "ActionItem", "description"),
}
KNOWN_TYPES = {kind for _, kind, _ in NODES.values()} | {"Metric", "System", "Unknown"}
TOPIC_STATUS = {"open", "resolved", "parked"}
# Ranks: a re-import may only move a status forward.
ACTION_RANK = {"open": 0, "in_progress": 1, "completed": 2}
WORK_RANK = {"in_work": 0, "work_completed": 1}
ASSIGN_SOURCES = {"explicit", "history", "manual"}
LINKED_BY = {"auto", "hitl"}
CONSTRAINT = "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE"

# --- user layer -------------------------------------------------------------
USER_NODE_QUERIES = {
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
    MATCH (u:Entity:User {id: row.id}), (t:Entity {id: row.team_id})
    MERGE (u)-[r:MEMBER_OF {source_type: $source}]->(t)
    RETURN count(r) AS written"""
ROLE_LINK = """UNWIND $rows AS row
    MATCH (u:Entity:User {id: row.id}), (r:Entity {id: row.role})
    MERGE (u)-[e:HAS_ROLE {source_type: $source}]->(r)
    RETURN count(e) AS written"""
SKILL_LINK = """UNWIND $rows AS row
    MATCH (u:Entity:User {id: row.user_id}), (s:Entity {id: row.skill_id})
    MERGE (u)-[r:HAS_SKILL {source_type: $source}]->(s)
    SET r.level = row.level, r.proficiency = row.level,
        r.updated_at = date(row.updated_at), r.is_test_data = true,
        r.verification_status = 'inferred_unverified',
        r.source_file = 'user_skills.csv'
    RETURN count(r) AS written"""

# --- project layer ----------------------------------------------------------
PROJECT_NODE = """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
    SET n:Project, n += row, n.type = 'Project', n.source_type = $source
    RETURN count(n) AS written"""
# summary/status/embedding are cumulative: only a row at least as recent as the stored one wins.
TOPIC_NODE = """UNWIND $rows AS row MERGE (n:Entity {id: row.id})
    WITH n, row, n.last_discussed_at IS NULL OR (row.last_discussed_at IS NOT NULL
         AND date(row.last_discussed_at) >= n.last_discussed_at) AS newer
    SET n:Topic, n.type = 'Topic', n.source_type = $source, n.title = row.title,
        n.project_id = row.project_id, n.parent_topic_id = row.parent_topic_id,
        n.status = CASE WHEN newer THEN row.status ELSE n.status END,
        n.summary = CASE WHEN newer THEN row.summary ELSE n.summary END,
        n.embedding = CASE WHEN newer THEN row.embedding ELSE n.embedding END,
        n.last_discussed_at = CASE WHEN newer THEN date(row.last_discussed_at)
                                   ELSE n.last_discussed_at END
    RETURN count(n) AS written"""
OWNS_LINK = """UNWIND $rows AS row
    MATCH (t:Entity {id: row.team_id}), (p:Entity {id: row.project_id})
    MERGE (t)-[r:OWNS]->(p) SET r.source_type = $source RETURN count(r) AS written"""
TOPIC_IN_LINK = """UNWIND $rows AS row
    MATCH (t:Entity {id: row.id}), (p:Entity {id: row.project_id})
    MERGE (t)-[r:IN]->(p) SET r.source_type = $source RETURN count(r) AS written"""
SUBTOPIC_LINK = """UNWIND $rows AS row
    MATCH (t:Entity {id: row.id}), (p:Entity {id: row.parent_topic_id})
    MERGE (t)-[r:SUBTOPIC_OF]->(p) SET r.source_type = $source RETURN count(r) AS written"""

# --- meeting layer ----------------------------------------------------------
MEETING_NODE = """UNWIND $rows AS row MATCH (p:Entity {id: row.project_id})
    CREATE (n:Entity:Meeting) SET n += row, n.type = 'Meeting', n.source_type = $source,
        n.held_at = date(row.held_at)
    CREATE (n)-[:IN {source_type: $source}]->(p)
    RETURN count(n) AS written"""
AGENDA_NODE = """UNWIND $rows AS row MATCH (m:Entity {id: row.meeting_id})
    CREATE (n:Entity:AgendaItem) SET n += row, n.type = 'AgendaItem', n.source_type = $source
    CREATE (m)-[:HAS_AGENDA {source_type: $source}]->(n)
    RETURN count(n) AS written"""
ABOUT_LINK = """UNWIND $rows AS row
    MATCH (a:Entity {id: row.id}), (t:Entity {id: row.topic_id})
    CREATE (a)-[r:ABOUT {confidence: row.confidence, linked_by: row.linked_by,
                         source_type: $source}]->(t)
    RETURN count(r) AS written"""
ACTION_NODE = """UNWIND $rows AS row MATCH (a:Entity {id: row.agenda_item_id})
    CREATE (n:Entity:ActionItem) SET n += row, n.type = 'ActionItem', n.source_type = $source,
        n.due = date(row.due)
    CREATE (a)-[:PRODUCED {source_type: $source}]->(n)
    RETURN count(n) AS written"""
FOLLOWS_UP_LINK = """UNWIND $rows AS row
    MATCH (n:Entity {id: row.id}), (prev:Entity {id: row.follows_up_id})
    CREATE (n)-[r:FOLLOWS_UP {source_type: $source}]->(prev) RETURN count(r) AS written"""
ATTENDED_LINK = """UNWIND $rows AS row
    MATCH (u:Entity {id: row.user_id}), (m:Entity {id: row.meeting_id})
    CREATE (u)-[r:ATTENDED {source_type: $source}]->(m) RETURN count(r) AS written"""
REQUIRES_SKILL_LINK = """UNWIND $rows AS row
    MATCH (a:Entity {id: row.action_item_id}), (s:Entity {id: row.skill_id})
    CREATE (a)-[r:REQUIRES_SKILL {importance: row.importance, source_type: $source}]->(s)
    RETURN count(r) AS written"""
# Used for new and already-imported meetings alike: work_status never moves back.
ASSIGNED_LINK = """UNWIND $rows AS row
    MATCH (u:Entity {id: row.user_id}), (a:Entity {id: row.action_item_id})
    MERGE (u)-[r:ASSIGNED_TO]->(a)
    SET r.work_status = CASE WHEN coalesce($rank[r.work_status], -1) < $rank[row.work_status]
                             THEN row.work_status ELSE r.work_status END,
        r.assign_source = coalesce(r.assign_source, row.assign_source),
        r.source_type = coalesce(r.source_type, $source)
    RETURN count(r) AS written"""
ACTION_STATUS = """UNWIND $rows AS row MATCH (n:Entity {id: row.id})
    SET n.status = CASE WHEN coalesce($rank[n.status], -1) < $rank[row.status]
                        THEN row.status ELSE n.status END
    RETURN count(n) AS written"""


def read_table(path: Path, header: str) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != header.split(","):
            raise ValueError(f"{path.name}: 헤더는 {header} 순서여야 합니다.")
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


def optional_date(value: str, location: str) -> str | None:
    return require_date(value, location) if value else None


def choice(value: str, allowed, location: str) -> str:
    if value not in allowed:
        raise ValueError(f"{location}: {'/'.join(sorted(allowed))} 중 하나여야 합니다: {value!r}")
    return value


def number(value: str, location: str) -> float:
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"{location}: 숫자가 필요합니다: {value!r}") from None


def split_list(value: str) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in value.split("|") if v.strip()))


def embedding(value: str, location: str) -> list[float] | None:
    return [number(v, location) for v in split_list(value)] or None


def unique(rows: list[dict], columns: tuple[str, ...], table: str) -> None:
    seen = set()
    for row in rows:
        key = tuple(row[c] for c in columns)
        if key in seen:
            raise ValueError(f"{table}.csv: 중복 연결: {key}")
        seen.add(key)


def local_ref(key: str, kind: str, local: dict, location: str) -> None:
    if local.get(key) != kind:
        raise ValueError(f"{location}: 같은 폴더 CSV에 {kind} ID가 없습니다: {key}")


def validate_user(data: dict, local: dict) -> list[tuple]:
    if not data["users"]:
        raise ValueError("users.csv에 최소 한 명이 필요합니다.")
    refs = []
    for row in data["users"]:
        refs += [(row["team_id"], "Team", f"users.csv {row['id']}.team_id"),
                 (row["role"], "Role", f"users.csv {row['id']}.role")]
        require_date(row["created_at"], "users.created_at")
        for field in ("position", "email"):
            row[field] = row[field] or None
    for row in data["skills"]:
        row["tools"] = split_list(row["tools"])
        for field in ("description", "domain", "source"):
            row[field] = row[field] or None
    unique(data["user_skills"], ("user_id", "skill_id"), "user_skills")
    for row in data["user_skills"]:
        refs += [(row["user_id"], "Person", "user_skills.csv user_id"),
                 (row["skill_id"], "Skill", "user_skills.csv skill_id")]
        if row["level"] not in {"1", "2", "3", "4"}:
            raise ValueError("user_skills.csv: level은 1~4 정수여야 합니다.")
        row["level"] = int(row["level"])
        require_date(row["updated_at"], "user_skills.updated_at")
    return refs


def validate_project(data: dict, local: dict) -> list[tuple]:
    refs = []
    for row in data["projects"]:
        row["status"] = row["status"] or None
    unique(data["rel_owns"], ("team_id", "project_id"), "rel_owns")
    for row in data["rel_owns"]:
        refs += [(row["team_id"], "Team", "rel_owns.csv team_id"),
                 (row["project_id"], "Project", "rel_owns.csv project_id")]
    for row in data["topics"]:
        loc = f"topics.csv {row['id']}"
        refs.append((row["project_id"], "Project", f"{loc}.project_id"))
        row["parent_topic_id"] = row["parent_topic_id"] or None
        if row["parent_topic_id"]:
            if row["parent_topic_id"] == row["id"]:
                raise ValueError(f"{loc}: 자기 자신을 상위 토픽으로 지정할 수 없습니다.")
            refs.append((row["parent_topic_id"], "Topic", f"{loc}.parent_topic_id"))
        choice(row["status"], TOPIC_STATUS, f"{loc}.status")
        row["summary"] = row["summary"] or None
        row["last_discussed_at"] = optional_date(row["last_discussed_at"], f"{loc}.last_discussed_at")
        row["embedding"] = embedding(row["embedding"], f"{loc}.embedding")
    return refs


def validate_meeting(data: dict, local: dict) -> list[tuple]:
    refs = []
    for row in data["meetings"]:
        refs.append((row["project_id"], "Project", f"meetings.csv {row['id']}.project_id"))
        require_date(row["held_at"], f"meetings.csv {row['id']}.held_at")
    for row in data["agenda_items"]:
        loc = f"agenda_items.csv {row['id']}"
        local_ref(row["meeting_id"], "Meeting", local, f"{loc}.meeting_id")
        try:
            row["order"] = int(row["order"])
        except ValueError:
            raise ValueError(f"{loc}.order: 정수가 필요합니다.") from None
        for field in ("background", "discussion"):
            row[field] = row[field] or None
        row["decisions"] = split_list(row["decisions"])
        row["next_topics"] = split_list(row["next_topics"])
        row["embedding"] = embedding(row["embedding"], f"{loc}.embedding")
        if row["topic_id"]:
            refs.append((row["topic_id"], "Topic", f"{loc}.topic_id"))
            choice(row["linked_by"], LINKED_BY, f"{loc}.linked_by")
            row["confidence"] = number(row["confidence"], f"{loc}.confidence") if row["confidence"] else None
            if row["confidence"] is not None and not 0 <= row["confidence"] <= 1:
                raise ValueError(f"{loc}.confidence: 0~1 사이여야 합니다.")
        else:
            row["topic_id"] = row["confidence"] = row["linked_by"] = None
    for row in data["action_items"]:
        loc = f"action_items.csv {row['id']}"
        local_ref(row["agenda_item_id"], "AgendaItem", local, f"{loc}.agenda_item_id")
        row["follows_up_id"] = row["follows_up_id"] or None
        if row["follows_up_id"]:
            if row["follows_up_id"] == row["id"]:
                raise ValueError(f"{loc}: 자기 자신을 후속 대상으로 지정할 수 없습니다.")
            refs.append((row["follows_up_id"], "ActionItem", f"{loc}.follows_up_id"))
        choice(row["status"], ACTION_RANK, f"{loc}.status")
        row["due"] = optional_date(row["due"], f"{loc}.due")
        row["owner_raw"] = row["owner_raw"] or None
    unique(data["rel_attended"], ("user_id", "meeting_id"), "rel_attended")
    for row in data["rel_attended"]:
        local_ref(row["meeting_id"], "Meeting", local, "rel_attended.csv meeting_id")
        refs.append((row["user_id"], "Person", "rel_attended.csv user_id"))
    unique(data["rel_assigned_to"], ("user_id", "action_item_id"), "rel_assigned_to")
    for row in data["rel_assigned_to"]:
        local_ref(row["action_item_id"], "ActionItem", local, "rel_assigned_to.csv action_item_id")
        refs.append((row["user_id"], "Person", "rel_assigned_to.csv user_id"))
        choice(row["work_status"], WORK_RANK, "rel_assigned_to.csv work_status")
        choice(row["assign_source"], ASSIGN_SOURCES, "rel_assigned_to.csv assign_source")
    unique(data["rel_requires_skill"], ("action_item_id", "skill_id"), "rel_requires_skill")
    for row in data["rel_requires_skill"]:
        local_ref(row["action_item_id"], "ActionItem", local, "rel_requires_skill.csv action_item_id")
        refs.append((row["skill_id"], "Skill", "rel_requires_skill.csv skill_id"))
        row["importance"] = number(row["importance"], "rel_requires_skill.csv importance")
    return refs


VALIDATORS = {"user": validate_user, "project": validate_project, "meeting": validate_meeting}


def read_layer(root: Path, layer: str) -> tuple[dict, list[tuple], dict]:
    """Returns (rows per table, external references, {id: type} of this layer's nodes)."""
    folder, _, headers = LAYERS[layer]
    data = {name: read_table(root / folder / f"{name}.csv", header) for name, header in headers.items()}
    local = {}
    for table in headers:
        if table not in NODES:
            continue
        column, kind, required = NODES[table]
        for row in data[table]:
            key = row[column]
            if not key or not row[required]:
                raise ValueError(f"{table}.csv: {column}와 {required}는 필수입니다.")
            if key in local:
                raise ValueError(f"{table}.csv: 중복 ID 또는 테이블 간 ID 충돌: {key}")
            local[key] = kind
            row["id"] = key
            if "is_active" in row:
                if row["is_active"].lower() not in {"true", "false"}:
                    raise ValueError(f"{table}.csv: is_active는 true/false여야 합니다.")
                row["is_active"] = row["is_active"].lower() == "true"
    return data, VALIDATORS[layer](data, local), local


def check_refs(refs: list[tuple], known: dict) -> None:
    for key, kind, location in refs:
        if known.get(key) != kind:
            raise ValueError(f"{location}: {kind} ID가 없습니다: {key}")


def db_types(tx, ids) -> dict:
    records = tx.run("MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.type AS type, "
                     "labels(n) AS labels", ids=list(ids))
    return {r["id"]: r["type"] or next(iter(sorted(set(r["labels"]) & KNOWN_TYPES)), "Unknown")
            for r in records}


def write(tx, query, rows, source, batch_size, **params) -> None:
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        result = tx.run(query, rows=batch, source=source, **params).single(strict=True)
        if result["written"] != len(batch):
            raise ValueError("노드/관계 저장 개수가 다릅니다. 트랜잭션을 취소합니다.")


def write_user(tx, data, db, source, batch_size) -> str:
    for table, query in USER_NODE_QUERIES.items():
        write(tx, query, data[table], source, batch_size)
    # Replace only this importer's relationships for the users in this CSV.
    # Nodes and relationships from other sources are retained.
    tx.run("""MATCH (u:Entity:User)-[r:MEMBER_OF|BELONGS_TO|HAS_ROLE|HAS_SKILL]->()
        WHERE u.id IN $ids AND r.source_type = $source DELETE r""",
        ids=[u["id"] for u in data["users"]], source=source).consume()
    write(tx, TEAM_LINK, data["users"], source, batch_size)
    write(tx, ROLE_LINK, data["users"], source, batch_size)
    write(tx, SKILL_LINK, data["user_skills"], source, batch_size)
    return f"user: 사용자 {len(data['users'])}명, 스킬 연결 {len(data['user_skills'])}개 동기화"


def write_project(tx, data, db, source, batch_size) -> str:
    write(tx, PROJECT_NODE, data["projects"], source, batch_size)
    write(tx, TOPIC_NODE, data["topics"], source, batch_size)
    write(tx, OWNS_LINK, data["rel_owns"], source, batch_size)
    write(tx, TOPIC_IN_LINK, data["topics"], source, batch_size)
    write(tx, SUBTOPIC_LINK, [t for t in data["topics"] if t["parent_topic_id"]], source, batch_size)
    return f"project: 프로젝트 {len(data['projects'])}개, 토픽 {len(data['topics'])}개 반영"


def write_meeting(tx, data, db, source, batch_size) -> str:
    skipped = {m["id"] for m in data["meetings"] if m["id"] in db}
    meetings = [m for m in data["meetings"] if m["id"] not in skipped]
    agenda = [a for a in data["agenda_items"] if a["meeting_id"] not in skipped]
    new_agenda = {a["id"] for a in agenda}
    actions = [x for x in data["action_items"] if x["agenda_item_id"] in new_agenda]
    old_actions = [x for x in data["action_items"] if x["agenda_item_id"] not in new_agenda]
    for row in meetings + agenda + actions:
        if row["id"] in db:
            raise ValueError(f"새 회의의 ID가 이미 DB에 있습니다: {row['id']}")
    for row in old_actions:
        if row["id"] not in db:
            raise ValueError(f"이미 저장된 회의에는 새 액션 아이템을 추가할 수 없습니다: {row['id']}")
    new_actions = {x["id"] for x in actions}
    new_meetings = {m["id"] for m in meetings}

    write(tx, MEETING_NODE, meetings, source, batch_size)
    write(tx, AGENDA_NODE, agenda, source, batch_size)
    write(tx, ABOUT_LINK, [a for a in agenda if a["topic_id"]], source, batch_size)
    write(tx, ACTION_NODE, actions, source, batch_size)
    write(tx, FOLLOWS_UP_LINK, [x for x in actions if x["follows_up_id"]], source, batch_size)
    write(tx, ATTENDED_LINK, [r for r in data["rel_attended"] if r["meeting_id"] in new_meetings],
          source, batch_size)
    write(tx, REQUIRES_SKILL_LINK,
          [r for r in data["rel_requires_skill"] if r["action_item_id"] in new_actions], source, batch_size)
    write(tx, ACTION_STATUS, old_actions, source, batch_size, rank=ACTION_RANK)
    write(tx, ASSIGNED_LINK, data["rel_assigned_to"], source, batch_size, rank=WORK_RANK)
    message = f"meeting: 새 회의 {len(meetings)}개 저장"
    if skipped:
        message += f", 이미 있는 회의 {len(skipped)}개 건너뜀 ({', '.join(sorted(skipped))}, 상태만 갱신)"
    return message


WRITERS = {"user": write_user, "project": write_project, "meeting": write_meeting}


def import_layers(tx, layers: dict, batch_size: int) -> list[str]:
    messages = []
    for layer, (data, refs, local) in layers.items():  # earlier layers are visible to later ones
        db = db_types(tx, set(local) | {key for key, _, _ in refs})
        for key, kind in local.items():
            if key in db and db[key] != kind:
                raise ValueError(f"기존 Entity의 타입이 CSV ID와 충돌합니다: {key} ({db[key]} ≠ {kind})")
        check_refs(refs, {**db, **local})
        messages.append(WRITERS[layer](tx, data, db, LAYERS[layer][1], batch_size))
    return messages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="레이어별 CSV → Neo4j 등록 (user → project → meeting)")
    parser.add_argument("--layer", choices=[*LAYERS, "all"], default="all")
    parser.add_argument("--csv-dir", type=Path, default=Path(__file__).resolve().parent / "csv")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true",
                        help="CSV 형식·ID 참조만 검사 (다른 레이어 참조는 그 레이어 CSV로 확인)")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size는 1 이상이어야 합니다.")
    selected = list(LAYERS) if args.layer == "all" else [args.layer]
    try:
        layers = {layer: read_layer(args.csv_dir, layer) for layer in selected}
        if args.dry_run:
            known = {}
            for layer in LAYERS:
                known |= (layers[layer] if layer in layers else read_layer(args.csv_dir, layer))[2]
            for _, refs, _ in layers.values():
                check_refs(refs, known)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print(f"CSV 검증 실패: {exc}", file=sys.stderr)
        return 1
    for layer, (data, _, _) in layers.items():
        print(f"CSV 검증 완료 [{layer}]: " + ", ".join(f"{k} {len(v)}행" for k, v in data.items()))
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
                messages = session.execute_write(import_layers, layers, args.batch_size)
    except (DriverError, Neo4jError, ValueError, OSError) as exc:
        print(f"Neo4j 처리 실패 ({type(exc).__name__}). 접속 설정·ID 충돌·DB 권한을 확인하세요.", file=sys.stderr)
        if isinstance(exc, ValueError):
            print(str(exc), file=sys.stderr)
        return 1
    print("Neo4j 저장 완료:\n  " + "\n  ".join(messages))
    print("질문은 python 2_ask_csv_graph.py 로 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
