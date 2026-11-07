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


def is_editor(m: dict, actor_id: str) -> bool:
    """이 회의(행)를 지금 고칠 · 확정할 수 있는 사람인지 (migrations/010 의 meeting_editor 와 같음).
    draft: 작성자 · 주관자
    생성 뒤: 검토자(reviewer_id), 없으면 주관자 (web start_finalize: 주관자만 확정)"""
    if m["status"] == "draft":
        return actor_id in (m["created_by"], m["host_user_id"])
    return actor_id == (m["reviewer_id"] or m["host_user_id"])


def attended(cur, meeting_id: str, actor_id: str) -> bool:
    """참석자인지 (meeting_attendees)."""
    cur.execute("SELECT 1 FROM meeting_attendees WHERE meeting_id = %s AND user_id = %s", (meeting_id, actor_id))
    return cur.fetchone() is not None


def can_access_meeting(
    cur,
    meeting_id: str,
    actor_id: str,
    *,
    write: bool = False,
) -> dict:
    """수정(write=True)은 is_editor() 한 사람만, 읽기는 주관 · 작성 · 검토 · 참석 · 공유받은 사람.

    미리보기(draft)는 작성자 · 주관자만 보고 고침. 생성(review) 뒤에는 is_editor() 규칙.
    """

    require_user(cur, actor_id)
    m = meeting(cur, meeting_id)

    if m["status"] == "draft":
        if actor_id in (m["created_by"], m["host_user_id"]):
            return m
        raise PermissionDenied("아직 작성 중인 회의록입니다.")

    if write:
        if is_editor(m, actor_id):
            return m
        if actor_id == m["created_by"]:
            raise PermissionDenied("검토를 요청한 회의록은 검토자(주관자)만 수정할 수 있습니다.")
        raise PermissionDenied("회의 수정 권한이 없습니다.")

    if actor_id in (m["host_user_id"], m["created_by"], m["reviewer_id"]):
        return m

    if attended(cur, meeting_id, actor_id):
        return m

    # 프로젝트 멤버라는 것만으로는 안 보임 (web can_see: 주관 · 참석 · 공유받음). 미참석 멤버는 공유받아야 봄
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

    if actor_id in (m["host_user_id"], m["created_by"], m["reviewer_id"]) or attended(cur, meeting_id, actor_id):
        return True  # 작성 · 검토 · 주관하거나 회의에 있었던 사람은 원문을 봄 (화면 can_see 규칙)

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

    return any(
        s["scope"] == "full" and not s["lock_raw"]
        for s in shares
    )


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
