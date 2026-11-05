"""구성원 조회·추가·수정과 팀·직무·스킬 목록. 관리자 구성원 관리, 회의 참석자 선택에서 쓴다.

추가 · 수정은 관리자 토큰이 있어야 한다.
shortcut: 조회(GET)는 로그인 없이 열려 있음, 외부에 공개 배포하기 전에 current_user로 막는다.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.adapters.neo4j import people
from followup.entrypoints.api.routes.auth import require_admin

router = APIRouter(prefix="/api", tags=["people"])

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
        return people.create_person(body.model_dump())
    except ValueError as e:
        raise _bad_request(e) from e


@router.patch("/people/{user_id}", response_model=Person, dependencies=[Depends(require_admin)])
def update_person(user_id: str, body: PersonUpdate):
    try:
        person = people.update_person(user_id, body.model_dump(exclude_none=True))  # null은 "안 바꿈"으로 본다
    except ValueError as e:
        raise _bad_request(e) from e
    if person is None:
        raise HTTPException(status_code=404, detail=f"구성원 {user_id}를 찾을 수 없습니다.")
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
