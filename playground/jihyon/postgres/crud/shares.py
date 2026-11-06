"""Meeting shares. Neo4j team membership must be checked upstream for team recipients."""
from __future__ import annotations
from db import transaction
from .common import (ValidationError, PermissionDenied, required, can_access_meeting,
                     get_one, is_admin, invalidate)


def create_share(*,actor_id: str,meeting_id: str,recipient_user_id: str | None = None,
                 recipient_team_id: str | None = None,scope: str = 'summary',lock_raw: bool = False) -> dict:
    if bool(recipient_user_id) == bool(recipient_team_id):
        raise ValidationError('사용자 또는 팀 중 하나만 지정해야 합니다.')
    if scope not in {'summary','full'}: raise ValidationError('공유 범위가 잘못되었습니다.')
    with transaction() as cur:
        can_access_meeting(cur,meeting_id,actor_id,write=True)
        cur.execute("""INSERT INTO meeting_shares
                       (meeting_id,sender_id,recipient_user_id,recipient_team_id,scope,lock_raw)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (meeting_id,actor_id,recipient_user_id,recipient_team_id,scope,lock_raw))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def list_shares(*,actor_id: str,meeting_id: str) -> list[dict]:
    with transaction() as cur:
        can_access_meeting(cur,meeting_id,actor_id,write=True)
        cur.execute('SELECT * FROM meeting_shares WHERE meeting_id=%s ORDER BY id',(meeting_id,))
        return cur.fetchall()


def update_share(*,actor_id: str,share_id: int,scope: str,lock_raw: bool) -> dict:
    if scope not in {'summary','full'}: raise ValidationError('공유 범위가 잘못되었습니다.')
    with transaction() as cur:
        sh=get_one(cur,'SELECT * FROM meeting_shares WHERE id=%s AND revoked_at IS NULL',(share_id,))
        can_access_meeting(cur,sh['meeting_id'],actor_id,write=True)
        cur.execute("""UPDATE meeting_shares SET scope=%s,lock_raw=%s
                       WHERE id=%s AND revoked_at IS NULL RETURNING *""", (scope,lock_raw,share_id))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def revoke_share(*,actor_id: str,share_id: int) -> dict:
    with transaction() as cur:
        sh=get_one(cur,'SELECT * FROM meeting_shares WHERE id=%s AND revoked_at IS NULL',(share_id,))
        can_access_meeting(cur,sh['meeting_id'],actor_id,write=True)
        cur.execute('UPDATE meeting_shares SET revoked_at=now() WHERE id=%s RETURNING *',(share_id,))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row
