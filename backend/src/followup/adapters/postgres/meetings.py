"""관리자 새 회의 만들 수 없음"""
from __future__ import annotations
from datetime import datetime
from followup.adapters.postgres.db import transaction
from .common import (
    ValidationError,
    PermissionDenied,
    required,
    get_one,
    account,
    require_user,
    can_access_folder,
    can_access_meeting,
    can_manage_meeting,
    can_view_raw,
    invalidate,
)

STATUSES = frozenset({'review','confirmed','sending','done','fail'})


def create_meeting(*, actor_id: str, meeting_id: str, project_id: str | None = None,
                   time_label: str = '', file_name: str = '', status: str = 'review',
                   title: str = '', held_at: datetime | None = None, directory_id: str = 'normal') -> dict:
    """held_at = 회의 날짜 · 시각. 목록 최신순 정렬 기준 (없으면 created_at 으로 정렬).
    directory_id = 회의 종류 (monthly | weekly | normal · web Meeting.directory_id). project_id = 프로젝트 (web Meeting.project_id)."""
    if status not in STATUSES: raise ValidationError('지원하지 않는 회의 상태입니다.')
    with transaction(actor_id) as cur:
        from .common import account
        account(cur,actor_id)
        require_user(cur, actor_id)
        if project_id: can_access_folder(cur,project_id,actor_id)   # 프로젝트 멤버면 그 프로젝트에 회의를 만들 수 있음
        _check_type(cur,directory_id)
        cur.execute("""INSERT INTO meetings (id,project_id,directory_id,host_user_id,title,held_at,time_label,file_name,status,created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (required(meeting_id,'회의 ID'),project_id,directory_id,actor_id,title.strip(),held_at,
                     time_label,file_name,status,actor_id))
        row=cur.fetchone()
        # 주관자는 늘 참석자 (화면 규칙). 다른 참석자는 detail.set_attendees
        cur.execute("INSERT INTO meeting_attendees (meeting_id,user_id,position) VALUES (%s,%s,0)",(row['id'],actor_id))
    invalidate('_base_meetings','base_folders')
    return row


def _check_type(cur, directory_id: str) -> None:
    cur.execute('SELECT 1 FROM meeting_types WHERE id=%s',(directory_id,))
    if cur.fetchone() is None: raise ValidationError(f'없는 회의 종류입니다: {directory_id}')


def get_meeting(*, actor_id: str, meeting_id: str) -> dict:
    with transaction(actor_id) as cur:
        return can_access_meeting(cur,meeting_id,actor_id)


def list_meetings(*, actor_id: str) -> list[dict]:
    """접근 권한이 있는 회의만 조회 (주관 · 작성 · 검토 · 참석 · 공유받음 — web can_see). 목록 화면은 minutes 를 쓰세요."""

    with transaction(actor_id) as cur:
        require_user(cur, actor_id)

        cur.execute(
            """
            SELECT m.*
            FROM meetings m
            WHERE m.deleted_at IS NULL
              AND (m.status <> 'draft' OR %(a)s IN (m.created_by, m.host_user_id))
              AND (
                  %(a)s IN (m.host_user_id, m.created_by, m.reviewer_id)
                  OR EXISTS (SELECT 1 FROM meeting_attendees ma WHERE ma.meeting_id = m.id AND ma.user_id = %(a)s)
                  OR EXISTS (SELECT 1 FROM meeting_shares s WHERE s.meeting_id = m.id
                             AND s.recipient_user_id = %(a)s AND s.revoked_at IS NULL)
              )
            ORDER BY m.position, m.id
            """,
            {"a": actor_id},
        )

        return cur.fetchall()


def update_meeting(*,actor_id: str,meeting_id: str,project_id: str | None = None,
                   status: str | None = None,time_label: str | None = None,
                   file_name: str | None = None,summary_text: str | None = None,
                   title: str | None = None,held_at: datetime | None = None,
                   directory_id: str | None = None, clear_project: bool = False) -> dict:
    """clear_project=True 면 프로젝트에서 빼기 (project_id=None 은 '안 바꿈' 이라서 따로 둠)."""
    if clear_project and project_id is not None:
        raise ValidationError('project_id 와 clear_project 를 같이 줄 수 없습니다.')
    if all(v is None for v in (project_id,status,time_label,file_name,summary_text,title,held_at,directory_id)) and not clear_project:
        raise ValidationError('수정할 필드가 없습니다.')
    if status is not None and status not in STATUSES: raise ValidationError('지원하지 않는 회의 상태입니다.')
    with transaction(actor_id) as cur:
        m=can_access_meeting(cur,meeting_id,actor_id,write=True)
        # 미리보기 → 생성은 알림이 같이 가야 해서 전용 함수로만 (compose.submit_meeting)
        if status is not None and status != m['status'] and 'draft' in (m['status'], status):
            raise ValidationError('미리보기 회의록은 compose.submit_meeting 으로 생성해 주세요.')
        if project_id is not None: can_access_folder(cur,project_id,actor_id)
        if directory_id is not None: _check_type(cur,directory_id)
        cur.execute("""UPDATE meetings SET project_id=CASE WHEN %s THEN NULL ELSE COALESCE(%s,project_id) END,
                       directory_id=COALESCE(%s,directory_id),status=COALESCE(%s,status),
                       time_label=COALESCE(%s,time_label),file_name=COALESCE(%s,file_name),
                       summary_text=COALESCE(%s,summary_text),title=COALESCE(%s,title),
                       held_at=COALESCE(%s,held_at),
                       confirmed_at=CASE WHEN %s='confirmed' AND status<>'confirmed' THEN now() ELSE confirmed_at END,
                       confirmed_by=CASE WHEN %s='confirmed' AND status<>'confirmed' THEN %s ELSE confirmed_by END
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""",
                    (clear_project,project_id,directory_id,status,time_label,file_name,summary_text,
                     title.strip() if title is not None else None,held_at,status,status,actor_id,meeting_id))
        row=cur.fetchone()
    invalidate('_base_meetings','base_folders')
    return row


def delete_meeting(*,actor_id: str,meeting_id: str) -> dict:
    """회의 삭제 (주관자 · 작성자 · 검토자는 못 함). 신입 주관자는 제출 뒤 고칠 수 없어도 삭제는 할 수 있어서
    RLS 의 수정 규칙 대신 migrations/018 의 remeet_internal.delete_meeting() 으로 지워요."""
    with transaction(actor_id) as cur:
        can_manage_meeting(cur,meeting_id,actor_id)
        cur.execute("SELECT * FROM remeet_internal.delete_meeting(%s)", (meeting_id,))
        row=cur.fetchone()
    invalidate('_base_meetings','base_folders')
    return row


def replace_transcript(*, actor_id: str, meeting_id: str, lines: list[dict]) -> int:
    ids=[required(p.get('line_id'),'발화 ID') for p in lines]
    if len(set(ids)) != len(ids): raise ValidationError('발화 ID가 중복됐습니다.')
    for p in lines: required(p.get('text'),'발화 내용')
    with transaction(actor_id) as cur:
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

    with transaction(actor_id) as cur:
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



def list_status_history(*, actor_id: str, meeting_id: str) -> list[dict]:
    """회의 상태 변경 이력 (오래된 것부터). 기록은 DB 트리거가 자동으로 남김 (migrations/007).
    changed_by 가 NULL 이면 사람 없이 바뀐 것 (시스템 · 스크립트)."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id)
        cur.execute("""SELECT from_status, to_status, changed_by, changed_at FROM meeting_status_history
                       WHERE meeting_id = %s ORDER BY changed_at, id""", (meeting_id,))
        return cur.fetchall()

