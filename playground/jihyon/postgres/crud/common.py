"""PostgreSQL 공통 예외, 입력값 검사, 접근 권한 검사.
현재 팀 단위 공유는 Neo4j의 팀 소속 정보가 필요해서 여기서는 처리하지 않음"""

from __future__ import annotations

from db import transaction


class NotFoundError(ValueError):
    pass


class PermissionDenied(PermissionError):
    pass


class ValidationError(ValueError):
    pass


def required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label}은(는) 필수입니다.")
    return value.strip()


def get_one(cur, sql: str, params: tuple = ()) -> dict:
    cur.execute(sql, params)
    row = cur.fetchone()

    if row is None:
        raise NotFoundError("요청한 데이터를 찾을 수 없습니다.")

    return row


def account(cur, actor_id: str) -> dict:
    return get_one(
        cur,
        """
        SELECT user_id, role
        FROM accounts
        WHERE user_id = %s
        """,
        (required(actor_id, "사용자 ID"),),
    )


def is_admin(cur, actor_id: str) -> bool:
    return account(cur, actor_id)["role"] == "ADMIN"


def require_user(cur, actor_id: str) -> None:
    """관리자 계정의 일반 사용자 기능 접근 차단."""
    if is_admin(cur, actor_id):
        raise PermissionDenied(
            "관리자는 회의 및 폴더 기능에 접근할 수 없습니다."
        )


def folder(cur, folder_id: str) -> dict:
    return get_one(
        cur,
        """
        SELECT *
        FROM folders
        WHERE id = %s
          AND deleted_at IS NULL
        """,
        (folder_id,),
    )


def can_access_folder(
    cur,
    folder_id: str,
    actor_id: str,
    *,
    write: bool = False,
) -> dict:
    """폴더 주관자 또는 구성원의 접근 권한 검사."""

    require_user(cur, actor_id)
    f = folder(cur, folder_id)

    if f["host_user_id"] == actor_id:
        return f

    if not write:
        cur.execute(
            """
            SELECT 1
            FROM folder_members
            WHERE folder_id = %s
              AND user_id = %s
            """,
            (folder_id, actor_id),
        )

        if cur.fetchone():
            return f

    raise PermissionDenied("폴더 접근 권한이 없습니다.")


def meeting(cur, meeting_id: str) -> dict:
    return get_one(
        cur,
        """
        SELECT *
        FROM meetings
        WHERE id = %s
          AND deleted_at IS NULL
        """,
        (meeting_id,),
    )


def can_access_meeting(
    cur,
    meeting_id: str,
    actor_id: str,
    *,
    write: bool = False,
) -> dict:
    """회의 주관자만 수정할 수 있고, 읽기는 권한에 따라 허용."""

    require_user(cur, actor_id)
    m = meeting(cur, meeting_id)

    if m["host_user_id"] == actor_id:
        return m

    if write:
        raise PermissionDenied("회의 수정 권한이 없습니다.")

    if m["folder_id"]:
        try:
            can_access_folder(
                cur, m["folder_id"], actor_id
            )
            return m
        except (PermissionDenied, NotFoundError):
            pass

    cur.execute(
        """
        SELECT 1
        FROM meeting_shares
        WHERE meeting_id = %s
          AND recipient_user_id = %s
          AND revoked_at IS NULL
        """,
        (meeting_id, actor_id),
    )

    if cur.fetchone():
        return m

    raise PermissionDenied("회의 접근 권한이 없습니다.")


def can_view_raw(
    cur,
    meeting_id: str,
    actor_id: str,
) -> bool:
    """회의 원문 열람 권한 검사."""

    m = can_access_meeting(cur, meeting_id, actor_id)

    if m["host_user_id"] == actor_id:
        return True

    # 직접 공유된 내역이 있으면 해당 공유 범위를 우선 적용
    cur.execute(
        """
        SELECT scope, lock_raw
        FROM meeting_shares
        WHERE meeting_id = %s
          AND recipient_user_id = %s
          AND revoked_at IS NULL
        """,
        (meeting_id, actor_id),
    )

    shares = cur.fetchall()

    if shares:
        return any(
            s["scope"] == "full" and not s["lock_raw"]
            for s in shares
        )

    # 직접 공유가 없다면 폴더 멤버 여부 확인
    if m["folder_id"]:
        cur.execute(
            """
            SELECT 1
            FROM folders f
            JOIN folder_members fm
              ON fm.folder_id = f.id
            WHERE f.id = %s
              AND f.deleted_at IS NULL
              AND fm.user_id = %s
            """,
            (m["folder_id"], actor_id),
        )

        return cur.fetchone() is not None

    return False


def ensure_updated(cur):
    if cur.rowcount != 1:
        raise NotFoundError("변경할 데이터를 찾을 수 없습니다.")


def invalidate(*names: str) -> None:
    """기존 Repository 조회 캐시 초기화."""
    import sys

    repo = sys.modules.get("repository_pg")

    if repo is not None:
        for name in names:
            fn = getattr(repo, name, None)

            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()
