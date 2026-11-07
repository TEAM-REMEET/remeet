"""회의 검색 (web views/search.py 의 탭: 회의명 · 폴더명 · 구성원 · 회의 내용).

검색 범위 = 내 프로젝트의 회의 중 내가 볼 수 있는 것 (web visible_note_ids · crud/minutes 의 트리와 같은 범위).
글자 일부 포함 검색(대소문자 무시) · migrations/009 의 pg_trgm 인덱스로 빠르게.

    search_titles(q)                 회의명
    search_folders(q)                프로젝트 이름 (web: 내가 멤버인 프로젝트)
    search_people(people)            그 사람이 주관 · 참석한 회의 + 그 사람의 발언 조금
                                     people = {user_id: 이름} — 이름으로 사람 찾기는 구성원 표(팀원 담당)에서 하고 넘겨 줌
    search_content(q)                원문 발화 · 요약 · 결정 사항 · 액션 아이템 (원문은 원문 권한이 있을 때만)
    search_all(q)                    탭별 결과 수 + 결과 (화면 첫 진입용)
"""
from __future__ import annotations

from db import transaction
from .common import ValidationError, require_user
from .minutes import _DAY, _IN_MY_PROJECTS, _LATEST

MAX_RESULTS = 100


def _q(q: str) -> str:
    q = (q or "").strip()
    if not q:
        raise ValidationError("검색어를 입력해 주세요.")
    if len(q) > 100:
        raise ValidationError("검색어가 너무 깁니다 (100자까지).")
    return q


