"""users.json · skills.json (backend/seed) → PostgreSQL 사람 · 조직 · 스킬 표.

Neo4j 쪽은 entrypoints/seed_people.py 가 같은 파일로 채운다. 이 파일은 PostgreSQL 쪽만.
실행은 entrypoints/seed_postgres.py 의 people 명령으로.

비밀번호는 넣지 않는다 (password_hash = NULL). 로그인은 Neo4j 가 담당하고, PostgreSQL accounts 는
외래 키가 가리키는 사람 ID 명부 역할만 한다. 이미 있는 계정은 이메일 · 권한을 바꾸지 않는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[4] / "seed"  # backend/seed
POSITIONS = {"junior", "mid", "senior"}


def read_org(seed_dir: Path) -> dict:
    org = json.loads((seed_dir / "users.json").read_text(encoding="utf-8"))
    org["skills"] = json.loads((seed_dir / "skills.json").read_text(encoding="utf-8"))
    return org


def check_org(org: dict) -> list[str]:
    """FK · CHECK 에 걸릴 값을 DB 에 넣기 전에 찾는다 (DB 오류보다 어디가 틀렸는지 알기 쉽게)."""
    teams = {t["team_id"] for t in org["teams"]}
    roles = {r["role_id"] for r in org["roles"]}
    skills = {s["skill_id"] for s in org["skills"]}
    errors = [f"{r['role_id']}: 없는 팀 {r['team_id']}" for r in org["roles"] if r["team_id"] not in teams]
    seen, emails = set(), set()
    for p in org["people"] + org.get("admins", []):
        uid = p["user_id"]
        if uid in seen:
            errors.append(f"{uid}: user_id 중복")
        if p.get("email") in emails:
            errors.append(f"{uid}: 이메일 중복 {p.get('email')}")
        seen.add(uid)
        emails.add(p.get("email"))
    for p in org["people"]:
        uid = p["user_id"]
        if p.get("team_id") not in teams:
            errors.append(f"{uid}: 없는 팀 {p.get('team_id')}")
        if p.get("role_id") not in roles:
            errors.append(f"{uid}: 없는 직무 {p.get('role_id')}")
        if p.get("position") not in POSITIONS:
            errors.append(f"{uid}: 직급은 junior · mid · senior 중 하나 ({p.get('position')})")
        for s in p.get("skills", []):
            if s["skill_id"] not in skills:
                errors.append(f"{uid}: 없는 스킬 {s['skill_id']}")
            if not 1 <= s["level"] <= 4:
                errors.append(f"{uid}: {s['skill_id']} 레벨은 1~4 ({s['level']})")
    return errors


def build_rows(org: dict) -> dict[str, list[tuple]]:
    """표 이름 → 넣을 행(튜플) 목록. 넣는 순서(외래 키 순서)대로."""
    return {
        "teams": [(t["team_id"], t["name"], t.get("is_active", True)) for t in org["teams"]],
        "roles": [(r["role_id"], r["name"], r["team_id"]) for r in org["roles"]],
        "skills": [(s["skill_id"], s["name"], s.get("description") or "", s["domain"],
                    [x.strip() for x in (s.get("tools") or "").split("|") if x.strip()],
                    s.get("source"), s.get("ncs_code"), s.get("ncs_name")) for s in org["skills"]],
        "accounts": [(p["user_id"], p["email"], "USER") for p in org["people"]]
                    + [(a["user_id"], a["email"], "ADMIN") for a in org.get("admins", [])],
        "people": [(p["user_id"], p["name"], p["position"], p["team_id"], p["role_id"], p.get("is_active", True))
                   for p in org["people"]],
        "person_skills": [(p["user_id"], s["skill_id"], s["level"]) for p in org["people"] for s in p.get("skills", [])],
    }


UPSERTS = {
    "teams": """INSERT INTO teams (team_id, name, is_active) VALUES (%s, %s, %s)
        ON CONFLICT (team_id) DO UPDATE SET name = EXCLUDED.name, is_active = EXCLUDED.is_active""",
    "roles": """INSERT INTO roles (role_id, name, team_id) VALUES (%s, %s, %s)
        ON CONFLICT (role_id) DO UPDATE SET name = EXCLUDED.name, team_id = EXCLUDED.team_id""",
    "skills": """INSERT INTO skills (skill_id, name, description, domain, tools, source, ncs_code, ncs_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (skill_id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
            domain = EXCLUDED.domain, tools = EXCLUDED.tools, source = EXCLUDED.source,
            ncs_code = EXCLUDED.ncs_code, ncs_name = EXCLUDED.ncs_name""",
    # 이미 있는 계정은 그대로 (seed.py 데모 계정의 로그인 이메일을 덮어쓰지 않게). 다른 user_id 와 이메일이 겹치면 오류
    "accounts": """INSERT INTO accounts (user_id, email, role) VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO NOTHING""",
    "people": """INSERT INTO people (user_id, name, position, team_id, role_id, is_active) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name, position = EXCLUDED.position,
            team_id = EXCLUDED.team_id, role_id = EXCLUDED.role_id, is_active = EXCLUDED.is_active, updated_at = now()""",
    "person_skills": """INSERT INTO person_skills (user_id, skill_id, level) VALUES (%s, %s, %s)
        ON CONFLICT (user_id, skill_id) DO UPDATE SET level = EXCLUDED.level, updated_at = now()""",
}


def write(rows: dict[str, list[tuple]], force: bool) -> None:
    from followup.adapters.postgres.db import transaction  # --check 는 psycopg 없이도 돌게 여기서 import

    with transaction() as cur:  # 전부 성공 아니면 전부 취소
        cur.execute("SELECT count(*) AS n FROM people")
        existing = cur.fetchone()["n"]
        if existing and not force:
            raise SystemExit(f"[거부] people 에 이미 {existing}명이 있습니다. 덮어쓰려면 --force (웹 수정분 유실).")
        for table, sql in UPSERTS.items():
            cur.executemany(sql, rows[table])
