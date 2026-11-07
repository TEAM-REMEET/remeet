"""회의 공유. 팀에게 보낸 공유(recipient_team_id)의 팀원 확인은 구성원 표(팀원 담당)가 생기면 연결."""
from __future__ import annotations
from db import transaction
from .common import (ValidationError, PermissionDenied, required, can_access_meeting,
                     get_one, is_admin, invalidate, require_user)


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


def list_sent(*, actor_id: str) -> list[dict]:
    """[공유한 회의] 내가 보낸 공유 (최근 순 · 취소한 것 제외). web SentShare(meeting_id, to, scope, date).
    받는 사람은 recipient_user_id 또는 recipient_team_id — 이름은 구성원 표에서."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("""SELECT s.id, s.meeting_id, m.title, s.recipient_user_id, s.recipient_team_id, s.scope,
                              s.lock_raw, s.shared_on
                       FROM meeting_shares s JOIN meetings m ON m.id = s.meeting_id AND m.deleted_at IS NULL
                       WHERE s.sender_id = %s AND s.revoked_at IS NULL
                       ORDER BY s.shared_on DESC, s.id DESC""", (actor_id,))
        return cur.fetchall()


def list_received(*, actor_id: str) -> list[dict]:
    """[공유 받은 회의] 나에게 온 공유 (최근 받은 순 · 회의마다 1줄 · 원문까지 볼 수 있는 공유를 우선).
    web received_meeting_ids · ShareInfo(sender, scope, received, lock_raw)."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("""SELECT DISTINCT ON (s.meeting_id)
                              s.meeting_id, m.title, m.project_id, m.directory_id, m.held_at,
                              s.sender_id, s.scope, s.lock_raw, s.shared_on
                       FROM meeting_shares s JOIN meetings m ON m.id = s.meeting_id
                            AND m.deleted_at IS NULL AND m.status <> 'draft'
                       WHERE s.recipient_user_id = %s AND s.revoked_at IS NULL
                       ORDER BY s.meeting_id, (s.scope = 'full' AND NOT s.lock_raw) DESC, s.id DESC""",
                    (actor_id,))
        rows = cur.fetchall()
    return sorted(rows, key=lambda r: (r["shared_on"], r["meeting_id"]), reverse=True)
