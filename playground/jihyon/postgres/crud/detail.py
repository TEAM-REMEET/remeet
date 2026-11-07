"""회의 상세 화면 (web data/models.py 의 Meeting).

get_meeting_detail() 은 web 목업 data/mock/meetings.json 한 개와 **같은 모양의 dict** 를 돌려줘요.
그래서 web 쪽에서는 data/repository.py 의 _meeting(d) 에 그대로 넣으면 Meeting 이 돼요.

    {id, title, when, date, project_id, directory_id, host, people, deep, time, file, initial_status,
     paragraphs[{id, speaker, text}], summary{text, src}, discussion[{label, text, src}], keywords[],
     decisions[{text, src}], open_issues[…], results[{kind, text, agendas, src}],
     agendas[{title, status, tone, deep, src, points[…],
              (안건별 상세가 있으면) background[…], topics[{title, points[{kind, text, src}]}],
              decisions[…], open_issues[…], next[…]}],
     todos[{id, item_id, text, owners[], due, src, urgent}], shared{from, scope, received, lock_raw} | None,
     branch_of{meeting, agenda_index} | None}
    + 화면에 없던 것: summary_version, can_view_raw, is_host, author, reviewer, can_edit

어디에 저장되나
    회의 정보 · 상태       meetings                         (crud/meetings.py)
    참석자                 meeting_attendees                set_attendees()
    원문                   transcript_lines                 (crud/meetings.replace_transcript)
    요약 내용 (버전)       meeting_summaries · summary_agendas · summary_topics · summary_points
                           save_summary() — 덮어쓰지 않고 version + 1, 화면은 최신 버전
    액션 아이템            meeting_todos · meeting_todo_owners   add_todo() · delete_todo() · restore_todo()
                           · set_todo_owners() · set_todo_due() — 요약 버전과 따로 (사람이 고치는 살아 있는 목록)
                           item_id = MeetingSummary 액션 item_id (백엔드 담당자 추천 · 지정에 쓰는 id)
    검토가 끝난 요약 JSON  meeting_summaries.summary_json   get_summary_json() (web AppState.summaries)
    안건 분리 요청         branch_requests                  request_branch()

권한 (crud/common.py)
    보기: 주관자 · 작성자 · 검토자 · 참석자 · 공유받은 사람 (web can_see — 프로젝트 멤버라는 것만으로는 X)
    원문(paragraphs): 원문 권한이 있을 때만 (요약만 공유 · 원문 잠금이면 빈 목록)
    쓰기(요약 · 액션 아이템 · 참석자): 검토자, 없으면 주관자 (미리보기 땐 작성자 · 주관자). 분리 요청: 볼 수 있는 누구나
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from db import transaction
from .common import (
    NotFoundError,
    PermissionDenied,
    ValidationError,
    can_access_meeting,
    can_view_raw,
    invalidate,
    is_editor,
    required,
)

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS = "월화수목금토일"
ORIGINS = frozenset({"ai", "edited"})
TONES = frozenset({"ok", "warn", "hold", "info"})
DETAIL_KEYS = ("background", "topics", "decisions", "open_issues", "next")
# 안건 상세 키 → summary_points.section
_AGENDA_SECTIONS = {"points": "point", "background": "background", "decisions": "decision",
                    "open_issues": "open_issue", "next": "next"}
_MEETING_SECTIONS = {"discussion": "discussion", "decisions": "decision", "open_issues": "open_issue",
                     "results": "result"}


# =====================================================================
# 입력 검사
# =====================================================================
def _src(v, label: str) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str) or not all(isinstance(x, (str, int)) for x in v):
        raise ValidationError(f"{label}의 근거(src)는 발화 번호 목록이어야 합니다.")
    return [str(x) for x in v]


def _lines(items, label: str) -> list[dict]:
    """[{text, src, (label | kind | agendas)}] 검사."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValidationError(f"{label}은(는) 목록이어야 합니다.")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValidationError(f"{label}의 항목은 {{text, src}} 형태여야 합니다.")
        out.append({**it, "text": required(it.get("text"), f"{label} 내용"), "src": _src(it.get("src"), label)})
    return out


