"""관리자 새 회의 만들 수 없음"""
from __future__ import annotations
from db import transaction
from .common import (
    ValidationError,
    PermissionDenied,
    required,
    get_one,
    account,
    require_user,
    can_access_folder,
    can_access_meeting,
    can_view_raw,
    invalidate,
)

STATUSES = frozenset({'review','confirmed','sending','done','fail'})


def create_meeting(*, actor_id: str, meeting_id: str, folder_id: str | None = None,
                   time_label: str = '', file_name: str = '', status: str = 'review') -> dict:
    if status not in STATUSES: raise ValidationError('지원하지 않는 회의 상태입니다.')
    with transaction() as cur:
        from .common import account
        account(cur,actor_id)
        require_user(cur, actor_id)
        if folder_id: can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""INSERT INTO meetings (id,folder_id,host_user_id,time_label,file_name,status,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (required(meeting_id,'회의 ID'),folder_id,actor_id,time_label,file_name,status,actor_id))
        row=cur.fetchone()
    invalidate('_base_meetings','base_folders')
    return row


def get_meeting(*, actor_id: str, meeting_id: str) -> dict:
    with transaction() as cur:
        return can_access_meeting(cur,meeting_id,actor_id)


def list_meetings(*, actor_id: str) -> list[dict]:
    """접근 권한이 있는 회의만 조회."""

    with transaction() as cur:
        require_user(cur, actor_id)

        cur.execute(
            """
            SELECT DISTINCT m.*
            FROM meetings m
            LEFT JOIN folders f
              ON f.id = m.folder_id
             AND f.deleted_at IS NULL
            WHERE m.deleted_at IS NULL
              AND (
                  m.host_user_id = %s

                  OR EXISTS (
                      SELECT 1
                      FROM folder_members fm
                      WHERE fm.folder_id = f.id
                        AND fm.user_id = %s
                  )

                  OR EXISTS (
                      SELECT 1
                      FROM meeting_shares s
                      WHERE s.meeting_id = m.id
                        AND s.recipient_user_id = %s
                        AND s.revoked_at IS NULL
                  )
              )
            ORDER BY m.position, m.id
            """,
            (actor_id, actor_id, actor_id),
        )

        return cur.fetchall()



def update_meeting(*,actor_id: str,meeting_id: str,folder_id: str | None = None,
                   status: str | None = None,time_label: str | None = None,
                   file_name: str | None = None,summary_text: str | None = None) -> dict:
    if all(v is None for v in (folder_id,status,time_label,file_name,summary_text)):
        raise ValidationError('수정할 필드가 없습니다.')
    if status is not None and status not in STATUSES: raise ValidationError('지원하지 않는 회의 상태입니다.')
    with transaction() as cur:
        can_access_meeting(cur,meeting_id,actor_id,write=True)
        if folder_id is not None: can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""UPDATE meetings SET folder_id=COALESCE(%s,folder_id),status=COALESCE(%s,status),
                       time_label=COALESCE(%s,time_label),file_name=COALESCE(%s,file_name),
                       summary_text=COALESCE(%s,summary_text)
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""",
                    (folder_id,status,time_label,file_name,summary_text,meeting_id))
        row=cur.fetchone()
    invalidate('_base_meetings','base_folders')
    return row


def delete_meeting(*,actor_id: str,meeting_id: str) -> dict:
    with transaction() as cur:
        can_access_meeting(cur,meeting_id,actor_id,write=True)
        cur.execute("""UPDATE meetings SET deleted_at=now() WHERE id=%s AND deleted_at IS NULL
                       RETURNING *""", (meeting_id,))
        row=cur.fetchone()
    invalidate('_base_meetings','base_folders')
    return row


def replace_transcript(*, actor_id: str, meeting_id: str, lines: list[dict]) -> int:
    ids=[required(p.get('line_id'),'발화 ID') for p in lines]
    if len(set(ids)) != len(ids): raise ValidationError('발화 ID가 중복됐습니다.')
    for p in lines: required(p.get('text'),'발화 내용')
    with transaction() as cur:
        can_access_meeting(cur,meeting_id,actor_id,write=True)
        cur.execute('DELETE FROM transcript_lines WHERE meeting_id=%s',(meeting_id,))
        for i,p in enumerate(lines):
            cur.execute("""INSERT INTO transcript_lines
                           (meeting_id,line_id,position,speaker,speaker_role,text,source_line)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (meeting_id,ids[i],i,p.get('speaker',''),p.get('speaker_role',''),p['text'],p.get('source_line')))
    invalidate('_base_meetings')
    return len(lines)



def get_transcript(
    *,
    actor_id: str,
    meeting_id: str,
) -> list[dict]:
    """권한을 확인한 뒤 회의 원문 반환."""

    with transaction() as cur:
        if not can_view_raw(
            cur,
            meeting_id,
            actor_id,
        ):
            raise PermissionDenied(
                "회의 원문 열람 권한이 없습니다."
            )

        cur.execute(
            """
            SELECT *
            FROM transcript_lines
            WHERE meeting_id = %s
            ORDER BY position
            """,
            (meeting_id,),
        )

        return cur.fetchall()

