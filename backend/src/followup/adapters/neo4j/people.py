"""구성원(Person)·팀·직무·스킬 적재와 조회.

    (Person)-[:MEMBER_OF]->(Team)
    (Person)-[:HAS_ROLE]->(Role)
    (Person)-[:HAS_SKILL {level, updated_at}]->(Skill)
    Person {user_id, name, email, permission, is_active, position}
        position: 'senior' | 'mid' | 'junior' (추천이 직급별로 후보를 뽑음)
        permission: 'admin' | 'member' (웹 권한, 없으면 member). 직무는 Role 노드라 이름을 나눴다.
    Admin {user_id, name, email, password_hash}
        구성원이 아닌 관리 전용 계정. Person이 아니라서 구성원 목록 · 담당자 추천 후보에 나오지 않는다.
"""

from typing import Any

from followup.adapters.neo4j.driver import get_driver

UPSERT_TEAMS = """
UNWIND $rows AS r
MERGE (n:Team {team_id: r.team_id})
SET n.name = r.name, n.is_active = coalesce(r.is_active, true)"""

UPSERT_ROLES = """
UNWIND $rows AS r
MERGE (n:Role {role_id: r.role_id}) SET n.name = r.name"""

UPSERT_SKILLS = """
UNWIND $rows AS r
MERGE (n:Skill {skill_id: r.skill_id}) SET n += r"""

# 팀·직무·스킬 관계는 지우고 다시 만든다 → 파일에서 팀을 바꾸면 이전 팀 관계가 남지 않는다.
UPSERT_PEOPLE = """
UNWIND $rows AS r
MERGE (p:Person {user_id: r.user_id})
SET p.name = r.name, p.is_active = r.is_active, p.position = r.position,
    p.email = r.email, p.password_hash = r.password_hash
WITH p, r
OPTIONAL MATCH (p)-[old:MEMBER_OF|HAS_ROLE|HAS_SKILL]->()
DELETE old
WITH DISTINCT p, r
MATCH (t:Team {team_id: r.team_id})
MATCH (ro:Role {role_id: r.role_id})
MERGE (p)-[:MEMBER_OF]->(t)
MERGE (p)-[:HAS_ROLE]->(ro)
WITH p, r
UNWIND r.skills AS s
MATCH (k:Skill {skill_id: s.skill_id})
MERGE (p)-[h:HAS_SKILL]->(k)
SET h.level = s.level, h.updated_at = datetime()"""

READ_PEOPLE = """
MATCH (p:Person)
WHERE $user_id IS NULL OR p.user_id = $user_id
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
OPTIONAL MATCH (p)-[:HAS_ROLE]->(r:Role)
OPTIONAL MATCH (p)-[h:HAS_SKILL]->(s:Skill)
WITH p, t, r, collect(s {.skill_id, .name, level: h.level}) AS skills
RETURN p.user_id AS user_id, p.name AS name, p.email AS email, coalesce(p.permission, 'member') AS permission,
       p.position AS position, p.is_active AS is_active,
       t.team_id AS team_id, t.name AS team, r.role_id AS role_id, r.name AS job, skills
ORDER BY toInteger(substring(p.user_id, 1)), p.user_id"""

# 이미 적재된 DB에 계정(이메일 · 비밀번호 해시)만 채운다. 이메일이 비어 있는 사람만, 다른 사람이 쓰는 이메일은 건너뜀.
FILL_ACCOUNTS = """
UNWIND $rows AS r
MATCH (p:Person {user_id: r.user_id})
WHERE p.email IS NULL AND NOT EXISTS { MATCH (o) WHERE (o:Person OR o:Admin) AND o.email = r.email }
SET p.email = r.email, p.password_hash = coalesce(p.password_hash, r.password_hash)
RETURN p.user_id AS user_id"""

# 관리 전용 계정. 처음 만들 때만 값을 넣는다 (이미 있으면 비밀번호 등을 덮어쓰지 않음).
UPSERT_ADMINS = """
UNWIND $rows AS r
MERGE (a:Admin {user_id: r.user_id})
ON CREATE SET a.name = r.name, a.email = r.email, a.password_hash = r.password_hash
RETURN a.user_id AS user_id"""

# 로그인: 이메일로 구성원(Person) 또는 관리 계정(Admin)을 찾는다.
FIND_LOGIN = """
MATCH (n) WHERE (n:Person OR n:Admin) AND n.email = $email
RETURN n.user_id AS user_id, n.name AS name, n.email AS email, n.password_hash AS password_hash,
       CASE WHEN n:Admin THEN 'admin' ELSE coalesce(n.permission, 'member') END AS permission,
       coalesce(n.is_active, true) AS is_active
LIMIT 1"""