def _check_content(c: dict) -> None:
    agendas = c.get("agendas") or []
    for a in agendas:
        required(a.get("title"), "안건 제목")
        if a.get("tone", "info") not in TONES:
            raise ValidationError(f"안건 tone 은 {sorted(TONES)} 중 하나입니다: {a.get('tone')}")
        _src(a.get("src"), "안건")
        for k in _AGENDA_SECTIONS:
            _lines(a.get(k), f"안건 {k}")
        for t in a.get("topics") or []:
            required(t.get("title"), "소주제 제목")
            _lines(t.get("points"), "소주제 내용")
    for k in _MEETING_SECTIONS:
        _lines(c.get(k), k)
    for r in c.get("results") or []:
        for n in r.get("agendas") or []:
            if not (isinstance(n, int) and 1 <= n <= len(agendas)):
                raise ValidationError(f"결과의 안건 번호({n})가 안건 범위(1~{len(agendas)})를 벗어났습니다.")
    s = c.get("summary") or {}
    _src(s.get("src"), "요약")


def _check_todos(todos: list[dict]) -> None:
    for t in todos:
        required(t.get("text"), "액션 아이템 내용")
        _src(t.get("src"), "액션 아이템")
        if t.get("due_date") not in (None, ""):
            _iso(t["due_date"])


def _iso(v) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise ValidationError(f"마감일 형식이 잘못됐습니다 (YYYY-MM-DD): {v}") from None


# =====================================================================
# 요약 저장
# =====================================================================
def _lock_meeting(cur, meeting_id: str) -> None:
    cur.execute("SELECT 1 FROM meetings WHERE id = %s FOR UPDATE", (meeting_id,))


