"""알림 (PostgreSQL). 알림을 만드는 건 서버 안(회의록 생성 · 공유 · 전송 실패 …)이라 여기에는 읽기 · 읽음 · 삭제만.

GET    /api/notifications?unread_only=true   내 알림, 최근 순
PATCH  /api/notifications/{notification_id}  읽음 / 안 읽음
DELETE /api/notifications/{notification_id}  지우기 (본인 것만)
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from followup.adapters.postgres import notifications
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api", tags=["notifications"])


class ReadState(BaseModel):
    read: bool = True


@router.get("/notifications")
def list_mine(unread_only: bool = False, me: str = Depends(actor)):
    return notifications.list_notifications(actor_id=me, unread_only=unread_only)


@router.patch("/notifications/{notification_id}")
def set_read(notification_id: str, body: ReadState, me: str = Depends(actor)):
    return notifications.set_read(actor_id=me, notification_id=notification_id, read=body.read)


@router.delete("/notifications/{notification_id}", status_code=204)
def delete(notification_id: str, me: str = Depends(actor)):
    notifications.delete_notification(actor_id=me, notification_id=notification_id)