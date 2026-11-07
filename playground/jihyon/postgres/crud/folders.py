from __future__ import annotations
from uuid import uuid4
from db import transaction
from .common import (
    ValidationError,
    PermissionDenied,
    required,
    get_one,
    can_access_folder,
    require_user,
    invalidate,
)


def create_folder(*, actor_id: str, name: str,
                  folder_id: str | None = None, members: list[str] | None = None) -> dict:
    """프로젝트(web Folder) 만들기. 회의 종류(월간 · 주간 · 일반)는 프로젝트가 아니라 회의마다(meetings.directory_id)."""
    fid = folder_id or f"f-{uuid4().hex[:16]}"
    name = required(name, "폴더 이름")
    with transaction(actor_id) as cur:
        from .common import account
        account(cur, actor_id)
        require_user(cur, actor_id)
        cur.execute("""INSERT INTO folders (id,name,host_user_id)
                       VALUES (%s,%s,%s) RETURNING *""", (fid,name,actor_id))
        row = cur.fetchone()
        for idx, uid in enumerate(dict.fromkeys([actor_id, *(members or [])])):
            cur.execute("""INSERT INTO folder_members (folder_id,user_id,position,added_by)
                           VALUES (%s,%s,%s,%s)""", (fid,uid,idx,actor_id))
    invalidate('_base_meetings', 'base_folders')
    return row



def list_folders(*, actor_id: str) -> list[dict]:
    """일반 사용자에게 접근 가능한 폴더만 조회."""

    with transaction(actor_id) as cur:
        require_user(cur, actor_id)

        cur.execute(
            """
            SELECT DISTINCT f.*
            FROM folders f
            LEFT JOIN folder_members fm
              ON fm.folder_id = f.id
             AND fm.user_id = %s
            WHERE f.deleted_at IS NULL
              AND (
                  f.host_user_id = %s
                  OR fm.user_id IS NOT NULL
              )
            ORDER BY f.sort_order, f.created_at
            """,
            (actor_id, actor_id),
        )

        return cur.fetchall()



def get_folder(*, actor_id: str, folder_id: str) -> dict:
    with transaction(actor_id) as cur:
        return can_access_folder(cur,folder_id,actor_id)


def update_folder(*, actor_id: str, folder_id: str, name: str) -> dict:
    """프로젝트 이름 바꾸기 (주관자만)."""
    with transaction(actor_id) as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""UPDATE folders SET name=%s
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""",
                    (required(name,'폴더 이름'),folder_id))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def delete_folder(*, actor_id: str, folder_id: str) -> dict:
    """Soft delete folder; do not cascade-delete existing meeting records."""
    with transaction(actor_id) as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""UPDATE folders SET deleted_at=now()
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""", (folder_id,))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def add_member(*, actor_id: str, folder_id: str, user_id: str) -> None:
    with transaction(actor_id) as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""INSERT INTO folder_members (folder_id,user_id,position,added_by)
                       VALUES (%s,%s,(SELECT COALESCE(MAX(position)+1,0) FROM folder_members WHERE folder_id=%s),%s)
                       ON CONFLICT (folder_id,user_id) DO NOTHING""", (folder_id,required(user_id,'구성원 ID'),folder_id,actor_id))
    invalidate('_base_meetings', 'base_folders')


def remove_member(*, actor_id: str, folder_id: str, user_id: str) -> None:
    with transaction(actor_id) as cur:
        f=can_access_folder(cur,folder_id,actor_id,write=True)
        if f['host_user_id']==user_id:
            raise ValidationError('주관자는 폴더 멤버에서 제거할 수 없습니다.')
        cur.execute("DELETE FROM folder_members WHERE folder_id=%s AND user_id=%s",(folder_id,user_id))
    invalidate('_base_meetings', 'base_folders')


def set_briefs(*, actor_id: str, folder_id: str, items: list[dict]) -> list[dict]:
    """폴더 브리프 통째로 바꾸기 (폴더 주관자만). items = [{label, text}] (화면 Folder.brief 순서대로)."""
    rows = [(required(i.get('label'), '브리프 제목'), required(i.get('text'), '브리프 내용')) for i in items]
    with transaction(actor_id) as cur:
        can_access_folder(cur, folder_id, actor_id, write=True)
        cur.execute('DELETE FROM folder_briefs WHERE folder_id = %s', (folder_id,))
        for pos, (label, text) in enumerate(rows):
            cur.execute('INSERT INTO folder_briefs (folder_id, position, label, text) VALUES (%s, %s, %s, %s)',
                        (folder_id, pos, label, text))
    invalidate('base_folders')
    return [{'label': label, 'text': text} for label, text in rows]


def list_briefs(*, actor_id: str, folder_id: str) -> list[dict]:
    with transaction(actor_id) as cur:
        can_access_folder(cur, folder_id, actor_id)
        cur.execute('SELECT label, text FROM folder_briefs WHERE folder_id = %s ORDER BY position', (folder_id,))
        return cur.fetchall()
