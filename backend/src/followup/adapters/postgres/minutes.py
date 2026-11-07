"""회의 원본 · 회의록 트리 / 목록 (web components/sidebar.py · state/archive.py 와 같은 구조).  ※ 회의 상세는 detail.py

    원본   meeting_sources      올린 회의록 파일 그대로 (다시 올리면 version + 1)

    사이드바 트리 (토글로 전환 · 같은 회의를 두 가지로 묶어 보여 줌)
      정기 회의 (tree_mode=time)   list_regular_tree()
        월간 회의 ─ 2026년 ─ 9월 (2026.09.26)                 node: t:monthly · y:monthly:2026 · m:<회의 id>
        주간 회의 ─ 2026년 ─ 10월 ─ 4주차 (2026.10.27)        node: t:weekly · y:weekly:2026 · ym:weekly:2026:10 · m:<id>
        일반 회의 ─ 2026.10.23                                node: t:normal · m:<id>
      프로젝트 (tree_mode=project)  list_project_tree()
        프로젝트 ─ 11월 릴리즈 준비 ─ 2026.10.29                node: proj · pf:<프로젝트 id> · m:<id>
    「회의록」 페이지 카드 → 그 종류의 회의 목록               list_regular_meetings(directory_id)
    프로젝트 화면                                             list_project_folders() · list_project_meetings(project_id)

    트리 · 목록에 나오는 회의 = 내 프로젝트(멤버 · 주관)의 회의 중 내가 볼 수 있는 것 (web folder_note_ids)
      볼 수 있음 = 주관 · 작성 · 검토 · 참석 · 공유받음 (web can_see — 프로젝트 멤버라는 것만으로는 X)
      미리보기(draft)는 트리 · 목록에 안 나옴 (compose.list_my_drafts). 공유만 받은 바깥 회의는 shares.list_received.
    주차 = 그 주 월요일이 속한 달 기준 몇째 주 (10/27 월 → 10월 4주차 · 11/1 일 → 10/26 주 → 10월 4주차)
    목록은 모두 최신 날짜순.

권한
    원본 보기 = 원문 보기 권한 (can_view_raw: 요약만 공유받은 사람은 못 봄) · 원본 쓰기 = 고칠 수 있는 사람(is_editor)

사용 예
    from followup.adapters.postgres import minutes
    minutes.add_source(actor_id='u1', meeting_id='m1', file_name='회의.txt', content_text='A: 시작할게요 ...')
    minutes.list_regular_tree(actor_id='u1')
    minutes.list_regular_meetings(actor_id='u1', directory_id='weekly')
    minutes.list_project_tree(actor_id='u1')
    minutes.list_project_meetings(actor_id='u1', project_id='rel')
"""
from __future__ import annotations

import hashlib

from followup.adapters.postgres.db import transaction
from .common import (
    NotFoundError,
    PermissionDenied,
    ValidationError,
    can_access_folder,
    can_access_meeting,
    can_view_raw,
    invalidate,
    require_user,
    required,
)

ORIGINS = frozenset({"ai", "edited"})
MAX_PAGE = 200

# 목록에 쓰는 정렬 기준 (schema.sql 의 meetings_*latest_idx 인덱스와 같은 식)
_LATEST = "COALESCE(m.held_at, m.created_at)"
# 회의 날짜 (한국 시간)
_DAY = f"(({_LATEST} AT TIME ZONE 'Asia/Seoul')::date)"

# 목록 한 줄에 같이 보여 줄 것: 프로젝트 이름, 최신 요약 version · 첫 부분, 원본 있는지
_LIST_COLUMNS = f"""
    m.id, m.title, m.held_at, {_LATEST} AS sort_at, to_char({_DAY}, 'YYYY.MM.DD') AS date_label,
    m.time_label, m.status, m.directory_id, mt.name AS directory_name,
    m.project_id, f.name AS project_name, m.host_user_id, m.file_name,
    EXISTS (SELECT 1 FROM meeting_shares x WHERE x.meeting_id = m.id AND x.recipient_user_id = %(actor)s
            AND x.revoked_at IS NULL) AS shared_to_me,
    s.version AS summary_version, left(s.overview, 200) AS summary_preview,
    EXISTS (SELECT 1 FROM meeting_sources src WHERE src.meeting_id = m.id) AS has_source
"""
_LATEST_SUMMARY_JOIN = """
    JOIN meeting_types mt ON mt.id = m.directory_id
    LEFT JOIN LATERAL (
        SELECT version, overview FROM meeting_summaries ms
        WHERE ms.meeting_id = m.id ORDER BY version DESC LIMIT 1
    ) s ON TRUE
"""


