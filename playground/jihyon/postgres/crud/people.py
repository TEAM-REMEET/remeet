"""구성원 추가 · 수정을 PostgreSQL 에 반영 (Neo4j 와 이중 쓰기).

backend 에서는 user_id(u{번호}) · 새 직무의 role_id 를 Neo4j 가 정한다. 그래서 순서는
    1) backend people.create_person / update_person  → Neo4j 저장, 저장된 사람(dict)을 돌려줌
    2) sync_people([그 사람])                         → 같은 값을 PostgreSQL 에 반영
추가 · 수정 · 비활성화(is_active=False) · 스킬 교체 모두 이 함수 하나로 한다.
Neo4j 와 어긋났을 때는 people.list_people() 전체를 넘기면 다시 맞춰진다.

권한 검사는 하지 않는다: backend API 가 require_admin 으로 이미 막은 뒤 부르는 반영 함수라서.
"""

from __future__ import annotations

from db import transaction
from seed_people import UPSERTS  # people · person_skills upsert 는 시드와 같은 SQL

from .common import ValidationError, required

ACCOUNT = """INSERT INTO accounts (user_id, email, role) VALUES (%s, %s, %s)
    ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role"""
# shortcut: 웹에서 새로 만든 직무(Neo4j Role 에 팀이 없음)는 처음 맡은 사람의 팀으로 기록, 직무 관리 화면이 생기면 거기서 팀 지정
ROLE = """INSERT INTO roles (role_id, name, team_id) VALUES (%s, %s, %s) ON CONFLICT (role_id) DO NOTHING"""
# backend 는 스킬 목록 전체를 교체하므로, 목록에 없는 스킬은 지운다
DROP_SKILLS = "DELETE FROM person_skills WHERE user_id = %s AND NOT (skill_id = ANY(%s))"


def person_rows(p: dict) -> dict:
    """backend Person(dict) → 표마다 넣을 값. 빠진 값은 PostgreSQL NOT NULL 에 걸리므로 여기서 막는다."""
    uid = required(p.get("user_id"), "user_id")
    team_id, role_id = required(p.get("team_id"), "팀"), required(p.get("role_id"), "직무")
    if p.get("position") not in {"junior", "mid", "senior"}:
        raise ValidationError(f"{uid}: 직급은 junior · mid · senior 중 하나여야 합니다.")
    skills = [(uid, s["skill_id"], s["level"]) for s in p.get("skills") or []]
    return {
        "account": (uid, required(p.get("email"), "이메일"), "ADMIN" if p.get("permission") == "admin" else "USER"),
        "role": (role_id, required(p.get("job"), "직무 이름"), team_id),
        "person": (uid, required(p.get("name"), "이름"), p["position"], team_id, role_id,
                   p.get("is_active") is not False),
        "skills": skills,
        "skill_ids": [s[1] for s in skills],
    }


def sync_people(people: list[dict]) -> int:
    """backend people.get_person() 형태의 사람 목록을 PostgreSQL 에 반영. 반영한 수를 돌려준다.
    한 트랜잭션: 한 명이라도 실패하면 전부 취소."""
    rows = [person_rows(p) for p in people]  # DB 에 쓰기 전에 전부 검사
    with transaction() as cur:
        for r in rows:
            cur.execute(ACCOUNT, r["account"])
            cur.execute(ROLE, r["role"])
            cur.execute(UPSERTS["people"], r["person"])
            cur.execute(DROP_SKILLS, (r["person"][0], r["skill_ids"]))
            cur.executemany(UPSERTS["person_skills"], r["skills"])
    return len(rows)
