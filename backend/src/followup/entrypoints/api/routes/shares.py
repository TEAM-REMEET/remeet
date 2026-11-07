"""회의 공유 (PostgreSQL).

POST   /api/meetings/{meeting_id}/shares   공유하기 (사람 또는 팀 하나, 범위 summary | full, 원문 잠금)
GET    /api/meetings/{meeting_id}/shares   이 회의의 공유 목록 (고칠 수 있는 사람만)
PATCH  /api/shares/{share_id}              범위 · 원문 잠금 바꾸기
DELETE /api/shares/{share_id}              공유 취소
GET    /api/shares/sent                    [공유한 회의] 내가 보낸 공유, 최근 순
GET    /api/shares/received                [공유 받은 회의] 나에게 온 공유, 최근 받은 순
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from followup.adapters.postgres import shares
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api", tags=["shares"])

Scope = Literal["summary", "full"]


class ShareCreate(BaseModel):
    recipient_user_id: str | None = None
    recipient_team_id: str | None = None   # 둘 중 하나만
    scope: Scope = "summary"
    lock_raw: bool = False


class ShareUpdate(BaseModel):
    scope: Scope
    lock_raw: bool


@router.get("/shares/sent")
def sent(me: str = Depends(actor)):
    return shares.list_sent(actor_id=me)


@router.get("/shares/received")
def received(me: str = Depends(actor)):
    return shares.list_received(actor_id=me)


@router.post("/meetings/{meeting_id}/shares", status_code=201)
def create(meeting_id: str, body: ShareCreate, me: str = Depends(actor)):
    return shares.create_share(actor_id=me, meeting_id=meeting_id, **body.model_dump())


@router.get("/meetings/{meeting_id}/shares")
def list_for_meeting(meeting_id: str, me: str = Depends(actor)):
    return shares.list_shares(actor_id=me, meeting_id=meeting_id)


@router.patch("/shares/{share_id}")
def update(share_id: int, body: ShareUpdate, me: str = Depends(actor)):
    return shares.update_share(actor_id=me, share_id=share_id, scope=body.scope, lock_raw=body.lock_raw)


@router.delete("/shares/{share_id}")
def revoke(share_id: int, me: str = Depends(actor)):
    return shares.revoke_share(actor_id=me, share_id=share_id)