READ_CATALOG = {
    "teams": "MATCH (t:Team) RETURN t.team_id AS team_id, t.name AS name ORDER BY name",
    "roles": "MATCH (r:Role) RETURN r.role_id AS role_id, r.name AS name ORDER BY name",
    "skills": "MATCH (s:Skill) RETURN s.skill_id AS skill_id, s.name AS name, s.domain AS domain ORDER BY skill_id",
}

# 웹에서 바꿀 수 있는 Person 속성 (관계가 아닌 값)
PERSON_PROPS = ("name", "email", "position", "permission", "is_active")


class Conflict(ValueError):
    """이미 있는 값과 겹침 (예: 이메일 중복)."""


def check_org(org: dict[str, Any]) -> list[str]:
    """사람이 가리키는 팀·직무·스킬이 모두 있는지 확인한다. 없으면 Cypher MATCH가 조용히 건너뛰기 때문."""
    teams = {t["team_id"] for t in org["teams"]}
    roles = {r["role_id"] for r in org["roles"]}
    skills = {s["skill_id"] for s in org["skills"]}
    errors, seen, emails = [], set(), set()
    for p in org["people"] + org.get("admins", []):
        uid = p["user_id"]
        if uid in seen:
            errors.append(f"{uid}: user_id 중복")
        seen.add(uid)
        if p.get("email") and p["email"] in emails:
            errors.append(f"{uid}: 이메일 중복 {p['email']}")
        emails.add(p.get("email"))
        if p in org.get("admins", []):
            continue  # 관리 계정은 팀 · 직무 · 스킬이 없음
        if p.get("team_id") not in teams:
            errors.append(f"{uid}: 없는 팀 {p.get('team_id')}")
        if p.get("role_id") not in roles:
            errors.append(f"{uid}: 없는 직무 {p.get('role_id')}")
        errors += [f"{uid}: 없는 스킬 {s['skill_id']}" for s in p.get("skills", []) if s["skill_id"] not in skills]
    return errors


def upsert_org(org: dict[str, Any]) -> None:
    """org = {teams, roles, skills, people}. 하나의 트랜잭션으로 넣어 중간 실패 시 아무것도 남기지 않는다."""
    errors = check_org(org)
    if errors:
        raise ValueError("구성원 데이터 오류:\n" + "\n".join(errors))

    def work(tx):
        for query, key in ((UPSERT_TEAMS, "teams"), (UPSERT_ROLES, "roles"),
                           (UPSERT_SKILLS, "skills"), (UPSERT_PEOPLE, "people"), (UPSERT_ADMINS, "admins")):
            tx.run(query, rows=org.get(key, [])).consume()

    with get_driver().session() as session:
        session.execute_write(work)


def _check_refs(tx, team_id: str | None, skills: list[dict] | None) -> None:
    if team_id is not None and not tx.run("MATCH (t:Team {team_id: $id}) RETURN t", id=team_id).single():
        raise ValueError(f"없는 팀: {team_id}")
    if skills:
        ids = [s["skill_id"] for s in skills]
        found = {r["id"] for r in tx.run("MATCH (s:Skill) WHERE s.skill_id IN $ids RETURN s.skill_id AS id", ids=ids)}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(f"없는 스킬: {', '.join(missing)}")


def _check_email(tx, email: str | None, user_id: str | None) -> None:
    # shortcut: 이메일 유일성은 트랜잭션 안에서만 확인, 동시 추가가 잦아지면 schema.cypher에 email 유일성 제약 추가
    if email and tx.run("MATCH (p) WHERE (p:Person OR p:Admin) AND p.email = $email "
                        "AND p.user_id <> coalesce($uid, '') RETURN p",
                        email=email, uid=user_id).single():
        raise Conflict(f"이미 등록된 이메일: {email}")


