"""PostgreSQL 저장소 (Neo4j 에 없는 것만).

web/data/repository.py(목업 JSON)와 함수 이름 · 돌려주는 모델이 똑같습니다.
web_patch.py 가 web 의 함수들을 이 파일의 것으로 바꿔 끼웁니다 (web 코드는 안 고침).

데이터가 어디서 오나
    PostgreSQL (이 파일)       폴더 · 폴더 멤버 · 회의 종류, 회의 관리 정보(폴더 · 주관자 · 상태 · 파일 · 요약),
                               회의 원문, 키워드, 공유, 외부 연동, 알림
    Neo4j (graph_source.py)    사람 · 팀 · 담당자 추천, 회의 제목 · 날짜 · 참석자 · 안건 · 논의 · 결정 · 할 일, 위키
                               → 지금은 목업 JSON. 이 파일은 graph_source 의 함수만 부릅니다.

회의(Meeting) 하나 = Neo4j 쪽 내용 + PostgreSQL 쪽 관리 정보를 meeting id 로 합친 것 (_base_meetings).

캐시: 화면을 한 번 그릴 때 repo.meeting() 이 수십 번 불려서 lru_cache 로 기억합니다.
      데이터를 바꾸는 함수는 끝에 관련 캐시를 비웁니다(cache_clear).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache

import paths  # noqa: F401  (web 폴더를 import 경로에)
import graph_source as graph
from config import CURRENT_USER_ID  # web/config.py
from db import execute, query
from data.models import (  # web/data/models.py
    BriefItem,
    Evidence,
    Folder,
    IntegrationTool,
    Meeting,
    MeetingType,
    Notification,
    Paragraph,
    ShareInfo,
)


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    """[{meeting_id: m1, ...}, ...] → {m1: [...], m2: [...]}"""
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def _md(d) -> str:
    """date(2026, 10, 30) → '10/30'"""
    return f"{d.month}/{d.day}" if d else ""


def _setting(key: str, default=None):
    rows = query("SELECT value FROM app_settings WHERE key = %s", (key,))
    return rows[0]["value"] if rows else default  # JSONB 는 파이썬 list/dict 로 자동 변환됨


# ---------------------------------------------------------------- meetings
# PostgreSQL 원문 발화 노출을 막는 조치(AI 상세 분석 내용까지 요약 공유 범위에 맞춰 제한하려면 별도 필드 검사 필요)
@lru_cache(maxsize=1)
def _base_meetings() -> dict[str, Meeting]:
    """권한이 있는 회의만 조회하고 원문 공유 범위를 적용."""

    actor_id = CURRENT_USER_ID

    # 일반 사용자만 회의 조회 가능
    ms = query(
        """
        SELECT DISTINCT m.*
        FROM meetings m
        JOIN accounts a
          ON a.user_id = %s
         AND a.role = 'USER'
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
        (actor_id, actor_id, actor_id, actor_id),
    )

    # Neo4j 담당 데이터 (현재는 목업)
    graph_meetings = graph.graph_meetings()

    # 사용자에게 직접 공유된 회의
    share_rows = query(
        """
        SELECT meeting_id, sender_id, scope,
               lock_raw, shared_on
        FROM meeting_shares
        WHERE recipient_user_id = %s
          AND revoked_at IS NULL
        ORDER BY id DESC
        """,
        (actor_id,),
    )

    shares = _group(share_rows, "meeting_id")

    # 접근 가능한 폴더 멤버십
    member_rows = query(
        """
        SELECT fm.folder_id
        FROM folder_members fm
        JOIN folders f
          ON f.id = fm.folder_id
        WHERE fm.user_id = %s
          AND f.deleted_at IS NULL
        """,
        (actor_id,),
    )

    member_folder_ids = {
        r["folder_id"] for r in member_rows
    }

    # 원문 열람 가능한 회의만 선별
    raw_ids = set()

    for m in ms:
        mid = m["id"]
        direct_shares = shares.get(mid, [])

        if m["host_user_id"] == actor_id:
            raw_ids.add(mid)

        elif direct_shares:
            if any(
                s["scope"] == "full" and not s["lock_raw"]
                for s in direct_shares
            ):
                raw_ids.add(mid)

        elif m["folder_id"] in member_folder_ids:
            raw_ids.add(mid)

    # 원문 권한이 있는 회의만 DB에서 가져옴
    if raw_ids:
        lines = _group(
            query(
                """
                SELECT meeting_id, line_id,
                       speaker, text
                FROM transcript_lines
                WHERE meeting_id = ANY(%s)
                ORDER BY meeting_id, position
                """,
                (list(raw_ids),),
            ),
            "meeting_id",
        )
    else:
        lines = {}

    # 키워드 조회
    visible_ids = [m["id"] for m in ms]

    if visible_ids:
        kws = _group(
            query(
                """
                SELECT meeting_id, keyword
                FROM meeting_keywords
                WHERE meeting_id = ANY(%s)
                ORDER BY meeting_id, position
                """,
                (visible_ids,),
            ),
            "meeting_id",
        )
    else:
        kws = {}

    people = graph.people()
    out = {}

    for m in ms:
        mid = m["id"]
        g = graph_meetings.get(mid)

        if g is None:
            continue

        direct_shares = shares.get(mid, [])

        # 열람 가능한 전체 공유를 우선 표시
        sh = next(
            (
                s for s in direct_shares
                if s["scope"] == "full"
                and not s["lock_raw"]
            ),
            direct_shares[0] if direct_shares else None,
        )

        sender = (
            people.get(sh["sender_id"])
            if sh else None
        )

        if mid in raw_ids:
            paragraphs = tuple(
                Paragraph(
                    p["line_id"],
                    p["speaker"],
                    p["text"],
                )
                for p in lines.get(mid, [])
            )
        else:
            paragraphs = ()

        out[mid] = replace(
            g,
            topic=m["folder_id"],
            host=m["host_user_id"],
            time=m["time_label"],
            deep=m["is_deep"],
            file=m["file_name"],
            initial_status=m["status"],
            paragraphs=paragraphs,
            summary=Evidence(
                m["summary_text"],
                tuple(m["summary_src"]),
            ),
            keywords=tuple(
                k["keyword"]
                for k in kws.get(mid, [])
            ),
            shared=(
                ShareInfo(
                    (
                        f"{sender.name} · {sender.team}"
                        if sender else sh["sender_id"]
                    ),
                    sh["scope"],
                    _md(sh["shared_on"]),
                    sh["lock_raw"],
                )
                if sh else None
            ),
            branch_of=m["branch_of_id"],
        )

    return out



