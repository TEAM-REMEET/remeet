"""Folders: create/read/update/soft-delete; manage membership and briefs."""
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


def create_folder(*, actor_id: str, name: str, type_id: str,
                  folder_id: str | None = None, members: list[str] | None = None) -> dict:
    fid = folder_id or f"f-{uuid4().hex[:16]}"
    name = required(name, "폴더 이름")
    with transaction() as cur:
        from .common import account
        account(cur, actor_id)
        require_user(cur, actor_id)
        cur.execute("""INSERT INTO folders (id,name,type_id,host_user_id)
                       VALUES (%s,%s,%s,%s) RETURNING *""", (fid,name,type_id,actor_id))
        row = cur.fetchone()
        for idx, uid in enumerate(dict.fromkeys([actor_id, *(members or [])])):
            cur.execute("""INSERT INTO folder_members (folder_id,user_id,position,added_by)
                           VALUES (%s,%s,%s,%s)""", (fid,uid,idx,actor_id))
    invalidate('_base_meetings', 'base_folders')
    return row



def list_folders(*, actor_id: str) -> list[dict]:
    """일반 사용자에게 접근 가능한 폴더만 조회."""

    with transaction() as cur:
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
    with transaction() as cur:
        return can_access_folder(cur,folder_id,actor_id)


def update_folder(*, actor_id: str, folder_id: str, name: str | None = None,
                  type_id: str | None = None) -> dict:
    if name is None and type_id is None:
        raise ValidationError("수정할 필드가 없습니다.")
    with transaction() as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""UPDATE folders SET name=COALESCE(%s,name),type_id=COALESCE(%s,type_id)
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""",
                    (required(name,'폴더 이름') if name is not None else None,type_id,folder_id))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def delete_folder(*, actor_id: str, folder_id: str) -> dict:
    """Soft delete folder; do not cascade-delete existing meeting records."""
    with transaction() as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""UPDATE folders SET deleted_at=now()
                       WHERE id=%s AND deleted_at IS NULL RETURNING *""", (folder_id,))
        row=cur.fetchone()
    invalidate('_base_meetings', 'base_folders')
    return row


def add_member(*, actor_id: str, folder_id: str, user_id: str) -> None:
    with transaction() as cur:
        can_access_folder(cur,folder_id,actor_id,write=True)
        cur.execute("""INSERT INTO folder_members (folder_id,user_id,position,added_by)
                       VALUES (%s,%s,(SELECT COALESCE(MAX(position)+1,0) FROM folder_members WHERE folder_id=%s),%s)
                       ON CONFLICT (folder_id,user_id) DO NOTHING""", (folder_id,required(user_id,'구성원 ID'),folder_id,actor_id))
    invalidate('_base_meetings', 'base_folders')


def remove_member(*, actor_id: str, folder_id: str, user_id: str) -> None:
    with transaction() as cur:
        f=can_access_folder(cur,folder_id,actor_id,write=True)
        if f['host_user_id']==user_id:
            raise ValidationError('주관자는 폴더 멤버에서 제거할 수 없습니다.')
        cur.execute("DELETE FROM folder_members WHERE folder_id=%s AND user_id=%s",(folder_id,user_id))
    invalidate('_base_meetings', 'base_folders')
