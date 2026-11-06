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
@lru_cache(maxsize=1)
def _base_meetings() -> dict[str, Meeting]:
    """Neo4j 쪽 회의 내용 + PostgreSQL 쪽 관리 정보 → Meeting.

    표마다 SQL 을 한 번씩만 날리고(회의마다 날리면 N+1 문제로 느림) 파이썬에서 회의별로 나눕니다.
    """
    graph_meetings = graph.graph_meetings()
    ms = query("SELECT * FROM meetings WHERE deleted_at IS NULL ORDER BY position, id")
    lines = _group(query("SELECT * FROM transcript_lines ORDER BY meeting_id, position"), "meeting_id")
    kws = _group(query("SELECT * FROM meeting_keywords ORDER BY meeting_id, position"), "meeting_id")
    # 나(CURRENT_USER_ID)에게 온 공유만. 보낸 사람 이름 · 팀은 Neo4j(Person)에 있어서 아래에서 붙임
    shr = {r["meeting_id"]: r for r in query("""
        SELECT meeting_id, sender_id, scope, lock_raw, shared_on
        FROM meeting_shares
        WHERE recipient_user_id = %s AND revoked_at IS NULL
    """, (CURRENT_USER_ID,))}
    people = graph.people()

    out: dict[str, Meeting] = {}
    for m in ms:
        mid = m["id"]
        g = graph_meetings.get(mid)
        if g is None:
            # PostgreSQL 에는 있는데 Neo4j 에 아직 적재 안 된 회의 (예: 분석 중) → 화면에 내지 않음
            continue
        sh = shr.get(mid)
        sender = people.get(sh["sender_id"]) if sh else None
        out[mid] = replace(
            g,  # 제목 · 날짜 · 참석자 · 안건 · 논의 · 결정 · 쟁점 · 할 일은 Neo4j 그대로
            topic=m["folder_id"],
            host=m["host_user_id"],
            time=m["time_label"],
            deep=m["is_deep"],
            file=m["file_name"],
            initial_status=m["status"],
            paragraphs=tuple(Paragraph(p["line_id"], p["speaker"], p["text"]) for p in lines[mid]),
            summary=Evidence(m["summary_text"], tuple(m["summary_src"])),
            keywords=tuple(k["keyword"] for k in kws[mid]),
            shared=ShareInfo(f"{sender.name} · {sender.team}" if sender else sh["sender_id"], sh["scope"],
                             _md(sh["shared_on"]), sh["lock_raw"]) if sh else None,
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
    fs = query("SELECT * FROM folders WHERE deleted_at IS NULL ORDER BY sort_order, created_at")
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