def _insert_points(cur, sid: int, section: str, items: list[dict], *, agenda: int | None = None,
                   topic: int | None = None) -> None:
    for i, it in enumerate(items):
        cur.execute(
            """INSERT INTO summary_points (summary_id, section, agenda_position, topic_position, position,
                                           label, kind, text, src, agendas)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (sid, section, agenda, topic, i, it.get("label") or "", it.get("kind") or "", it["text"].strip(),
             it["src"], list(it.get("agendas") or [])),
        )


def _insert_todos(cur, meeting_id: str, todos: list[dict], *, origin: str, summary_id: int | None,
                  created_by: str | None) -> list[int]:
    cur.execute("SELECT COALESCE(MAX(position) + 1, 0) AS p FROM meeting_todos WHERE meeting_id = %s", (meeting_id,))
    pos = cur.fetchone()["p"]
    ids = []
    for i, t in enumerate(todos):
        cur.execute(
            """INSERT INTO meeting_todos (meeting_id, position, text, due_label, due_date, src, urgent, origin,
                                          summary_id, owner_raw, created_by, item_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (meeting_id, pos + i, t["text"].strip(), t.get("due") or "",
             _iso(t["due_date"]) if t.get("due_date") else None, _src(t.get("src"), "액션 아이템"),
             bool(t.get("urgent")), origin, summary_id, t.get("owner_raw") or "", created_by,
             t.get("item_id") or None),
        )
        tid = cur.fetchone()["id"]
        ids.append(tid)
        _replace_owners(cur, tid, t.get("owners") or [], created_by)
    return ids


def _replace_owners(cur, todo_id: int, owners: list[str], by: str | None) -> None:
    cur.execute("DELETE FROM meeting_todo_owners WHERE todo_id = %s", (todo_id,))
    for j, uid in enumerate(dict.fromkeys(o for o in owners if o)):
        cur.execute("INSERT INTO meeting_todo_owners (todo_id, user_id, position, assigned_by) VALUES (%s, %s, %s, %s)",
                    (todo_id, uid, j, by))


def insert_summary(cur, *, meeting_id: str, content: dict, todos: list[dict] | None = None,
                   origin: str = "ai", created_by: str | None = None,
                   analysis_id: int | None = None, source_id: int | None = None,
                   summary_json: dict | None = None) -> dict:
    """권한 검사 없이 요약 1벌 저장 (같은 트랜잭션 안에서 쓰는 내부용 · seed.py · import_summary.py).
    화면/API 에서는 save_summary() 를 쓰세요.

    content = 목업 meetings.json 의 요약 부분: summary · discussion · decisions · open_issues · results · agendas
    todos   = 주면 이 요약에서 나온 AI 액션 아이템으로 넣음 ({text, owners, due, src, urgent, item_id}).
              이전 AI 액션 아이템은 지우고(주관자가 직접 추가한 건 유지), 안 주면(None) 액션 아이템은 그대로 둠.
    summary_json = 검토가 끝난 MeetingSummary JSON 원본 (web AppState.summaries · 백엔드 담당자 추천에 보내는 것)
    """
    if origin not in ORIGINS:
        raise ValidationError("origin 은 'ai' 또는 'edited' 입니다.")
    _check_content(content)
    _check_todos(todos or [])
    for col, table, val in (("analysis_id", "meeting_analyses", analysis_id),
                            ("source_id", "meeting_sources", source_id)):
        if val is not None:
            cur.execute(f"SELECT 1 FROM {table} WHERE id = %s AND meeting_id = %s", (val, meeting_id))
            if cur.fetchone() is None:
                raise ValidationError(f"{col}={val} 은(는) 이 회의의 것이 아닙니다.")

    _lock_meeting(cur, meeting_id)
    cur.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM meeting_summaries WHERE meeting_id = %s", (meeting_id,))
    version = cur.fetchone()["v"]
    summ = content.get("summary") or {}
    overview, overview_src = (summ.get("text") or "").strip(), _src(summ.get("src"), "요약")
    cur.execute(
        """INSERT INTO meeting_summaries (meeting_id, version, origin, analysis_id, source_id, overview,
                                          overview_src, created_by, summary_json)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (meeting_id, version, origin, analysis_id, source_id, overview, overview_src, created_by,
         Jsonb(summary_json) if summary_json is not None else None),
    )
    summary = cur.fetchone()
    sid = summary["id"]

    for i, a in enumerate(content.get("agendas") or []):
        detailed = bool(a.get("detailed")) or any(k in a for k in DETAIL_KEYS)
        cur.execute(
            """INSERT INTO summary_agendas (summary_id, position, title, status, tone, deep, src, detailed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (sid, i, a["title"].strip(), a.get("status") or "", a.get("tone") or "info", bool(a.get("deep")),
             _src(a.get("src"), "안건"), detailed),
        )
        for key, section in _AGENDA_SECTIONS.items():
            _insert_points(cur, sid, section, _lines(a.get(key), key), agenda=i)
        for j, t in enumerate(a.get("topics") or []):
            cur.execute("INSERT INTO summary_topics (summary_id, agenda_position, position, title) VALUES (%s, %s, %s, %s)",
                        (sid, i, j, t["title"].strip()))
            _insert_points(cur, sid, "topic_point", _lines(t.get("points"), "소주제 내용"), agenda=i, topic=j)
    for key, section in _MEETING_SECTIONS.items():
        _insert_points(cur, sid, section, _lines(content.get(key), key))

    # 목록 화면 · 예전 코드용 한 줄 요약도 최신으로
    cur.execute("UPDATE meetings SET summary_text = %s, summary_src = %s WHERE id = %s",
                (overview, overview_src, meeting_id))
    if todos is not None:
        cur.execute("DELETE FROM meeting_todos WHERE meeting_id = %s AND origin = 'ai'", (meeting_id,))
        _insert_todos(cur, meeting_id, todos, origin="ai", summary_id=sid, created_by=created_by)
    return summary


def save_summary(*, actor_id: str, meeting_id: str, content: dict, todos: list[dict] | None = None,
                 origin: str = "edited", analysis_id: int | None = None, source_id: int | None = None,
                 summary_json: dict | None = None) -> dict:
    """요약 새 버전 저장 (주관자만). 화면 「편집」 저장 · AI 재요약 결과 저장에 씀. 이전 버전은 남음."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        row = insert_summary(cur, meeting_id=meeting_id, content=content, todos=todos, origin=origin,
                             created_by=actor_id, analysis_id=analysis_id, source_id=source_id,
                             summary_json=summary_json)
    invalidate("_base_meetings")
    return row


def list_summary_versions(*, actor_id: str, meeting_id: str) -> list[dict]:
    """요약 버전 이력 (최신이 먼저)."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id)
        cur.execute(
            """SELECT id, version, origin, analysis_id, source_id, created_by, created_at,
                      left(overview, 200) AS overview_preview
               FROM meeting_summaries WHERE meeting_id = %s ORDER BY version DESC""",
            (meeting_id,),
        )
        return cur.fetchall()


# =====================================================================
# 회의 상세 읽기
# =====================================================================
def _when(ts: datetime | None) -> tuple[str, str]:
    """held_at → ("10월 29일 (수)", "10/29")"""
    if ts is None:
        return "", ""
    d = ts.astimezone(KST).date() if isinstance(ts, datetime) else ts
    return f"{d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})", f"{d.month}/{d.day}"


def _ev(p: dict) -> dict:
    return {"text": p["text"], "src": list(p["src"])}


def _summary_content(cur, sid: int | None) -> dict:
    empty = {"discussion": [], "decisions": [], "open_issues": [], "results": [], "agendas": []}
    if sid is None:
        return empty
    cur.execute("SELECT * FROM summary_agendas WHERE summary_id = %s ORDER BY position", (sid,))
    agendas = cur.fetchall()
    cur.execute("SELECT * FROM summary_topics WHERE summary_id = %s ORDER BY agenda_position, position", (sid,))
    topics = defaultdict(list)
    for t in cur.fetchall():
        topics[t["agenda_position"]].append(t)
    cur.execute("""SELECT * FROM summary_points WHERE summary_id = %s
                   ORDER BY section, agenda_position NULLS FIRST, topic_position NULLS FIRST, position""", (sid,))
    pts = defaultdict(list)            # (section, agenda, topic) → [point]
    for p in cur.fetchall():
        pts[(p["section"], p["agenda_position"], p["topic_position"])].append(p)

    out = {
        "discussion": [{"label": p["label"], "text": p["text"], "src": list(p["src"])}
                       for p in pts[("discussion", None, None)]],
        "decisions": [_ev(p) for p in pts[("decision", None, None)]],
        "open_issues": [_ev(p) for p in pts[("open_issue", None, None)]],
        "results": [{"kind": p["kind"], "text": p["text"], "agendas": list(p["agendas"]), "src": list(p["src"])}
                    for p in pts[("result", None, None)]],
        "agendas": [],
    }
    for a in agendas:
        i = a["position"]
        item = {"title": a["title"], "status": a["status"], "tone": a["tone"], "deep": a["deep"],
                "src": list(a["src"]), "points": [_ev(p) for p in pts[("point", i, None)]]}
        if a["detailed"]:
            item.update(
                background=[_ev(p) for p in pts[("background", i, None)]],
                topics=[{"title": t["title"],
                         "points": [{"kind": p["kind"], "text": p["text"], "src": list(p["src"])}
                                    for p in pts[("topic_point", i, t["position"])]]}
                        for t in topics[i]],
                decisions=[_ev(p) for p in pts[("decision", i, None)]],
                open_issues=[_ev(p) for p in pts[("open_issue", i, None)]],
                next=[_ev(p) for p in pts[("next", i, None)]],
            )
        out["agendas"].append(item)
    return out


def _todos(cur, meeting_id: str, *, deleted: bool = False) -> list[dict]:
    cur.execute(
        f"""SELECT t.*, COALESCE(array_agg(o.user_id ORDER BY o.position) FILTER (WHERE o.user_id IS NOT NULL),
                               '{{}}') AS owners
            FROM meeting_todos t LEFT JOIN meeting_todo_owners o ON o.todo_id = t.id
            WHERE t.meeting_id = %s AND t.deleted_at IS {'NOT ' if deleted else ''}NULL
            GROUP BY t.id ORDER BY t.position""",
        (meeting_id,),
    )
    return [{"id": t["id"], "item_id": t["item_id"] or "", "text": t["text"], "owners": list(t["owners"]),
             "due": t["due_label"],
             "src": list(t["src"]), "urgent": t["urgent"], "origin": t["origin"], "owner_raw": t["owner_raw"],
             "deleted_at": t["deleted_at"]} for t in cur.fetchall()]


def get_meeting_detail(*, actor_id: str, meeting_id: str, version: int | None = None) -> dict:
    """회의 상세 1건 (web 목업 meetings.json 한 개와 같은 모양). version 을 안 주면 최신 요약."""
    with transaction(actor_id) as cur:
        m = can_access_meeting(cur, meeting_id, actor_id)
        raw_ok = can_view_raw(cur, meeting_id, actor_id)
        cur.execute("SELECT user_id FROM meeting_attendees WHERE meeting_id = %s ORDER BY position, user_id",
                    (meeting_id,))
        people = [r["user_id"] for r in cur.fetchall()]
        cur.execute(
            """SELECT * FROM meeting_summaries WHERE meeting_id = %s AND (%s::int IS NULL OR version = %s::int)
               ORDER BY version DESC LIMIT 1""",
            (meeting_id, version, version),
        )
        summ = cur.fetchone()
        if version is not None and summ is None:
            raise NotFoundError(f"요약 버전 {version} 이 없습니다.")
        content = _summary_content(cur, summ["id"] if summ else None)
        paragraphs = []
        if raw_ok:
            cur.execute("SELECT line_id, speaker, text FROM transcript_lines WHERE meeting_id = %s ORDER BY position",
                        (meeting_id,))
            paragraphs = [{"id": r["line_id"], "speaker": r["speaker"], "text": r["text"]} for r in cur.fetchall()]
        cur.execute("SELECT keyword FROM meeting_keywords WHERE meeting_id = %s ORDER BY position", (meeting_id,))
        keywords = [r["keyword"] for r in cur.fetchall()]
        cur.execute(
            """SELECT sender_id, scope, lock_raw, shared_on FROM meeting_shares
               WHERE meeting_id = %s AND recipient_user_id = %s AND revoked_at IS NULL
               ORDER BY (scope = 'full' AND NOT lock_raw) DESC, id DESC LIMIT 1""",
            (meeting_id, actor_id),
        )
        sh = cur.fetchone()
        sender = None
        if sh:  # 보낸 사람 "이름 · 팀" (구성원 표 people · teams). 구성원 표에 없으면 id 그대로
            cur.execute("""SELECT p.name, t.name AS team FROM people p LEFT JOIN teams t ON t.team_id = p.team_id
                           WHERE p.user_id = %s""", (sh["sender_id"],))
            who = cur.fetchone()
            sender = f"{who['name']} · {who['team']}" if who and who["team"] else (who["name"] if who else sh["sender_id"])
        todos = _todos(cur, meeting_id)

    when, md = _when(m["held_at"])
    return {
        "id": m["id"], "title": m["title"], "when": when, "date": md,
        "project_id": m["project_id"], "directory_id": m["directory_id"],
        "host": m["host_user_id"], "people": people, "deep": m["is_deep"], "time": m["time_label"],
        "file": m["file_name"], "initial_status": m["status"], "paragraphs": paragraphs,
        "summary": {"text": summ["overview"] if summ else m["summary_text"],
                    "src": list(summ["overview_src"] if summ else m["summary_src"])},
        **content,
        "keywords": keywords,
        "todos": [{k: t[k] for k in ("id", "item_id", "text", "owners", "due", "src", "urgent")} for t in todos],
        # from: 보낸 사람 id → 화면에서 구성원 표로 "이름 · 팀" 으로 바꿔 보여 주기
        "shared": ({"from": sender, "sender_id": sh["sender_id"], "scope": sh["scope"], "lock_raw": sh["lock_raw"],
                    "received": f"{sh['shared_on'].month}/{sh['shared_on'].day}"} if sh else None),
        "branch_of": ({"meeting": m["branch_of_id"], "agenda_index": m["branch_agenda_index"]}
                      if m["branch_of_id"] else None),
        "summary_version": summ["version"] if summ else None,
        "can_view_raw": raw_ok, "is_host": m["host_user_id"] == actor_id,
        # 작성 흐름 (migrations/008): 작성자 · 검토자 · 지금 내가 고칠 수 있는지 (신입이 제출한 뒤면 False)
        "author": m["created_by"], "reviewer": m["reviewer_id"], "can_edit": is_editor(m, actor_id),
    }


# =====================================================================
# 참석자
# =====================================================================
def set_attendees(*, actor_id: str, meeting_id: str, user_ids: list[str]) -> list[str]:
    """참석자 목록 바꾸기 (주관자만). 주관자는 늘 참석자로 들어감 (화면 규칙)."""
    with transaction(actor_id) as cur:
        m = can_access_meeting(cur, meeting_id, actor_id, write=True)
        ids = list(dict.fromkeys([m["host_user_id"], *(required(u, "참석자 ID") for u in user_ids)]))
        cur.execute("DELETE FROM meeting_attendees WHERE meeting_id = %s", (meeting_id,))
        for i, uid in enumerate(ids):
            cur.execute("INSERT INTO meeting_attendees (meeting_id, user_id, position) VALUES (%s, %s, %s)",
                        (meeting_id, uid, i))
    invalidate("_base_meetings", "base_folders")
    return ids


# =====================================================================
# 액션 아이템
# =====================================================================
def _todo_for_write(cur, todo_id: int, actor_id: str) -> dict:
    cur.execute("SELECT * FROM meeting_todos WHERE id = %s", (todo_id,))
    t = cur.fetchone()
    if t is None:
        raise NotFoundError("액션 아이템을 찾을 수 없습니다.")
    can_access_meeting(cur, t["meeting_id"], actor_id, write=True)
    return t


def set_todo_due(*, actor_id: str, todo_id: int, due: str, due_date: str | date | None = None) -> dict:
    """마감일 고치기 (담당자 추천 단계 · web AppState.todo_due). due = 화면 표기("11/5" · "오늘"), due_date = 날짜(선택)."""
    with transaction(actor_id) as cur:
        _todo_for_write(cur, todo_id, actor_id)
        cur.execute("UPDATE meeting_todos SET due_label = %s, due_date = %s WHERE id = %s RETURNING id, due_label, due_date",
                    ((due or "").strip(), _iso(due_date) if due_date else None, todo_id))
        row = cur.fetchone()
    invalidate("_base_meetings")
    return row


def get_summary_json(*, actor_id: str, meeting_id: str) -> dict | None:
    """가장 최근에 저장한 「검토가 끝난 MeetingSummary JSON」 (없으면 None). 담당자 추천 API 에 보낼 때."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id)
        cur.execute("""SELECT summary_json FROM meeting_summaries WHERE meeting_id = %s AND summary_json IS NOT NULL
                       ORDER BY version DESC LIMIT 1""", (meeting_id,))
        row = cur.fetchone()
    return row["summary_json"] if row else None


def list_todos(*, actor_id: str, meeting_id: str, deleted: bool = False) -> list[dict]:
    """액션 아이템 (deleted=True 면 「삭제한 액션 아이템」 목록)."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id)
        return _todos(cur, meeting_id, deleted=deleted)


def add_todo(*, actor_id: str, meeting_id: str, text: str, owners: list[str] | None = None, due: str = "",
             due_date: str | date | None = None, src: list[str] | None = None, urgent: bool = False) -> dict:
    """액션 아이템 직접 추가 (주관자만)."""
    todo = {"text": text, "owners": owners or [], "due": due, "due_date": due_date, "src": src or [], "urgent": urgent}
    _check_todos([todo])
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        _lock_meeting(cur, meeting_id)
        (tid,) = _insert_todos(cur, meeting_id, [todo], origin="manual", summary_id=None, created_by=actor_id)
        row = next(t for t in _todos(cur, meeting_id) if t["id"] == tid)
    invalidate("_base_meetings")
    return row


def delete_todo(*, actor_id: str, todo_id: int) -> None:
    """삭제 (주관자만 · restore_todo 로 되돌리기 가능)."""
    with transaction(actor_id) as cur:
        _todo_for_write(cur, todo_id, actor_id)
        cur.execute("UPDATE meeting_todos SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL", (todo_id,))
    invalidate("_base_meetings")


def restore_todo(*, actor_id: str, todo_id: int) -> None:
    with transaction(actor_id) as cur:
        _todo_for_write(cur, todo_id, actor_id)
        cur.execute("UPDATE meeting_todos SET deleted_at = NULL WHERE id = %s", (todo_id,))
    invalidate("_base_meetings")


def set_todo_owners(*, actor_id: str, todo_id: int, owners: list[str]) -> list[str]:
    """담당자 지정 (여러 명 · 빈 목록이면 담당자 비우기). 주관자만."""
    with transaction(actor_id) as cur:
        t = _todo_for_write(cur, todo_id, actor_id)
        if t["deleted_at"] is not None:
            raise ValidationError("삭제한 액션 아이템에는 담당자를 지정할 수 없습니다.")
        _replace_owners(cur, todo_id, owners, actor_id)
    invalidate("_base_meetings")
    return list(dict.fromkeys(o for o in owners if o))


# =====================================================================
# 안건 분리 요청
# =====================================================================
def request_branch(*, actor_id: str, meeting_id: str) -> None:
    """참석자 등이 주관자에게 안건 분리를 요청 (같은 사람이 다시 눌러도 1번)."""
    with transaction(actor_id) as cur:
        m = can_access_meeting(cur, meeting_id, actor_id)
        if m["host_user_id"] == actor_id:
            raise ValidationError("주관자는 요청 없이 바로 안건을 분리할 수 있습니다.")
        cur.execute("INSERT INTO branch_requests (meeting_id, requested_by) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (meeting_id, actor_id))


def list_branch_requests(*, actor_id: str, meeting_id: str) -> list[dict]:
    """주관자가 받은 분리 요청."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        cur.execute("SELECT requested_by, requested_at FROM branch_requests WHERE meeting_id = %s ORDER BY requested_at",
                    (meeting_id,))
        return cur.fetchall()

