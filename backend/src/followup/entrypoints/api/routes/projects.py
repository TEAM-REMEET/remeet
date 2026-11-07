"""프로젝트(폴더) · 폴더 트리 · 회의 목록 · 상태 이력 (PostgreSQL). 회의록 작성 · 상세는 다른 라우터 담당.

GET    /api/folders/tree                         사이드바: 정기 회의 트리 + 프로젝트 트리
GET    /api/meeting-types                        정기 회의 종류 (이름 · 설명 · 내가 못 보는 회의 수)
GET    /api/meeting-types/{type_id}/meetings     정기 회의(weekly · monthly · normal) 안의 회의, 최신순
GET    /api/meetings                             내가 볼 수 있는 전체 회의, 최신순   (POST /api/meetings 는 요약 생성)
GET    /api/meetings/{meeting_id}/status-history 상태 변경 이력 (누가 · 언제)
GET    /api/projects                             내 프로젝트 목록
POST   /api/projects                             프로젝트 만들기 (만든 사람 = 주관자)
GET    /api/projects/{project_id}                프로젝트 하나 (+ members: 멤버 user_id, 주관자 먼저)
GET    /api/projects/{project_id}/members        멤버 user_id 목록
PATCH  /api/projects/{project_id}                이름 바꾸기 (주관자)
DELETE /api/projects/{project_id}                지우기 (주관자)
POST   /api/projects/{project_id}/members        멤버 추가 (주관자)
DELETE /api/projects/{project_id}/members/{user_id}
GET    /api/projects/{project_id}/meetings       프로젝트 안 회의, 최신순
GET    /api/projects/{project_id}/briefs         폴더 브리프
PUT    /api/projects/{project_id}/briefs         폴더 브리프 전체 교체 (주관자)

권한 검사는 adapters/postgres 함수가 하고, 오류 → HTTP 상태는 entrypoints/api/errors.py.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from followup.adapters.postgres import folders, meetings, minutes
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    members: list[str] = []


class ProjectUpdate(BaseModel):
    name: str = Field(min_length=1)


class MemberAdd(BaseModel):
    user_id: str


class Brief(BaseModel):
    label: str = Field(min_length=1)
    text: str


# ---------------------------------------------------------------- 트리 · 목록
@router.get("/folders/tree")
def folder_tree(me: str = Depends(actor)):
    return {"regular": minutes.list_regular_tree(actor_id=me), "projects": minutes.list_project_tree(actor_id=me)}


@router.get("/meeting-types")
def meeting_types(me: str = Depends(actor)):
    return minutes.list_meeting_types(actor_id=me)


@router.get("/meeting-types/{type_id}/meetings")
def regular_meetings(type_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     me: str = Depends(actor)):
    return minutes.list_regular_meetings(actor_id=me, directory_id=type_id, limit=limit, offset=offset)


@router.get("/meetings")
def all_meetings(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), me: str = Depends(actor)):
    return minutes.list_all_meetings(actor_id=me, limit=limit, offset=offset)


@router.get("/meetings/{meeting_id}/status-history")
def status_history(meeting_id: str, me: str = Depends(actor)):
    return meetings.list_status_history(actor_id=me, meeting_id=meeting_id)


# ---------------------------------------------------------------- 프로젝트
@router.get("/projects")
def list_projects(me: str = Depends(actor)):
    return minutes.list_project_folders(actor_id=me)


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, me: str = Depends(actor)):
    return folders.create_folder(actor_id=me, name=body.name, members=body.members)


@router.get("/projects/{project_id}")
def get_project(project_id: str, me: str = Depends(actor)):
    project = folders.get_folder(actor_id=me, folder_id=project_id)
    return {**project, "members": folders.list_members(actor_id=me, folder_id=project_id)}


@router.get("/projects/{project_id}/members")
def project_members(project_id: str, me: str = Depends(actor)):
    return folders.list_members(actor_id=me, folder_id=project_id)


@router.patch("/projects/{project_id}")
def rename_project(project_id: str, body: ProjectUpdate, me: str = Depends(actor)):
    return folders.update_folder(actor_id=me, folder_id=project_id, name=body.name)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, me: str = Depends(actor)):
    return folders.delete_folder(actor_id=me, folder_id=project_id)


@router.post("/projects/{project_id}/members", status_code=204)
def add_member(project_id: str, body: MemberAdd, me: str = Depends(actor)):
    folders.add_member(actor_id=me, folder_id=project_id, user_id=body.user_id)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
def remove_member(project_id: str, user_id: str, me: str = Depends(actor)):
    folders.remove_member(actor_id=me, folder_id=project_id, user_id=user_id)


@router.get("/projects/{project_id}/meetings")
def project_meetings(project_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     me: str = Depends(actor)):
    return minutes.list_project_meetings(actor_id=me, project_id=project_id, limit=limit, offset=offset)


@router.get("/projects/{project_id}/briefs", response_model=list[Brief])
def get_briefs(project_id: str, me: str = Depends(actor)):
    return folders.list_briefs(actor_id=me, folder_id=project_id)


@router.put("/projects/{project_id}/briefs", response_model=list[Brief])
def set_briefs(project_id: str, body: list[Brief], me: str = Depends(actor)):
    return folders.set_briefs(actor_id=me, folder_id=project_id, items=[b.model_dump() for b in body])