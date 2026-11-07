"""Notifications per user; deletion is physical since schema has no deleted_at."""
from __future__ import annotations
from uuid import uuid4
from followup.adapters.postgres.db import transaction
from .common import (ValidationError, PermissionDenied, required, account,
                     can_access_meeting, can_access_folder, invalidate)

# web Notification.kind + 자동 알림 (migrations/010 의 notifications_kind_check 와 같음)
KINDS={'review','shared','integration','member','delivered','fail','created','budget'}


def create_notification(*,actor_id: str,user_id: str,kind: str,text: str,
                        meeting_id: str | None = None,folder_id: str | None = None) -> dict:
    if kind not in KINDS: raise ValidationError('알림 유형이 잘못되었습니다.')
    # Only admins or a host of the related meeting/folder may send an alert to someone else.
    with transaction() as cur:
        sender=account(cur,actor_id)
        if user_id != actor_id and sender['role'] != 'ADMIN':
            if meeting_id: can_access_meeting(cur,meeting_id,actor_id,write=True)
            elif folder_id: can_access_folder(cur,folder_id,actor_id,write=True)
            else: raise PermissionDenied('다른 사용자에게 알림을 생성할 권한이 없습니다.')
        nid=f'n-{uuid4().hex[:16]}'
        cur.execute("""INSERT INTO notifications (id,user_id,kind,text,meeting_id,folder_id)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (nid,required(user_id,'수신자 ID'),kind,required(text,'알림 내용'),meeting_id,folder_id))
        row=cur.fetchone()
    invalidate('notifications')
    return row


def list_notifications(*,actor_id: str, unread_only: bool=False) -> list[dict]:
    with transaction() as cur:
        account(cur,actor_id)
        cur.execute('''SELECT * FROM notifications WHERE user_id=%s AND (%s=FALSE OR is_read=FALSE)
                       ORDER BY created_at DESC''',(actor_id,unread_only))
        return cur.fetchall()


def set_read(*,actor_id: str,notification_id: str,read: bool=True) -> dict:
    with transaction() as cur:
        account(cur,actor_id)
        cur.execute('''UPDATE notifications SET is_read=%s WHERE id=%s AND user_id=%s RETURNING *''',
                    (read,notification_id,actor_id))
        row=cur.fetchone()
        if row is None: raise PermissionDenied('본인의 알림만 수정할 수 있습니다.')
    invalidate('notifications')
    return row


def delete_notification(*,actor_id: str,notification_id: str) -> None:
    with transaction() as cur:
        account(cur,actor_id)
        cur.execute('DELETE FROM notifications WHERE id=%s AND user_id=%s RETURNING id',(notification_id,actor_id))
        if cur.fetchone() is None: raise PermissionDenied('본인의 알림만 삭제할 수 있습니다.')
    invalidate('notifications')
