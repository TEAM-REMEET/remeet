"""구성원 조회·추가·수정과 팀·직무·스킬 목록. 관리자 구성원 관리, 회의 참석자 선택에서 쓴다.

모든 API는 로그인 토큰이 있어야 하고, 추가 · 수정은 관리자 토큰이어야 한다 (nginx가 /api/를 외부에 공개하므로).
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.adapters.neo4j import people
from followup.adapters.postgres import people as pg_people
from followup.entrypoints.api.routes.auth import current_user, require_admin

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["people"], dependencies=[Depends(current_user)])

Position = Literal["senior", "mid", "junior"]
Permission = Literal["admin", "member"]
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Skill(BaseModel):
    skill_id: str
    name: str | None
    level: int | None


class Person(BaseModel):
    user_id: str
    name: str
    email: str | None
    permission: Permission      # admin: 관리자 / member: 구성원 (Neo4j Role은 직무라 이름을 나눔)
    position: str | None        # senior | mid | junior
    is_active: bool | None
    team_id: str | None
    team: str | None            # 팀 이름
    role_id: str | None
    job: str | None             # 직무 이름 (예: 산업 디자이너)
    skills: list[Skill]


class SkillLevel(BaseModel):
    skill_id: str
    level: int = Field(ge=1, le=4)


class PersonCreate(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(pattern=EMAIL_PATTERN)
    team_id: str
    job: str = Field(min_length=1)  # 목록에 없으면 새 직무로 만든다
    position: Position
    permission: Permission = "member"
    skills: list[SkillLevel] = []


class PersonUpdate(BaseModel):
    """보낸 항목만 바꾼다. skills는 목록 전체를 교체한다."""
    name: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN)
    team_id: str | None = None
    job: str | None = Field(default=None, min_length=1)
    position: Position | None = None
    permission: Permission | None = None
    is_active: bool | None = None
    skills: list[SkillLevel] | None = None


def _sync_postgres(person: dict) -> None:
    """Neo4j 에 저장한 사람을 PostgreSQL 에도 반영 (회의 권한 · 외래 키가 accounts · people 을 봄).

    Neo4j 는 이미 저장됐으므로 여기서 실패해도 되돌리지 않고 기록만 남김 (같은 요청을 다시 보내면 중복 생성).
    어긋나면: PYTHONPATH=src python -m followup.entrypoints.seed_postgres sync-from-neo4j
    """
    try:
        pg_people.sync_people([person])
    except Exception:  # noqa: BLE001 - PostgreSQL 이 꺼져 있어도 구성원 관리는 되게
        logger.exception("PostgreSQL 구성원 반영 실패: %s (sync-from-neo4j 로 다시 맞추세요)", person.get("user_id"))

def _bad_request(e: ValueError) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(e, people.Conflict) else 400, detail=str(e))


@router.get("/people", response_model=list[Person])
def list_people():
    return people.list_people()


@router.get("/people/{user_id}", response_model=Person)
def get_person(user_id: str):
    person = people.get_person(user_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"구성원 {user_id}를 찾을 수 없습니다.")
    return person


@router.post("/people", response_model=Person, status_code=201, dependencies=[Depends(require_admin)])
def create_person(body: PersonCreate):
    try:
        person = people.create_person(body.model_dump())
    except ValueError as e:
        raise _bad_request(e) from e
    _sync_postgres(person)
    return person


@router.patch("/people/{user_id}", response_model=Person, dependencies=[Depends(require_admin)])
def update_person(user_id: str, body: PersonUpdate):
    try:
        person = people.update_person(user_id, body.model_dump(exclude_none=True))  # null은 "안 바꿈"으로 본다
    except ValueError as e:
        raise _bad_request(e) from e
    if person is None:
        raise HTTPException(status_code=404, detail=f"구성원 {user_id}를 찾을 수 없습니다.")
    _sync_postgres(person)
    return person


@router.get("/teams")
def list_teams() -> list[dict]:
    return people.list_catalog("teams")


@router.get("/roles")
def list_roles() -> list[dict]:
    return people.list_catalog("roles")


@router.get("/skills")
def list_skills() -> list[dict]:
    return people.list_catalog("skills")