def _page(limit: int, offset: int) -> tuple[int, int]:
    if not (1 <= limit <= MAX_PAGE) or offset < 0:
        raise ValidationError(f"limit 은 1~{MAX_PAGE}, offset 은 0 이상이어야 합니다.")
    return limit, offset


def _lock_meeting(cur, meeting_id: str) -> None:
    """version 번호를 매길 때 두 요청이 같은 번호를 받지 않도록 회의 행을 잠금."""
    cur.execute("SELECT 1 FROM meetings WHERE id = %s FOR UPDATE", (meeting_id,))


def _next_version(cur, table: str, meeting_id: str) -> int:
    # table 은 이 파일 안의 고정 값만 들어옴 (사용자 입력 아님)
    cur.execute(f"SELECT COALESCE(MAX(version), 0) + 1 AS v FROM {table} WHERE meeting_id = %s", (meeting_id,))
    return cur.fetchone()["v"]


# =====================================================================
# 회의 원본
# =====================================================================
def add_source(*, actor_id: str, meeting_id: str, file_name: str,
               content_text: str | None = None, storage_uri: str | None = None,
               mime_type: str = "text/plain", size_bytes: int | None = None) -> dict:
    """회의 원본 올리기. 텍스트는 content_text, 음성 · 큰 파일은 저장소에 올린 뒤 storage_uri."""
    if not content_text and not storage_uri:
        raise ValidationError("원본 내용(content_text) 또는 파일 경로(storage_uri)가 필요합니다.")
    digest = None
    if content_text:
        raw = content_text.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        size_bytes = len(raw) if size_bytes is None else size_bytes
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        _lock_meeting(cur, meeting_id)
        version = _next_version(cur, "meeting_sources", meeting_id)
        cur.execute(
            """INSERT INTO meeting_sources
                   (meeting_id, version, file_name, mime_type, content_text, storage_uri,
                    size_bytes, sha256, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, meeting_id, version, file_name, mime_type, storage_uri,
                         size_bytes, sha256, uploaded_by, uploaded_at""",
            (meeting_id, version, required(file_name, "파일 이름"), mime_type, content_text,
             storage_uri, size_bytes, digest, actor_id),
        )
        row = cur.fetchone()
        # 목록 화면의 파일 이름도 최신 원본으로
        cur.execute("UPDATE meetings SET file_name = %s WHERE id = %s", (row["file_name"], meeting_id))
    invalidate("_base_meetings")
    return row


