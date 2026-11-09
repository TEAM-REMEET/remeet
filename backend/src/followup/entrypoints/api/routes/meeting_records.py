"""회의록 저장 · 조회 (PostgreSQL). AI 요약(summary_routes · meeting-jobs)이 만든 결과를 회의록으로 남기고 화면에 보여 줌.

작성 흐름 (adapters/postgres/compose.py · 화면 「새 회의록」)
    POST /api/drafts                          미리보기 저장 (새로 만들기 · meeting_id 를 주면 그 미리보기를 고쳐 저장,
                                              아직 없는 id 면 그 id 로 새로 만듦 — AI 요약의 meeting_id 를 그대로)
                                              ⚠️ 고쳐 저장할 때도 전체(프로젝트 · 참석자 · 원문 …)를 다시 보내요. 빠진 값은 비워짐
    GET  /api/drafts                          내가 쓰다 만 미리보기, 최근 저장 순
    POST /api/meetings/{meeting_id}/handoff   (작성자 ≠ 주관자) 분석이 끝난 미리보기를 주관자에게 넘김 · 주관자에게 알림
                                              본문 {request: 작성 화면 입력값, draft: {nn_name …}} · 이후 작성자는 못 고침
    GET  /api/meetings/{meeting_id}/handoff   (주관자) 넘겨받은 입력값 + 요약 JSON → 작성 2단계를 다시 열기
    POST /api/meetings/{meeting_id}/submit    회의록 생성 (draft → review) · 참석자 · 주관자에게 알림 (주관자는 시니어 · 미드만)
    POST /api/meetings/{meeting_id}/confirm   확정 (review → confirmed) · 주관자만
    GET  /api/review-queue                    [홈 · 회의록 생성 검토 대기] 나에게 넘어온 회의록 (kind: handoff | review)

회의 상세 (adapters/postgres/detail.py · minutes.py) — 응답은 web Meeting 과 같은 모양
    GET  /api/meetings/{meeting_id}?version=  상세 (요약 · 안건 · 원문 · 할 일 · 공유 정보). version 없으면 최신
    GET  /api/meetings/{meeting_id}/summaries 요약 버전 이력
    PUT  /api/meetings/{meeting_id}/summary   요약 새 버전 저장 (화면 「편집」 · 주관자 · 검토자)
    GET  /api/meetings/{meeting_id}/summary-json  담당자 추천에 보낼 MeetingSummary JSON (저장해 둔 것)
    PUT  /api/meetings/{meeting_id}/attendees 참석자 바꾸기 (주관자는 늘 포함)
    GET  /api/meetings/{meeting_id}/sources   원본 파일 버전 목록 · GET .../sources/latest 최신 원본 내용 (원문 볼 권한)
    POST /api/meetings/{meeting_id}/branch-requests  안건 분리 요청 · GET 받은 요청 (주관자)
    DELETE /api/meetings/{meeting_id}         회의 삭제 (고칠 수 있는 사람 · 목록에서 사라짐)

결과 전달 (상태: confirmed → sending → done | fail · 다시 보내면 done → sending)
    PUT  /api/meetings/{meeting_id}/status    상태 바꾸기 {status: sending | done | fail}
    POST /api/meetings/{meeting_id}/delivery/finish  외부 도구 전송이 끝남 → 남은 실패가 있으면 fail, 없으면 done

액션 아이템
    GET    /api/meetings/{meeting_id}/todos?deleted=  목록 (deleted=true 면 삭제한 것)
    POST   /api/meetings/{meeting_id}/todos           직접 추가
    PATCH  /api/todos/{todo_id}                       고치기 (내용 · 담당자 · 마감일, 보낸 것만)
    DELETE /api/todos/{todo_id}                       삭제 (되돌리기 가능)
    POST   /api/todos/{todo_id}/restore               되돌리기
    PUT    /api/todos/{todo_id}/owners                담당자 (여러 명 · 빈 목록이면 비우기)
    PUT    /api/todos/{todo_id}/due                   마감일

content · todos 모양은 web 목업 meetings.json 과 같아요 (detail.py 맨 위 주석).
AI 결과(MeetingSummary)를 화면 모양으로 바꾸는 건 web state/summary_view.py 가 이미 하고 있어서, 그 결과를 그대로 보내면 돼요.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from followup.adapters.postgres import compose, detail, integrations, meetings, minutes
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api", tags=["meeting-records"])


class Draft(BaseModel):
    content: dict[str, Any]                       # summary · discussion · decisions · open_issues · results · agendas
    meeting_id: str | None = None                 # 없으면 새로 만들기
    title: str = ""
    directory_id: str = "normal"                  # weekly | monthly | normal
    project_id: str | None = None
    host_id: str | None = None                    # 없으면 나
    held_at: datetime | None = None
    time_label: str = ""
    file_name: str = ""
    attendees: list[str] | None = None
    transcript: list[dict[str, Any]] | None = None  # [{line_id, speaker, text, speaker_role?}]
    todos: list[dict[str, Any]] | None = None       # [{text, owners, due, src, urgent, item_id?}]
    summary_json: dict[str, Any] | None = None      # 검토가 끝난 MeetingSummary (담당자 추천용 원본)


class Submit(BaseModel):
    reviewer_id: str | None = None                # 더 이상 쓰지 않음 (주면 400) · 주관자(시니어 · 미드)가 윗사람 역할


class Handoff(BaseModel):
    request: dict[str, Any]                       # 작성 화면 입력값 (POST /api/meeting-jobs 본문 그대로)
    draft: dict[str, Any] | None = None           # 화면에서만 쓰는 값 (새 프로젝트 이름 nn_name 등)


class SummaryVersion(BaseModel):
    content: dict[str, Any]
    todos: list[dict[str, Any]] | None = None
    summary_json: dict[str, Any] | None = None


class Attendees(BaseModel):
    user_ids: list[str]


class TodoCreate(BaseModel):
    text: str = Field(min_length=1)
    owners: list[str] = []
    due: str = ""                                 # 화면 표기 (11/5 · 오늘)
    due_date: str | None = None                   # YYYY-MM-DD
    src: list[str] = []
    urgent: bool = False
    agenda: str = ""                              # 고른 안건 제목 (그 안건 아래에 보여 줌)


class TodoUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    owners: list[str] | None = None
    due: str | None = None
    due_date: str | None = None
    agenda: str | None = None


class Owners(BaseModel):
    owners: list[str]


class Due(BaseModel):
    due: str
    due_date: str | None = None


class Status(BaseModel):
    status: Literal["sending", "done", "fail"]


# ---------------------------------------------------------------- 작성 흐름
@router.post("/drafts", status_code=201)
def save_draft(body: Draft, me: str = Depends(actor)):
    return compose.save_draft(actor_id=me, **body.model_dump())


@router.get("/drafts")
def my_drafts(me: str = Depends(actor)):
    return compose.list_my_drafts(actor_id=me)


@router.post("/meetings/{meeting_id}/handoff")
def handoff(meeting_id: str, body: Handoff, me: str = Depends(actor)):
    row = compose.handoff_meeting(actor_id=me, meeting_id=meeting_id, request=body.request, draft=body.draft)
    return {"id": row["id"], "host_user_id": row["host_user_id"], "handed_off_at": row["handed_off_at"]}


@router.get("/meetings/{meeting_id}/handoff")
def get_handoff(meeting_id: str, me: str = Depends(actor)):
    return compose.get_handoff(actor_id=me, meeting_id=meeting_id)


@router.post("/meetings/{meeting_id}/submit")
def submit(meeting_id: str, body: Submit, me: str = Depends(actor)):
    return compose.submit_meeting(actor_id=me, meeting_id=meeting_id, reviewer_id=body.reviewer_id)


@router.post("/meetings/{meeting_id}/confirm")
def confirm(meeting_id: str, me: str = Depends(actor)):
    return compose.confirm_meeting(actor_id=me, meeting_id=meeting_id)


@router.get("/review-queue")
def review_queue(me: str = Depends(actor)):
    return compose.list_review_queue(actor_id=me)


# ---------------------------------------------------------------- 상세
@router.get("/meetings/{meeting_id}")
def meeting_detail(meeting_id: str, version: int | None = Query(None, ge=1), me: str = Depends(actor)):
    return detail.get_meeting_detail(actor_id=me, meeting_id=meeting_id, version=version)


@router.get("/meetings/{meeting_id}/summaries")
def summary_versions(meeting_id: str, me: str = Depends(actor)):
    return detail.list_summary_versions(actor_id=me, meeting_id=meeting_id)


@router.put("/meetings/{meeting_id}/summary", status_code=201)
def save_summary(meeting_id: str, body: SummaryVersion, me: str = Depends(actor)):
    return detail.save_summary(actor_id=me, meeting_id=meeting_id, **body.model_dump())


@router.get("/meetings/{meeting_id}/summary-json")
def summary_json(meeting_id: str, me: str = Depends(actor)):
    return detail.get_summary_json(actor_id=me, meeting_id=meeting_id)


@router.put("/meetings/{meeting_id}/attendees")
def set_attendees(meeting_id: str, body: Attendees, me: str = Depends(actor)):
    return detail.set_attendees(actor_id=me, meeting_id=meeting_id, user_ids=body.user_ids)


@router.get("/meetings/{meeting_id}/sources")
def sources(meeting_id: str, me: str = Depends(actor)):
    return minutes.list_sources(actor_id=me, meeting_id=meeting_id)


@router.get("/meetings/{meeting_id}/sources/latest")
def latest_source(meeting_id: str, me: str = Depends(actor)):
    return minutes.get_source(actor_id=me, meeting_id=meeting_id)


@router.post("/meetings/{meeting_id}/branch-requests", status_code=204)
def request_branch(meeting_id: str, me: str = Depends(actor)):
    detail.request_branch(actor_id=me, meeting_id=meeting_id)


@router.get("/meetings/{meeting_id}/branch-requests")
def branch_requests(meeting_id: str, me: str = Depends(actor)):
    return detail.list_branch_requests(actor_id=me, meeting_id=meeting_id)


# ---------------------------------------------------------------- 액션 아이템
@router.get("/meetings/{meeting_id}/todos")
def todos(meeting_id: str, deleted: bool = False, me: str = Depends(actor)):
    return detail.list_todos(actor_id=me, meeting_id=meeting_id, deleted=deleted)


@router.post("/meetings/{meeting_id}/todos", status_code=201)
def add_todo(meeting_id: str, body: TodoCreate, me: str = Depends(actor)):
    return detail.add_todo(actor_id=me, meeting_id=meeting_id, **body.model_dump())


@router.patch("/todos/{todo_id}")
def update_todo(todo_id: int, body: TodoUpdate, me: str = Depends(actor)):
    return detail.update_todo(actor_id=me, todo_id=todo_id, **body.model_dump())


@router.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int, me: str = Depends(actor)):
    detail.delete_todo(actor_id=me, todo_id=todo_id)


@router.post("/todos/{todo_id}/restore", status_code=204)
def restore_todo(todo_id: int, me: str = Depends(actor)):
    detail.restore_todo(actor_id=me, todo_id=todo_id)


@router.put("/todos/{todo_id}/owners")
def set_owners(todo_id: int, body: Owners, me: str = Depends(actor)):
    return detail.set_todo_owners(actor_id=me, todo_id=todo_id, owners=body.owners)


@router.put("/todos/{todo_id}/due")
def set_due(todo_id: int, body: Due, me: str = Depends(actor)):
    row = detail.set_todo_due(actor_id=me, todo_id=todo_id, due=body.due, due_date=body.due_date)
    return {"id": row["id"], "due": row["due_label"], "due_date": row["due_date"]}   # 목록과 같은 이름(due)


# ---------------------------------------------------------------- 삭제 · 결과 전달 상태
@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: str, me: str = Depends(actor)):
    meetings.delete_meeting(actor_id=me, meeting_id=meeting_id)


@router.put("/meetings/{meeting_id}/status")
def set_status(meeting_id: str, body: Status, me: str = Depends(actor)):
    row = meetings.update_meeting(actor_id=me, meeting_id=meeting_id, status=body.status)
    return {"id": row["id"], "status": row["status"]}


@router.post("/meetings/{meeting_id}/delivery/finish")
def finish_delivery(meeting_id: str, me: str = Depends(actor)):
    return {"id": meeting_id, "status": integrations.finish_delivery(actor_id=me, meeting_id=meeting_id)}