def _like(q: str) -> str:
    """ILIKE 패턴 — %, _ 같은 특수 글자는 글자 그대로 찾게."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# 내가 볼 수 있는 회의 + 원문을 봐도 되는지(raw_ok) — crud/common.can_view_raw 와 같은 규칙
_VIS_CTE = f"""
WITH vis AS (
  SELECT m.id, m.title, m.directory_id, m.project_id, f.name AS project_name, m.host_user_id,
         {_LATEST} AS sort_at, to_char({_DAY}, 'YYYY.MM.DD') AS date_label,
         (%(actor)s IN (m.host_user_id, m.created_by, m.reviewer_id)
          OR EXISTS (SELECT 1 FROM meeting_attendees a WHERE a.meeting_id = m.id AND a.user_id = %(actor)s)
          OR EXISTS (SELECT 1 FROM meeting_shares s WHERE s.meeting_id = m.id AND s.recipient_user_id = %(actor)s
                     AND s.revoked_at IS NULL AND s.scope = 'full' AND NOT s.lock_raw)) AS raw_ok,
         (SELECT ms.id FROM meeting_summaries ms WHERE ms.meeting_id = m.id ORDER BY ms.version DESC LIMIT 1) AS sid
  FROM meetings m LEFT JOIN folders f ON f.id = m.project_id AND f.deleted_at IS NULL
  WHERE {_IN_MY_PROJECTS}
)
"""


def search_titles(*, actor_id: str, q: str, limit: int = MAX_RESULTS) -> list[dict]:
    """[회의명] 제목에 검색어가 들어간 회의 (최신순) + 요약 앞부분."""
    q = _q(q)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            _VIS_CTE + """
            SELECT v.id, v.title, v.directory_id, v.project_id, v.project_name, v.host_user_id, v.date_label,
                   left(s.overview, 200) AS summary_preview
            FROM vis v LEFT JOIN meeting_summaries s ON s.id = v.sid
            WHERE v.title ILIKE %(like)s
            ORDER BY v.sort_at DESC, v.id DESC LIMIT %(limit)s""",
            {"actor": actor_id, "like": _like(q), "limit": limit},
        )
        return cur.fetchall()


def search_folders(*, actor_id: str, q: str) -> list[dict]:
    """[폴더명] 이름에 검색어가 들어간 내 프로젝트 + 회의 수(내가 볼 수 있는 것) · 최근 회의 제목 · 멤버 수."""
    q = _q(q)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            f"""SELECT f.id, f.name, count(m.id) AS count, max({_LATEST}) AS last_meeting_at,
                       (array_agg(m.title ORDER BY {_LATEST} DESC) FILTER (WHERE m.id IS NOT NULL))[1] AS latest_title,
                       (SELECT count(*) FROM folder_members x WHERE x.folder_id = f.id) AS member_count
                FROM folders f
                LEFT JOIN meetings m ON m.project_id = f.id AND {_IN_MY_PROJECTS}
                WHERE f.deleted_at IS NULL AND f.name ILIKE %(like)s
                  AND (f.host_user_id = %(actor)s
                       OR EXISTS (SELECT 1 FROM folder_members fm WHERE fm.folder_id = f.id AND fm.user_id = %(actor)s))
                GROUP BY f.id ORDER BY f.sort_order, f.created_at""",
            {"actor": actor_id, "like": _like(q)},
        )
        return cur.fetchall()


def search_people(*, actor_id: str, people: dict[str, str], limit: int = MAX_RESULTS) -> list[dict]:
    """[구성원] people = {user_id: 이름}. 그 사람이 주관 · 참석한 회의 (최신순) + 그 사람의 발언 2개.
    발언은 원문 권한이 있을 때만, 화자 이름(speaker)이나 화자 매핑(meeting_speakers.user_id)으로 찾음."""
    if not people:
        return []
    ids, names = list(people), list(people.values())
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            _VIS_CTE + """
            , ppl AS (SELECT * FROM unnest(%(ids)s::text[], %(names)s::text[]) AS p(user_id, name))
            , hit AS (
              SELECT v.*, p.user_id, p.name, (p.user_id = v.host_user_id) AS is_host
              FROM vis v JOIN ppl p
                ON p.user_id = v.host_user_id
                OR EXISTS (SELECT 1 FROM meeting_attendees a WHERE a.meeting_id = v.id AND a.user_id = p.user_id)
            )
            SELECT h.id, h.title, h.project_id, h.project_name, h.directory_id, h.date_label, h.user_id, h.name,
                   CASE WHEN h.is_host THEN '주관' ELSE '참석' END AS role,
                   CASE WHEN h.raw_ok THEN (
                     SELECT COALESCE(array_agg(x.text ORDER BY x.position), '{}') FROM (
                       SELECT t.text, t.position FROM transcript_lines t
                       WHERE t.meeting_id = h.id AND t.speaker <> ''
                         AND (t.speaker = h.name
                              OR EXISTS (SELECT 1 FROM meeting_speakers sp WHERE sp.meeting_id = h.id
                                         AND sp.label = t.speaker AND sp.user_id = h.user_id))
                       ORDER BY t.position LIMIT 2) x)
                   ELSE '{}' END AS said
            FROM hit h ORDER BY h.sort_at DESC, h.id DESC, h.user_id LIMIT %(limit)s""",
            {"actor": actor_id, "ids": ids, "names": names, "limit": limit},
        )
        return cur.fetchall()


def search_content(*, actor_id: str, q: str, limit: int = MAX_RESULTS) -> list[dict]:
    """[회의 내용] 원문 발화(원문 권한 있을 때만) · 최신 요약 · 결정 사항 · 액션 아이템에서 찾기.
    회의마다 1줄: matches(찾은 곳 수) · snippets(앞 2곳: {where, text, line_id}) — line_id 로 원문 위치로 이동."""
    q = _q(q)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            _VIS_CTE + """
            , found AS (
              SELECT v.id, 'transcript' AS "where", t.text, t.line_id, t.position AS ord
              FROM vis v JOIN transcript_lines t ON t.meeting_id = v.id
              WHERE v.raw_ok AND t.speaker <> '' AND t.text ILIKE %(like)s
              UNION ALL
              SELECT v.id, 'summary', s.overview, NULL, -3 FROM vis v JOIN meeting_summaries s ON s.id = v.sid
              WHERE s.overview ILIKE %(like)s
              UNION ALL
              SELECT v.id, 'decision', p.text, NULL, -2 FROM vis v JOIN summary_points p ON p.summary_id = v.sid
              WHERE p.section = 'decision' AND p.text ILIKE %(like)s
              UNION ALL
              SELECT v.id, 'todo', d.text, NULL, -1 FROM vis v JOIN meeting_todos d ON d.meeting_id = v.id
              WHERE d.deleted_at IS NULL AND d.text ILIKE %(like)s
            )
            SELECT v.id, v.title, v.project_id, v.project_name, v.directory_id, v.host_user_id, v.date_label,
                   count(*) AS matches,
                   (array_agg(json_build_object('where', fo."where", 'text', fo.text, 'line_id', fo.line_id)
                              ORDER BY fo.ord))[1:2] AS snippets
            FROM found fo JOIN vis v ON v.id = fo.id
            GROUP BY v.id, v.title, v.project_id, v.project_name, v.directory_id, v.host_user_id, v.date_label, v.sort_at
            ORDER BY v.sort_at DESC, v.id DESC LIMIT %(limit)s""",
            {"actor": actor_id, "like": _like(q), "limit": limit},
        )
        return cur.fetchall()


def find_people(*, actor_id: str, q: str) -> dict[str, str]:
    """이름에 검색어가 들어간 구성원 {user_id: 이름} (구성원 표 people · 비활성 포함)."""
    q = _q(q)
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT user_id, name FROM people WHERE name ILIKE %s ORDER BY name, user_id", (_like(q),))
        return {r["user_id"]: r["name"] for r in cur.fetchall()}


def search_all(*, actor_id: str, q: str, people: dict[str, str] | None = None) -> dict:
    """탭별 결과 한 번에. people 을 안 주면 구성원 표에서 이름으로 찾음 (find_people)."""
    if people is None:
        people = find_people(actor_id=actor_id, q=q)
    res = {"title": search_titles(actor_id=actor_id, q=q), "folder": search_folders(actor_id=actor_id, q=q),
           "people": search_people(actor_id=actor_id, people=people),
           "content": search_content(actor_id=actor_id, q=q)}
    return {"counts": {k: len(v) for k, v in res.items()}, **res}