def _set_relations(tx, user_id: str, team_id: str | None, job: str | None, skills: list[dict] | None) -> None:
    """None인 항목은 그대로 둔다. 값이 있으면 기존 관계를 지우고 새로 잇는다."""
    if team_id is not None:
        tx.run("""MATCH (p:Person {user_id: $uid}) OPTIONAL MATCH (p)-[old:MEMBER_OF]->() DELETE old
                  WITH DISTINCT p MATCH (t:Team {team_id: $team_id}) MERGE (p)-[:MEMBER_OF]->(t)""",
               uid=user_id, team_id=team_id).consume()
    if job is not None:
        # 목록에 없는 직무를 입력하면 Role 노드를 새로 만든다
        tx.run("""MATCH (p:Person {user_id: $uid}) OPTIONAL MATCH (p)-[old:HAS_ROLE]->() DELETE old
                  WITH DISTINCT p MERGE (r:Role {name: $job}) ON CREATE SET r.role_id = 'r-' + randomUUID()
                  MERGE (p)-[:HAS_ROLE]->(r)""", uid=user_id, job=job).consume()
    if skills is not None:
        tx.run("""MATCH (p:Person {user_id: $uid}) OPTIONAL MATCH (p)-[old:HAS_SKILL]->() DELETE old
                  WITH DISTINCT p UNWIND $skills AS s MATCH (k:Skill {skill_id: s.skill_id})
                  MERGE (p)-[h:HAS_SKILL]->(k) SET h.level = s.level, h.updated_at = datetime()""",
               uid=user_id, skills=skills).consume()


def create_person(data: dict[str, Any]) -> dict[str, Any]:
    """data: name, email, team_id, job, position, permission, skills[{skill_id, level}]. user_id는 u{최대+1}."""
    data = {**data, "email": data["email"].strip().lower()}  # 로그인은 소문자로 찾음
    def work(tx) -> str:
        _check_refs(tx, data["team_id"], data["skills"])
        _check_email(tx, data["email"], None)
        last = tx.run("MATCH (p:Person) RETURN max(toInteger(substring(p.user_id, 1))) AS n").single()["n"] or 0
        user_id = f"u{last + 1}"
        props = {k: data[k] for k in PERSON_PROPS if k in data} | {"is_active": True}
        tx.run("CREATE (p:Person {user_id: $uid}) SET p += $props", uid=user_id, props=props).consume()
        _set_relations(tx, user_id, data["team_id"], data["job"], data["skills"])
        return user_id

    with get_driver().session() as session:
        user_id = session.execute_write(work)
    return get_person(user_id)


def update_person(user_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """changes에 들어 있는 항목만 바꾼다 (skills는 목록 전체를 교체). 없는 사람이면 None."""
    if changes.get("email"):
        changes = {**changes, "email": changes["email"].strip().lower()}  # 로그인은 소문자로 찾음
    def work(tx) -> bool:
        if not tx.run("MATCH (p:Person {user_id: $uid}) RETURN p", uid=user_id).single():
            return False
        _check_refs(tx, changes.get("team_id"), changes.get("skills"))
        _check_email(tx, changes.get("email"), user_id)
        props = {k: changes[k] for k in PERSON_PROPS if k in changes}
        if props:
            tx.run("MATCH (p:Person {user_id: $uid}) SET p += $props", uid=user_id, props=props).consume()
        _set_relations(tx, user_id, changes.get("team_id"), changes.get("job"), changes.get("skills"))
        return True

    with get_driver().session() as session:
        found = session.execute_write(work)
    return get_person(user_id) if found else None


def upsert_admins(admins: list[dict[str, Any]]) -> list[str]:
    """관리 계정을 없으면 만든다 (있으면 그대로). user_id 목록을 돌려준다."""
    records, _, _ = get_driver().execute_query(UPSERT_ADMINS, rows=admins)
    return [r["user_id"] for r in records]


def find_login(email: str) -> dict[str, Any] | None:
    """로그인용 계정 (password_hash 포함 — API 응답으로 내보내지 않는다)."""
    records, _, _ = get_driver().execute_query(FIND_LOGIN, email=email)
    return records[0].data() if records else None


def list_catalog(kind: str) -> list[dict[str, Any]]:
    """kind: teams | roles | skills"""
    records, _, _ = get_driver().execute_query(READ_CATALOG[kind])
    return [r.data() for r in records]


def fill_accounts(people_rows: list[dict[str, Any]]) -> list[str]:
    """이메일이 비어 있는 사람에게만 이메일 · 비밀번호 해시를 넣고, 넣은 user_id를 돌려준다."""
    rows = [{k: p[k] for k in ("user_id", "email", "password_hash")} for p in people_rows if p.get("email")]
    records, _, _ = get_driver().execute_query(FILL_ACCOUNTS, rows=rows)
    return [r["user_id"] for r in records]


def list_people() -> list[dict[str, Any]]:
    records, _, _ = get_driver().execute_query(READ_PEOPLE, user_id=None)
    return [r.data() for r in records]


def get_person(user_id: str) -> dict[str, Any] | None:
    records, _, _ = get_driver().execute_query(READ_PEOPLE, user_id=user_id)
    return records[0].data() if records else None