def get_source(*, actor_id: str, meeting_id: str, version: int | None = None) -> dict:
    """원본 1벌 (내용 포함). version 을 안 주면 최신."""
    with transaction(actor_id) as cur:
        if not can_view_raw(cur, meeting_id, actor_id):
            raise PermissionDenied("회의 원본 열람 권한이 없습니다.")
        cur.execute(
            """SELECT * FROM meeting_sources WHERE meeting_id = %s
                 AND (%s::int IS NULL OR version = %s::int)
               ORDER BY version DESC LIMIT 1""",
            (meeting_id, version, version),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFoundError("회의 원본이 없습니다.")
    return row


def list_sources(*, actor_id: str, meeting_id: str) -> list[dict]:
    """원본 버전 목록 (내용은 빼고 정보만). 최신 버전이 먼저."""
    with transaction(actor_id) as cur:
        if not can_view_raw(cur, meeting_id, actor_id):
            raise PermissionDenied("회의 원본 열람 권한이 없습니다.")
        cur.execute(
            """SELECT id, version, file_name, mime_type, storage_uri, size_bytes, sha256,
                      uploaded_by, uploaded_at
               FROM meeting_sources WHERE meeting_id = %s ORDER BY version DESC""",
            (meeting_id,),
        )
        return cur.fetchall()


# =====================================================================
# 회의록 트리 · 목록
# =====================================================================
# 내가 볼 수 있는 회의 (common.can_access_meeting 과 같은 규칙 · web can_see). m = meetings
_VISIBLE = """
    m.deleted_at IS NULL
    AND (m.status <> 'draft' OR %(actor)s IN (m.created_by, m.host_user_id))   -- 미리보기는 작성자 · 주관자만
    AND (
        %(actor)s IN (m.host_user_id, m.created_by, m.reviewer_id)
        OR EXISTS (SELECT 1 FROM meeting_attendees ma WHERE ma.meeting_id = m.id AND ma.user_id = %(actor)s)
        OR EXISTS (SELECT 1 FROM meeting_shares sh
                   WHERE sh.meeting_id = m.id AND sh.recipient_user_id = %(actor)s AND sh.revoked_at IS NULL)
    )
"""
# 트리 · 목록 범위 = 내 프로젝트(살아 있고 내가 멤버 · 주관)의 회의 중 볼 수 있는 것, 미리보기 제외 (web folder_note_ids)
_IN_MY_PROJECTS = _VISIBLE + """
    AND m.status <> 'draft'
    AND EXISTS (SELECT 1 FROM folders pf WHERE pf.id = m.project_id AND pf.deleted_at IS NULL
                AND (pf.host_user_id = %(actor)s
                     OR EXISTS (SELECT 1 FROM folder_members pm WHERE pm.folder_id = pf.id AND pm.user_id = %(actor)s)))
"""
REGULAR_ORDER = ("monthly", "weekly", "normal")   # web sidebar _TREE_ORDER


def _dotted(d) -> str:
    return f"{d.year}.{d.month:02d}.{d.day:02d}"


def _leaf(r: dict, label: str) -> dict:
    return {"node": f"m:{r['id']}", "label": label, "meeting_id": r["id"], "title": r["title"],
            "project_id": r["project_id"], "date": _dotted(r["day"]), "children": []}


def _folder(node: str, label: str) -> dict:
    return {"node": node, "label": label, "count": 0, "children": []}


def _tree_rows(cur, actor_id: str, extra: str = "", params: dict | None = None) -> list[dict]:
    cur.execute(
        f"""SELECT m.id, m.title, m.directory_id, m.project_id, {_DAY} AS day
            FROM meetings m WHERE {_IN_MY_PROJECTS} {extra}
            ORDER BY {_LATEST} DESC, m.id DESC""",
        {"actor": actor_id, **(params or {})},
    )
    return cur.fetchall()


def list_meeting_types(*, actor_id: str) -> list[dict]:
    """정기 회의 종류 (web MeetingType: id · name · meta · hidden_count) — 화면 순서(월간 · 주간 · 일반).
    hidden_count = 그 종류 회의 중 내가 볼 수 없는 것 (생성된 회의만 · 미리보기 · 삭제 제외)."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            f"""SELECT t.id, t.name, t.meta, t.sort_order,
                       (SELECT count(*) FROM meetings m WHERE m.directory_id = t.id AND m.deleted_at IS NULL
                          AND m.status <> 'draft') AS total,
                       (SELECT count(*) FROM meetings m WHERE m.directory_id = t.id AND {_IN_MY_PROJECTS}) AS visible
                FROM meeting_types t""",
            {"actor": actor_id},
        )
        rows = cur.fetchall()
    rank = {t: i for i, t in enumerate(REGULAR_ORDER)}
    rows.sort(key=lambda r: (rank.get(r["id"], len(rank)), r["sort_order"], r["id"]))
    return [{"id": r["id"], "name": r["name"], "meta": r["meta"], "hidden_count": r["total"] - r["visible"]}
            for r in rows]


def list_regular_tree(*, actor_id: str) -> list[dict]:
    """[사이드바 · 정기 회의] web sidebar._timeline 과 같은 트리 (node id · 이름 · 회의 수 · 최근 것이 먼저).

        월간 회의 ─ 2026년 ─ 9월 (2026.09.26)
        주간 회의 ─ 2026년 ─ 10월 ─ 4주차 (2026.10.27)     연도 · 월 · 주차는 그 주 월요일 기준
        일반 회의 ─ 2026.10.23
    폴더 = {node, label, count, children} · 회의 = {node: m:<id>, label, meeting_id, title, project_id, date, children: []}
    회의가 없는 종류도 나옴 (count 0).
    """
    from datetime import timedelta

    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT id, name FROM meeting_types ORDER BY sort_order, id")
        names = {r["id"]: r["name"] for r in cur.fetchall()}
        rows = _tree_rows(cur, actor_id)

    order = [t for t in REGULAR_ORDER if t in names] + [t for t in names if t not in REGULAR_ORDER]
    tree = {t: _folder(f"t:{t}", names[t]) for t in order}
    index: dict[str, dict] = {}

    def child(parent: dict, node: str, label: str) -> dict:
        if node not in index:
            index[node] = _folder(node, label)
            parent["children"].append(index[node])            # 행이 최신순 → 최근 폴더가 먼저
        return index[node]

    for r in rows:
        t, d = r["directory_id"], r["day"]
        top = tree[t]
        top["count"] += 1
        if t == "normal":
            top["children"].append(_leaf(r, _dotted(d)))
            continue
        if t == "weekly":
            monday = d - timedelta(days=d.weekday())
            y = child(top, f"y:{t}:{monday.year}", f"{monday.year}년")
            ym = child(y, f"ym:{t}:{monday.year}:{monday.month}", f"{monday.month}월")
            y["count"] += 1
            ym["count"] += 1
            ym["children"].append(_leaf(r, f"{(monday.day - 1) // 7 + 1}주차 ({_dotted(d)})"))
            continue
        y = child(top, f"y:{t}:{d.year}", f"{d.year}년")     # 월간 (그 밖의 종류도 같은 모양)
        y["count"] += 1
        y["children"].append(_leaf(r, f"{d.month}월 ({_dotted(d)})"))
    return list(tree.values())


def list_project_tree(*, actor_id: str) -> dict:
    """[사이드바 · 프로젝트] web sidebar._projects 와 같은 트리: 프로젝트 ─ 프로젝트 폴더 ─ 2026.10.29.
    {node: proj, label: 프로젝트, count: 프로젝트 수, children: [{node: pf:<id>, label: 이름, count, children: [회의]}]}"""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            """SELECT f.id, f.name FROM folders f
               WHERE f.deleted_at IS NULL
                 AND (f.host_user_id = %(actor)s
                      OR EXISTS (SELECT 1 FROM folder_members fm WHERE fm.folder_id = f.id AND fm.user_id = %(actor)s))
               ORDER BY f.sort_order, f.created_at""",
            {"actor": actor_id},
        )
        projects = cur.fetchall()
        rows = _tree_rows(cur, actor_id)
    nodes = {p["id"]: _folder(f"pf:{p['id']}", p["name"]) for p in projects}
    for r in rows:
        n = nodes[r["project_id"]]
        n["count"] += 1
        n["children"].append(_leaf(r, _dotted(r["day"])))
    return {"node": "proj", "label": "프로젝트", "count": len(nodes), "children": list(nodes.values())}


def list_regular_meetings(*, actor_id: str, directory_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """[「회의록」 페이지 · 월간/주간/일반 카드] 그 종류의 회의 목록, 최신 날짜순 (web archive.meeting_ids_of_type)."""
    limit, offset = _page(limit, offset)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM meetings m
            LEFT JOIN folders f ON f.id = m.project_id AND f.deleted_at IS NULL
            {_LATEST_SUMMARY_JOIN}
            WHERE m.directory_id = %(dir)s AND {_IN_MY_PROJECTS}
            ORDER BY {_LATEST} DESC, m.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"actor": actor_id, "dir": directory_id, "limit": limit, "offset": offset},
        )
        return cur.fetchall()


def list_all_meetings(*, actor_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """내가 볼 수 있는 회의 전부 (내 프로젝트 + 공유만 받은 회의), 최신 날짜순. 공유만 받은 회의는 shared_to_me."""
    limit, offset = _page(limit, offset)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM meetings m
            LEFT JOIN folders f ON f.id = m.project_id AND f.deleted_at IS NULL
            {_LATEST_SUMMARY_JOIN}
            WHERE {_VISIBLE} AND m.status <> 'draft'
            ORDER BY {_LATEST} DESC, m.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"actor": actor_id, "limit": limit, "offset": offset},
        )
        return cur.fetchall()


def list_project_folders(*, actor_id: str) -> list[dict]:
    """[프로젝트] 내가 멤버 · 주관인 프로젝트 목록 + (내가 볼 수 있는) 회의 수 · 마지막 회의 날짜."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            f"""
            SELECT f.id, f.name, f.host_user_id, f.sort_order, f.created_at,
                   count(m.id) AS meeting_count, max({_LATEST}) AS last_meeting_at
            FROM folders f
            LEFT JOIN meetings m ON m.project_id = f.id AND {_IN_MY_PROJECTS}
            WHERE f.deleted_at IS NULL
              AND (f.host_user_id = %(actor)s
                   OR EXISTS (SELECT 1 FROM folder_members fm WHERE fm.folder_id = f.id AND fm.user_id = %(actor)s))
            GROUP BY f.id
            ORDER BY f.sort_order, f.created_at
            """,
            {"actor": actor_id},
        )
        return cur.fetchall()


def list_project_meetings(*, actor_id: str, project_id: str,
                          limit: int = 50, offset: int = 0) -> list[dict]:
    """[프로젝트 > 프로젝트 폴더] 그 안에서 내가 볼 수 있는 회의록, 최신 날짜순 (web folder_note_ids)."""
    limit, offset = _page(limit, offset)
    with transaction(actor_id) as cur:
        can_access_folder(cur, project_id, actor_id)
        cur.execute(
            f"""
            SELECT {_LIST_COLUMNS}
            FROM meetings m
            JOIN folders f ON f.id = m.project_id
            {_LATEST_SUMMARY_JOIN}
            WHERE m.project_id = %(project)s AND {_IN_MY_PROJECTS}
            ORDER BY {_LATEST} DESC, m.id DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"actor": actor_id, "project": project_id, "limit": limit, "offset": offset},
        )
        return cur.fetchall()