# ---------------------------------------------------------------- folders
@lru_cache(maxsize=1)
def meeting_types() -> dict[str, MeetingType]:
    rows = query("SELECT * FROM meeting_types ORDER BY sort_order")
    return {r["id"]: MeetingType(r["id"], r["name"], r["meta"], r["hidden_count"]) for r in rows}


@lru_cache(maxsize=1)
def base_folders() -> dict[str, Folder]:
    fs = query(
    """
    SELECT DISTINCT f.*
    FROM folders f
    JOIN accounts a
      ON a.user_id = %s
     AND a.role = 'USER'
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
    (CURRENT_USER_ID, CURRENT_USER_ID, CURRENT_USER_ID),
)
    mem = _group(query("SELECT folder_id, user_id FROM folder_members ORDER BY folder_id, position, added_at"), "folder_id")
    brf = _group(query("SELECT * FROM folder_briefs ORDER BY folder_id, position"), "folder_id")
    # 안건 분리로 생긴 회의(branch_of_id 있음)는 목업과 똑같이 폴더 목록에 넣지 않음 (분리 확정 시 화면에서 추가)
    notes = _group(query("""
        SELECT folder_id, id FROM meetings
        WHERE folder_id IS NOT NULL AND deleted_at IS NULL AND branch_of_id IS NULL
        ORDER BY folder_id, folder_position
    """), "folder_id")
    visible = _base_meetings()  # Neo4j 에 아직 없는 회의는 폴더 목록에서도 뺌
    return {
        f["id"]: Folder(
            id=f["id"],
            name=f["name"],
            type=f["type_id"],
            host=f["host_user_id"],
            members=tuple(r["user_id"] for r in mem[f["id"]]),
            notes=tuple(r["id"] for r in notes[f["id"]] if r["id"] in visible),
            brief=tuple(BriefItem(b["label"], b["text"]) for b in brf[f["id"]]),
        )
        for f in fs
    }


# ---------------------------------------------------------------- integrations
@lru_cache(maxsize=1)
def integrations() -> dict:
    tools = query("SELECT * FROM integration_tools ORDER BY sort_order")
    failed: dict[str, list[str]] = defaultdict(list)
    for r in query("SELECT tool_id, meeting_id FROM failed_exports ORDER BY tool_id, meeting_id"):
        failed[r["tool_id"]].append(r["meeting_id"])
    return {
        "organization": _setting("organization", ""),
        "tools": [IntegrationTool(t["id"], t["mark"], t["name"], t["description"], t["target"], t["is_phase2"])
                  for t in tools],
        "initial_status": {r["tool_id"]: r["status"] for r in query("SELECT tool_id, status FROM integration_status")},
        "failed_exports": dict(failed),
    }


# ---------------------------------------------------------------- notifications
def _ago(ts: datetime) -> str:
    """알림 시각 → '10분 전' · '1시간 전' · '어제' · '10/30'"""
    sec = (datetime.now(timezone.utc) - ts).total_seconds()
    if sec < 60:
        return "방금"
    if sec < 3600:
        return f"{int(sec // 60)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    if sec < 2 * 86400:
        return "어제"
    return _md(ts.date())


@lru_cache(maxsize=1)
def notifications() -> list[Notification]:
    rows = query("""
        SELECT id, kind, text, created_at, meeting_id, folder_id, is_read
        FROM notifications WHERE user_id = %s
        ORDER BY created_at DESC
    """, (CURRENT_USER_ID,))
    return [Notification(r["id"], r["kind"], r["text"], _ago(r["created_at"]), r["meeting_id"], not r["is_read"],
                         r["folder_id"]) for r in rows]


# =====================================================================
# 쓰기 (데이터 바꾸기)
# =====================================================================
def mark_notification_read(nid: str) -> None:
    execute("UPDATE notifications SET is_read = TRUE WHERE id = %s AND user_id = %s", (nid, CURRENT_USER_ID))
    notifications.cache_clear()


# 할 일 완료는 Neo4j ActionItem.status 라서 graph_source 로 넘김
todo_done_map = graph.todo_done_map
set_todo_done = graph.set_todo_done


def clear_cache() -> None:
    """DB 를 직접 고쳤을 때(psql 등) 앱에 바로 반영하고 싶으면 호출."""
    for fn in (_base_meetings, meeting_types, base_folders, integrations, notifications):
        fn.cache_clear()
