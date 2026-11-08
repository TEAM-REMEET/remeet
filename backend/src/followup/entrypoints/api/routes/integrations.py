"""외부 연동 상태 · 전송 실패 (PostgreSQL).

GET  /api/integrations/organization                   조직 이름 (연동 화면 상단)
GET  /api/integrations/tools                          도구 목록 · 연결 상태 · 마지막 동기화
PUT  /api/integrations/tools/{tool_id}/status         연결 상태 바꾸기 (관리자)
GET  /api/integrations/failures?meeting_id=&open_only= 전송 실패 기록 (관리자는 전체, 사용자는 회의 단위로만)
POST /api/integrations/failures                     보내기 실패 기록 (회의를 고칠 수 있는 사람 · tech 면 관리자에게 알림)
POST /api/integrations/exports                      보내기 성공 기록 (도구의 마지막 전송 시각)
POST /api/integrations/failures/{failure_id}/resolve  다시 보내서 해결됨 (simple: 주관자 · tech: 관리자)
     → 그 회의에 남은 실패가 없으면 회의가 done 이 되고 주관자에게 '전달 완료' 알림

실패 기록 · 전송 완료 기록(record_failure · record_export · finish_delivery)은 실제로 보내는 서버 코드가 부르므로
API 로 열지 않았어요.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from followup.adapters.postgres import integrations
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class ToolStatus(BaseModel):
    status: Literal["on", "off", "expired"]


class Failure(BaseModel):
    meeting_id: str
    tool_id: str
    kind: Literal["simple", "tech"]
    reason: str = Field(min_length=1)
    code: str = ""


class Export(BaseModel):
    meeting_id: str
    tool_id: str


@router.get("/organization")
def organization(me: str = Depends(actor)):
    return {"name": integrations.organization(actor_id=me)}


@router.get("/tools")
def tools(me: str = Depends(actor)):
    return integrations.list_tools(actor_id=me)


@router.put("/tools/{tool_id}/status")
def set_status(tool_id: str, body: ToolStatus, me: str = Depends(actor)):
    return integrations.set_tool_status(actor_id=me, tool_id=tool_id, status=body.status)


@router.get("/failures")
def failures(meeting_id: str | None = None, open_only: bool = False, me: str = Depends(actor)):
    return integrations.list_failures(actor_id=me, meeting_id=meeting_id, open_only=open_only)


@router.post("/failures", status_code=201)
def record_failure(body: Failure, me: str = Depends(actor)):
    return integrations.record_failure(actor_id=me, **body.model_dump())


@router.post("/exports", status_code=204)
def record_export(body: Export, me: str = Depends(actor)):
    integrations.record_export(actor_id=me, meeting_id=body.meeting_id, tool_id=body.tool_id)


@router.post("/failures/{failure_id}/resolve")
def resolve(failure_id: str, me: str = Depends(actor)):
    return integrations.resolve_failure(actor_id=me, failure_id=failure_id)