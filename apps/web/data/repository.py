"""데이터 저장소.

백엔드 API 에서 읽는 것
    구성원 · 팀 · 직무          GET /api/people · /api/teams · /api/roles · /api/skills   (Neo4j)
    회의 종류                   GET /api/meeting-types                                   (PostgreSQL)
    회의 목록 · 상세            GET /api/meetings → GET /api/meetings/{id}               (PostgreSQL)
    프로젝트 · 멤버 · 회의 · 브리프  GET /api/projects → /api/projects/{id}/members · /meetings · /briefs
백엔드 API 에 쓰는 것 (회의록 쓰기 섹션)
    새 회의록 저장 · 생성 · 확정 · 편집(안건), 할 일 추가 · 수정 · 삭제 · 되돌리기, 안건 분리 요청, 회의 삭제,
    결과 전달 상태 · 전송 기록, 프로젝트 멤버 · 삭제, 공유 · 공유 취소                    (PostgreSQL)
알림 · 외부 연동(도구 상태 · 전송 실패 · 조직 이름)도 백엔드 API (PostgreSQL)
나머지(위키 · 담당자 후보 목업 · 비용 …)는 아직 data/mock/*.json 에서 읽어요.
실제 API 로 바꿀 때는 이 파일의 함수 시그니처만 유지하고 내부 구현을 교체하면 됩니다.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import streamlit as st

from config import ASK_TIMEOUT, EXPORT_TIMEOUT, MEETING_SUMMARY_TIMEOUT, REVIEW_TIMEOUT
from data import api

from data.models import (
    MISC_AGENDA,
    Agenda,
    AgendaTopic,
    MeetingResult,
    TaggedPoint,
    BriefItem,
    Candidate,
    Discussion,
    Evidence,
    Folder,
    GraphEdge,
    GraphNode,
    ExportFailure,
    IntegrationTool,
    KnowledgeGraph,
    Meeting,
    MeetingType,
    Notification,
    Paragraph,
    Person,
    Predecessor,
    ShareInfo,
    TimelineItem,
    Todo,
    Team,
    TeamMember,
)

MOCK_DIR = Path(__file__).parent / "mock"


def _load(name: str) -> dict | list:
    with open(MOCK_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def _evidence(items: list[dict]) -> tuple[Evidence, ...]:
    return tuple(Evidence(i["text"], tuple(i["src"])) for i in items)


# ---------------------------------------------------------------- people
# ---------------------------------------------------------------- 구성원 (백엔드 → Neo4j)
def _token() -> str:
    """로그인한 사람의 출입증. state.store 를 import 하면 순환 import 라 세션 상태에서 바로 읽어요."""
    return getattr(st.session_state.get("_app_state"), "token", "")


# shortcut: 10분 캐시 (클릭마다 Neo4j를 다녀오는 지연을 줄임). 이 앱에서 바꾸면 바로 비우지만,
#   다른 곳(DB 직접 수정)의 변경은 최대 10분 늦게 보임 → 바로 보려면 프론트 재시작 또는 화면 메뉴의 Clear cache
_PEOPLE_TTL = 600  # 초


@st.cache_data(ttl=_PEOPLE_TTL, show_spinner=False)
def _people_rows(token: str) -> list[dict]:
    return api.call("GET", "/api/people", token=token)


def people_rows() -> list[dict]:
    """구성원 전체 (GET /api/people): user_id · name · email · permission · position · is_active · team · job · skills."""
    return _people_rows(_token())


@st.cache_data(ttl=_PEOPLE_TTL, show_spinner=False)
def _catalog(kind: str, token: str) -> list[dict]:
    return api.call("GET", f"/api/{kind}", token=token)


def catalog(kind: str) -> list[dict]:
    """kind: teams | roles | skills"""
    return _catalog(kind, _token())


def _people_changed() -> None:
    _people_rows.clear()
    _catalog.clear()  # 새 직무를 입력하면 직무 목록도 바뀜
    org_changed()  # 열려 있는 다른 화면도 새로 그림


def create_person(body: dict, token: str) -> dict:
    row = api.call("POST", "/api/people", body, token)
    _people_changed()
    return row


def update_person(user_id: str, changes: dict, token: str) -> dict:
    row = api.call("PATCH", f"/api/people/{user_id}", changes, token)
    _people_changed()
    return row


def login(email: str, password: str) -> dict:
    """{token, user: {user_id, name, email, permission}}. 틀리면 api.ApiError."""
    return api.call("POST", "/api/auth/login", {"email": email, "password": password})


def me(token: str) -> dict:
    """토큰 → 로그인한 사람 (새로고침 후 로그인 되살리기). 만료 · 위조면 api.ApiError."""
    return api.call("GET", "/api/auth/me", token=token)


def start_summary_job(request: dict, draft: dict) -> dict:
    """회의 등록 입력 → 요약 · 액션 아이템을 백그라운드로 시작. 입력이 틀리거나(422) 이미 분석 중이면(409) api.ApiError."""
    return api.call("POST", "/api/meeting-jobs", {"request": request, "draft": draft}, _token())


def current_summary_job() -> dict:
    """내 분석 작업: status = none | running | done(result 포함) | failed(message) · request · draft 는 입력값."""
    return api.call("GET", "/api/meeting-jobs/current", None, _token())


def clear_summary_job() -> None:
    """끝난 분석 작업 지우기."""
    api.call("DELETE", "/api/meeting-jobs/current", None, _token())


def review_meeting(meeting_id: str, edits: list[dict]) -> dict:
    """사용자 수정 반영 · 근거 연결 · 검증 → 최종 JSON (POST /api/meetings/{id}/review). 실패하면 api.ApiError."""
    return api.call("POST", f"/api/meetings/{meeting_id}/review", {"edits": edits}, _token(), timeout=REVIEW_TIMEOUT)


def recommend_assignees(summary: dict) -> dict[str, dict]:
    """검토가 끝난 MeetingSummary → Neo4j 적재 · 담당자 추천 (POST /api/meetings/{id}/recommendations).
    돌려줌: {item_id: 추천 결과}. 몇십 초 ~ 몇 분 걸림. 실패하면 api.ApiError (같은 요청을 다시 보내도 됨)."""
    res = api.call("POST", f"/api/meetings/{summary['meeting_id']}/recommendations", summary, _token(),
                   timeout=MEETING_SUMMARY_TIMEOUT)
    return {item["item_id"]: item for item in res["items"]}


def save_final_assignees(meeting_id: str, changes: list[dict]) -> dict:
    """담당자 추천 단계에서 바꾼 담당자 · 마감일 → 최종 JSON에 반영 (PUT /api/meetings/{id}/final/assignees).
    돌려줌: 고쳐진 최종 JSON. 실패하면 api.ApiError."""
    return api.call("PUT", f"/api/meetings/{meeting_id}/final/assignees", {"changes": changes}, _token())


def confirm_action_items(meeting_id: str, items: list[dict]) -> list[dict]:
    """「다음: 최종 회의록」: 이 회의 액션 아이템 전부의 담당자 · 마감일을 한 번에 확정 (PUT /api/meetings/{id}/action-items).
    items = [{item_id, user_ids, due: "YYYY-MM-DD"}]. 하나라도 틀리면 아무것도 저장 안 되고 api.ApiError."""
    return api.call("PUT", f"/api/meetings/{meeting_id}/action-items", {"items": items}, _token())


def recommendation_config(item: dict) -> dict:
    """추천 결과 한 건 → 담당자 지정 창이 쓰는 모양 (assignee_config()와 같은 키)."""
    positions = {r["user_id"]: r.get("position") or "" for r in people_rows()}  # 추천 응답엔 직급이 없어서 구성원 표에서

    def candidate(c: dict) -> Candidate:
        match = "\n".join(x for x in (", ".join(c["skills"]), f"진행 중 업무 {c['open_tasks']}건") if x)  # 스킬 / 업무 수 따로 줄
        return Candidate(c["user_id"], c["name"], c["team"] or "", c["role"] or "", round(c["score"]),
                         round(c["skill_score"]), round(c["role_team_score"] or 0), round(c["workload_score"]),
                         c["open_tasks"], c["attended"], tuple(c["skills"]), match, positions.get(c["user_id"], ""))

    def md(day: str | None) -> str:  # 2026-11-05 → 11/5
        return f"{int(day[5:7])}/{int(day[8:10])}" if day else ""

    return {
        "candidates": [candidate(c) for c in item["candidates"]],
        "predecessors": [Predecessor(p["name"], p["team"] or "", p["role"] or "", p["task"], p["meeting"] or "", md(p["date"]),
                                     p["user_id"])
                         for p in item["predecessors"]],
        "needed_skills": set(), "related_teams": set(), "reasons": {},
    }


def people() -> dict[str, Person]:
    """조직 구성원 (Neo4j). short는 원문 화자 이름인데 Neo4j에 따로 없어서 이름을 그대로 써요."""
    return {r["user_id"]: Person(r["user_id"], r["name"], r["name"], r["team"] or "") for r in people_rows()}


def people_by_short_name() -> dict[str, str]:
    """원문 화자 이름(서윤) → 사람 id(yun)."""
    return {p.short: p.id for p in people().values()}


# ---------------------------------------------------------------- meetings
def _owners(t: dict) -> tuple[str, ...]:
    """액션 아이템 담당자들 (사람 id). owners → assignee_user_ids(에이전트 출력 · 대조 안 된 null 은 뺌) → 예전 owner."""
    if "owners" in t:
        raw = t["owners"]
    elif "assignee_user_ids" in t:
        raw = t["assignee_user_ids"]
    else:
        raw = [t.get("owner")]
    return tuple(dict.fromkeys(pid for pid in raw if pid))  # 빈 값 · 중복 제외, 순서 유지


def _agenda(a: dict) -> Agenda:
    """안건. background/topics/decisions/open_issues/next 가 있으면 「안건별 상세」로 그대로 씀."""
    detail_keys = ("background", "topics", "decisions", "open_issues", "next")
    return Agenda(
        a["title"], a["status"], a["tone"], a["deep"], tuple(a["src"]), _evidence(a["points"]),
        background=_evidence(a.get("background", [])),
        topics=tuple(
            AgendaTopic(t["title"], tuple(TaggedPoint(p["kind"], p["text"], tuple(p.get("src", ()))) for p in t["points"]))
            for t in a.get("topics", [])
        ),
        decisions=_evidence(a.get("decisions", [])),
        open_issues=_evidence(a.get("open_issues", [])),
        next_candidates=_evidence(a.get("next", [])),
        detailed=any(k in a for k in detail_keys),
    )


def _todo(t: dict, deleted: bool = False) -> Todo:
    return Todo(t["text"], _owners(t), t["due"], tuple(t["src"]), t["urgent"], item_id=t.get("item_id") or "",
                due_iso=t.get("due_date") or "", todo_id=t.get("id") or 0, deleted=deleted, agenda=t.get("agenda") or "")


def _todos(d: dict) -> tuple[Todo, ...]:
    """할 일 + 삭제한 할 일(deleted_todos)을 원래 순서(id 순)로. 번호가 지우기 · 되돌리기와 상관없이 그대로 남아요."""
    rows = [(t, False) for t in d["todos"]] + [(t, True) for t in d.get("deleted_todos", ())]
    rows.sort(key=lambda r: r[0].get("id") or 0)
    return tuple(_todo(t, deleted) for t, deleted in rows)


def _meeting(d: dict) -> Meeting:
    m = _meeting_only(d)
    if any(t.agenda for t in m.todos):  # 직접 추가하며 고른 안건 → 그 안건 아래에 보여 줌 (DB agenda_title)
        m = _attach_added_todos(m, 0, [i for i, t in enumerate(m.todos) if t.deleted])
    return m


def _meeting_only(d: dict) -> Meeting:
    shared = d.get("shared")
    return Meeting(
        id=d["id"],
        title=d["title"],
        when=d["when"],
        date=d["date"],
        project_id=d.get("project_id"),
        directory_id=d["directory_id"],
        host=d["host"],
        people=tuple(d["people"]),
        deep=d["deep"],
        time=d["time"],
        file=d["file"],
        initial_status=d["initial_status"],
        paragraphs=tuple(Paragraph(p["id"], p["speaker"], p["text"]) for p in d["paragraphs"]),
        summary=Evidence(d["summary"]["text"], tuple(d["summary"]["src"])),
        discussion=(
            tuple(Discussion(x["label"], x["text"], tuple(x["src"])) for x in d["discussion"])
            if d.get("discussion")
            else None
        ),
        keywords=tuple(d["keywords"]),
        decisions=_evidence(d["decisions"]),
        open_issues=_evidence(d["open_issues"]),
        agendas=tuple(_agenda(a) for a in d["agendas"]),
        results=tuple(
            MeetingResult(r["kind"], r["text"], tuple(r.get("agendas", ())), tuple(r.get("src", ())))
            for r in d.get("results", ())
        ),
        todos=_todos(d),
        shared=ShareInfo(shared["from"], shared["scope"], shared["received"], shared["lock_raw"]) if shared else None,
        branch_of=(d.get("branch_of") or {}).get("meeting"),
        author=d.get("author") or "",
        reviewer=d.get("reviewer") or "",
    )


# ---------------------------------------------------------------- 회의 · 프로젝트 (백엔드 → PostgreSQL)
# shortcut: 1분 캐시 (화면을 그릴 때마다 회의 수만큼 상세를 다시 받지 않게). 다른 사람이 바꾼 내용은 최대 1분 늦게 보임
#   → 바로 보려면 meetings_changed() (이 앱에서 회의를 바꾸는 API 를 연결하면 그 뒤에 부르기)
_MEETINGS_TTL = 60  # 초
_PAGE = 200  # 목록 API 한 번에 받는 최대 개수 (백엔드 MAX_PAGE)
_DETAIL_WORKERS = 8  # 회의 상세를 동시에 받는 수
_DEFAULT_TYPES = {"weekly": "주간 회의", "monthly": "월간 회의", "normal": "일반 회의"}  # 못 불러왔을 때 화면 뼈대용


def _pages(path: str, token: str) -> list[dict]:
    """limit · offset 목록 API 를 끝까지 받아요."""
    rows: list[dict] = []
    while True:
        page = api.call("GET", f"{path}?limit={_PAGE}&offset={len(rows)}", token=token)
        rows += page
        if len(page) < _PAGE:
            return rows


def _md(dotted: str | None) -> str:
    """'2026.10.27' → '10/27' (목록의 날짜 · 회의 날짜가 비어 있으면 만든 날 기준)"""
    if not dotted:
        return ""
    _, month, day = dotted.split(".")
    return f"{int(month)}/{int(day)}"


def _detail(row: dict, token: str) -> dict | None:
    """회의 상세 (GET /api/meetings/{id}, 목업 meetings.json 한 개와 같은 모양). 그사이 지워졌거나 권한이 없어졌으면 None."""
    try:
        d = api.call("GET", f"/api/meetings/{row['id']}", token=token)
    except api.ApiError as e:
        if e.status in (403, 404):
            return None
        raise
    if not d["date"]:  # 회의 날짜(held_at)가 없는 회의 → 목록과 같은 날짜(만든 날)로
        d["date"] = _md(row.get("date_label"))
    return d


# cache_resource: 같은 객체를 그대로 돌려줌 (cache_data 처럼 부를 때마다 복사하지 않음 · Meeting 은 바뀌지 않는 객체라 공유해도 됨)
# token 이 캐시 키라서 사람마다 따로 저장돼요.
@st.cache_resource(ttl=_MEETINGS_TTL, show_spinner="회의록을 불러오는 중…")
def _api_meetings(token: str) -> dict[str, Meeting]:
    rows = _pages("/api/meetings", token)  # 내가 볼 수 있는 회의 (내 프로젝트 + 공유받은 회의), 최신순
    with ThreadPoolExecutor(_DETAIL_WORKERS) as pool:
        details = pool.map(lambda r: _detail(r, token), rows)
        return {d["id"]: _meeting(d) for d in details if d}


@st.cache_resource(ttl=_MEETINGS_TTL, show_spinner=False)
def _api_meeting_types(token: str) -> dict[str, MeetingType]:
    rows = api.call("GET", "/api/meeting-types", token=token)
    return {r["id"]: MeetingType(r["id"], r["name"], r["meta"] or "", r["hidden_count"]) for r in rows}


@st.cache_resource(ttl=_MEETINGS_TTL, show_spinner="프로젝트를 불러오는 중…")
def _api_folders(token: str) -> dict[str, Folder]:
    def folder(p: dict) -> Folder:
        pid = p["id"]
        members = api.call("GET", f"/api/projects/{pid}/members", token=token)  # 주관자가 맨 앞
        notes = [r["id"] for r in _pages(f"/api/projects/{pid}/meetings", token)]  # 최신순
        brief = api.call("GET", f"/api/projects/{pid}/briefs", token=token)
        return Folder(pid, p["name"], p["host_user_id"], tuple(members), tuple(notes),
                      tuple(BriefItem(b["label"], b["text"]) for b in brief))

    projects = api.call("GET", "/api/projects", token=token)  # 내가 멤버 · 주관인 프로젝트
    with ThreadPoolExecutor(_DETAIL_WORKERS) as pool:
        return {f.id: f for f in pool.map(folder, projects)}


def _is_admin() -> bool:
    """관리자 계정으로 로그인했는지 (_token 과 같은 이유로 세션 상태에서 바로 읽어요)."""
    return bool(getattr(st.session_state.get("_app_state"), "is_admin", False))


def _from_api(source: str, loader, default, *, user_only: bool = False):
    """로그인 전이면 조용히 default. 실패하면 화면 위에 안내(data_errors)를 남기고 default.

    user_only=True: 일반 사용자 전용 API (회의 · 회의 종류 · 프로젝트). 백엔드가 관리자를 403 으로 막으므로
    관리자면 부르지 않고 조용히 default (화면을 다시 그릴 때마다 403 이 쌓이고 안내가 뜨는 것 방지).
    """
    token = _token()
    if not token:
        return default
    errors = st.session_state.setdefault("_data_errors", {})
    if user_only and _is_admin():
        errors.pop(source, None)
        return default
    try:
        out = loader(token)
    except api.ApiError as e:
        errors[source] = str(e)
        return default
    errors.pop(source, None)
    return out


def data_errors() -> dict[str, str]:
    """백엔드에서 못 불러온 데이터 {무엇: 이유} (화면 위 안내용)."""
    return dict(st.session_state.get("_data_errors", {}))


def meetings_changed() -> None:
    """회의 · 프로젝트 캐시 비우기 (다음 화면에서 백엔드에서 다시 받음)."""
    _api_meetings.clear()
    _api_meeting_types.clear()
    _api_folders.clear()


def _base_meetings() -> dict[str, Meeting]:
    return _from_api("회의록", _api_meetings, {}, user_only=True)


def _session_meetings() -> dict[str, Meeting]:
    """이 화면(세션)에서 「회의록 생성 완료」로 새로 만든 회의 (AppState.new_meetings). 목업이라 새로고침하면 사라짐."""
    try:
        import streamlit as st

        state = st.session_state.get("_app_state")
    except Exception:  # noqa: BLE001 - Streamlit 밖(테스트 등)에서 부를 때
        return {}
    out = dict(getattr(state, "new_meetings", {}) or {})
    preview = getattr(state, "preview_meeting", None)  # 새 회의록 3단계 미리보기 (폴더에는 넣지 않음)
    if preview is not None:
        out[preview.id] = preview
    return out


def _added_todos() -> dict[str, list[Todo]]:
    """주관자가 직접 추가한 액션 아이템 (AppState.added_todos)."""
    try:
        state = st.session_state.get("_app_state")
    except Exception:  # noqa: BLE001 - Streamlit 밖(테스트 등)에서 부를 때
        return {}
    return getattr(state, "added_todos", {}) or {}


def _edited_todos() -> dict[str, dict[int, dict]]:
    """주관자가 수정한 액션 아이템 (AppState.edited_todos)."""
    try:
        state = st.session_state.get("_app_state")
    except Exception:  # noqa: BLE001 - Streamlit 밖(테스트 등)에서 부를 때
        return {}
    return getattr(state, "edited_todos", {}) or {}


def _deleted_todos() -> dict[str, list[int]]:
    """삭제한 액션 아이템 번호 (AppState.deleted_todos)."""
    try:
        state = st.session_state.get("_app_state")
    except Exception:  # noqa: BLE001 - Streamlit 밖(테스트 등)에서 부를 때
        return {}
    return getattr(state, "deleted_todos", {}) or {}


def _with_added_todo(agenda: Agenda, t: Todo) -> Agenda:
    """안건에 직접 추가한 할 일을 넣음 (상세가 있으면 마지막 소주제 아래, 없으면 요점으로)."""
    src = tuple(dict.fromkeys([*agenda.src, *t.src]))
    if not agenda.detailed:
        return replace(agenda, src=src, points=(*agenda.points, Evidence(t.text, t.src)))
    point = TaggedPoint("액션", t.text, t.src)
    last = agenda.topics[-1] if agenda.topics else AgendaTopic(MISC_AGENDA, ())
    return replace(agenda, src=src, topics=(*agenda.topics[:-1], replace(last, points=(*last.points, point))))


def _attach_added_todos(m: Meeting, first: int, gone: list[int]) -> Meeting:
    """직접 추가한 할 일을 고른 안건에 표시. 기타를 골랐는데 기타 안건이 없으면 맨 뒤에 새로 만들어요."""
    agendas = list(m.agendas)
    for i, t in enumerate(m.todos):
        if i < first or i in gone or not t.agenda:
            continue
        at = next((k for k, a in enumerate(agendas) if a.title == t.agenda), None)
        if at is None:
            agendas.append(Agenda(t.agenda, "논의됨", "info", False, (), (), detailed=True))
            at = len(agendas) - 1
        agendas[at] = _with_added_todo(agendas[at], t)
    return replace(m, agendas=tuple(agendas))


def _fresh_todo(t) -> Todo:
    """세션에 남아 있던 예전 Todo(담당자 한 명 · owner)를 지금 모양(owners)으로. 앱을 띄운 채 코드를 바꾼 경우 대비."""
    if hasattr(t, "owners"):
        return t
    owner = getattr(t, "owner", None)
    return Todo(t.text, (owner,) if owner else (), t.due, tuple(t.src), getattr(t, "urgent", False))


def meetings() -> dict[str, Meeting]:
    """목업 회의 + 새로 만든 회의 (+ 주관자가 직접 추가한 액션 아이템)."""
    out = dict(_base_meetings())
    for mid, m in _session_meetings().items():
        out[mid] = replace(m, todos=tuple(_fresh_todo(t) for t in m.todos))
    first_added = {}
    for mid, todos in _added_todos().items():
        if mid in out and todos:
            first_added[mid] = len(out[mid].todos)
            out[mid] = replace(out[mid], todos=out[mid].todos + tuple(_fresh_todo(t) for t in todos))
    for mid, edits in _edited_todos().items():
        if mid in out and edits:
            out[mid] = replace(out[mid], todos=tuple(replace(t, urgent=False, **edits[i]) if i in edits else t
                                                     for i, t in enumerate(out[mid].todos)))
    gone = _deleted_todos()
    for mid, first in first_added.items():
        out[mid] = _attach_added_todos(out[mid], first, gone.get(mid, []))
    return out


def meeting(mid: str) -> Meeting:
    return meetings()[mid]


def deleted_todo_indexes(mid: str) -> list[int]:
    """DB 에서 삭제한 할 일 번호 (화면 「삭제한 액션 아이템」 · 이 세션에서 지운 번호와 함께 써요)."""
    return [i for i, t in enumerate(meeting(mid).todos) if t.deleted]


def received_meeting_ids() -> list[str]:
    """공유 받은 회의 (최근 받은 순)."""
    shared = [m for m in meetings().values() if m.shared]
    return [m.id for m in sorted(shared, key=lambda m: m.shared.received, reverse=True)]


# ---------------------------------------------------------------- folders
def meeting_types() -> dict[str, MeetingType]:
    """회의 종류 (GET /api/meeting-types). 못 불러와도 월간 · 주간 · 일반 칸은 있어야 화면(주소 · 카드)이 그려져요."""
    out = {k: MeetingType(k, name, "", 0) for k, name in _DEFAULT_TYPES.items()}
    out.update(_from_api("회의 종류", _api_meeting_types, {}, user_only=True))
    return out


def base_folders() -> dict[str, Folder]:
    """내 프로젝트 (GET /api/projects + 멤버 · 회의 · 브리프)."""
    return _from_api("프로젝트", _api_folders, {}, user_only=True)


# ---------------------------------------------------------------- assignee candidates
@lru_cache(maxsize=1)
def assignee_config() -> dict:
    raw = _load("candidates")
    return {
        "candidates": [
            Candidate(**{**c, "skills": tuple(c["skills"])}) for c in raw["candidates"]
        ],
        "needed_skills": set(raw["needed_skills"]),
        "related_teams": set(raw["related_teams"]),
        "reasons": raw["reasons"],
        "predecessors": [Predecessor(**p) for p in raw.get("predecessors", [])],
    }


# ---------------------------------------------------------------- wiki graphs
def _graph(raw: dict) -> KnowledgeGraph:
    nodes = {
        k: GraphNode(k, v["label"], v["kind"], v["x"], v["y"], v["desc"], v.get("initial", ""), v.get("person"))
        for k, v in raw["nodes"].items()
    }
    edges = tuple(GraphEdge(e["a"], e["b"], e["label"], e["weight"]) for e in raw["edges"])
    timeline = {
        k: tuple(TimelineItem(t["when"], t["text"], t["meeting"], tuple(t["src"])) for t in v)
        for k, v in raw["timeline"].items()
    }
    extra = {k: v for k, v in raw.items() if k not in ("nodes", "edges", "timeline")}
    if "branch_timeline" in extra:
        extra["branch_timeline"] = {
            k: tuple(TimelineItem(t["when"], t["text"], t["meeting"], tuple(t["src"])) for t in v)
            for k, v in extra["branch_timeline"].items()
        }
    return KnowledgeGraph(nodes, edges, timeline, extra)


@lru_cache(maxsize=1)
def folder_wiki() -> KnowledgeGraph:
    return _graph(_load("wiki_folder"))


# ---------------------------------------------------------------- 회의에 물어보기
def ask(question: str) -> dict:
    """회의 기록에 묻고 근거가 달린 답을 받음 (POST /api/ask · 온보딩 에이전트). 형식은 ask 결과 JSON 그대로.

    실패하면(백엔드 꺼짐 · 시간 초과 · 로그인 만료) answer_status="error" 와 이유를 담아 돌려줘요.
    """
    try:
        return api.call("POST", "/api/ask", {"question": question}, _token(), timeout=ASK_TIMEOUT)
    except api.ApiError as e:
        return {"question": question, "answer_status": "error", "summary": str(e)}


# ---------------------------------------------------------------- 외부 도구 전송 · Jira
def export_jira(meeting_title: str, items: list[dict]) -> dict:
    """액션 아이템 → Jira 이슈 (POST /api/exports/jira). items: [{key, title, owners, due}]
    → {project_key, created, total, items: [{key, ok, issue_key | error, assigned}]} · 연결 · 설정 실패는 ApiError."""
    return api.call("POST", "/api/exports/jira", {"meeting_title": meeting_title, "items": items}, _token(),
                    timeout=EXPORT_TIMEOUT)


def export_mail(meeting_title: str, body: str, recipients: list[str]) -> dict:
    """회의록 글 → 참석자마다 메일 (POST /api/exports/mail). recipients: [user_id]
    → {items: [{key, ok, sent | error}]} · 연결 · 설정 실패는 ApiError."""
    return api.call("POST", "/api/exports/mail", {"meeting_title": meeting_title, "body": body, "recipients": recipients},
                    _token(), timeout=EXPORT_TIMEOUT)


# ---------------------------------------------------------------- 홈 · 대시보드
def dashboard_jira(refresh: bool = False) -> dict:
    """내 Jira 할 일 (GET /api/dashboard/jira · 로그인한 사람이 담당자인 미완료 이슈, 서버가 1분 캐시).
    refresh: 「동기화」 (캐시를 건너뜀).
    → {linked, issues: [{key, title, status, category, due, url}], all_url, synced_at} · 연결 · 설정 실패는 ApiError."""
    return api.call("GET", "/api/dashboard/jira" + ("?refresh=true" if refresh else ""), token=_token(), timeout=30)


# ---------------------------------------------------------------- 회의록 쓰기 (PostgreSQL · 백엔드 API)
# 실패하면 api.ApiError (메시지는 화면에 그대로 보여 줘도 돼요). 쓴 뒤에는 meetings_changed() 로 캐시를 비워 바로 보이게 해요.
def _write(method: str, path: str, body: dict | None = None):
    res = api.call(method, path, body, _token())
    meetings_changed()
    return res


def is_saved(mid: str) -> bool:
    """백엔드(DB)에 있는 회의인지 (작성 중 미리보기면 False → 화면(세션)에서만 고침)."""
    return mid in _base_meetings()


def project_exists(project_id: str) -> bool:
    try:
        api.call("GET", f"/api/projects/{project_id}", token=_token())
    except api.ApiError as e:
        if e.status in (403, 404):
            return False
        raise
    return True


def create_project(name: str, members: list[str], project_id: str | None = None) -> dict:
    """프로젝트 만들기 (POST /api/projects). project_id 를 주면 그 id 로 (AI 요약의 project_id · Neo4j 와 같게)."""
    return _write("POST", "/api/projects", {"name": name, "members": members, "id": project_id})


def save_record(payload: dict) -> dict:
    """미리보기 회의록 저장 (POST /api/drafts). payload 는 state/record_payload.draft_payload()."""
    return _write("POST", "/api/drafts", payload)


def submit_record(mid: str, reviewer_id: str | None = None) -> dict:
    """회의록 생성 (draft → review) · 참석자 알림 · 주관자가 다른 사람이면 주관자에게 검토 요청."""
    return _write("POST", f"/api/meetings/{mid}/submit", {"reviewer_id": reviewer_id})


def confirm_record(mid: str) -> dict:
    """확정 (review → confirmed) · 검토자, 없으면 주관자만."""
    return _write("POST", f"/api/meetings/{mid}/confirm")


def add_record_todo(mid: str, body: dict) -> dict:
    return _write("POST", f"/api/meetings/{mid}/todos", body)


def update_record_todo(todo_id: int, body: dict) -> dict:
    return _write("PATCH", f"/api/todos/{todo_id}", body)


def delete_record_todo(todo_id: int) -> None:
    _write("DELETE", f"/api/todos/{todo_id}")


def restore_record_todo(todo_id: int) -> None:
    _write("POST", f"/api/todos/{todo_id}/restore")


def request_record_branch(mid: str) -> None:
    _write("POST", f"/api/meetings/{mid}/branch-requests")


def save_record_summary(mid: str, content: dict) -> dict:
    """회의 상세 「편집」 결과를 요약 새 버전으로 저장 (PUT /api/meetings/{id}/summary · 이전 버전은 남음)."""
    return _write("PUT", f"/api/meetings/{mid}/summary", {"content": content})


def delete_record(mid: str) -> None:
    _write("DELETE", f"/api/meetings/{mid}")


def set_record_status(mid: str, status: str) -> None:
    """결과 전달 상태 (sending | done | fail). 실패해도 화면은 그대로 이어가요 (서버 기록만 늦음)."""
    _write("PUT", f"/api/meetings/{mid}/status", {"status": status})


def finish_record_delivery(mid: str) -> str:
    """외부 도구 전송이 끝남 → 남은 실패가 있으면 fail, 없으면 done (서버가 정함)."""
    return _write("POST", f"/api/meetings/{mid}/delivery/finish")["status"]


# ---------------------------------------------------------------- 프로젝트 멤버 · 삭제 (PostgreSQL)
def add_project_member(fid: str, user_id: str) -> None:
    _write("POST", f"/api/projects/{fid}/members", {"user_id": user_id})


def remove_project_member(fid: str, user_id: str) -> None:
    _write("DELETE", f"/api/projects/{fid}/members/{user_id}")


def delete_project(fid: str) -> None:
    _write("DELETE", f"/api/projects/{fid}")


# ---------------------------------------------------------------- 공유 (PostgreSQL)
def share_record(mid: str, user_id: str, scope: str) -> dict:
    """회의 공유 (POST /api/meetings/{id}/shares) · 받는 사람에게 알림은 서버가 보내요."""
    res = api.call("POST", f"/api/meetings/{mid}/shares", {"recipient_user_id": user_id, "scope": scope}, _token())
    _sent.clear()
    return res


def revoke_record_share(share_id: int) -> None:
    api.call("DELETE", f"/api/shares/{share_id}", None, _token())
    _sent.clear()


@st.cache_data(ttl=30, show_spinner=False)
def _sent(token: str) -> list[dict]:
    return api.call("GET", "/api/shares/sent", token=token)


def sent_share_rows() -> list[dict]:
    """[공유한 회의] 내가 보낸 공유 (GET /api/shares/sent · 최근 순). 못 불러오면(로그인 전 · 관리자) 빈 목록."""
    if not _token():
        return []
    try:
        return _sent(_token())
    except api.ApiError:
        return []


# ---------------------------------------------------------------- 알림 (PostgreSQL)
_NOTI_TTL = 3  # 초 · 화면이 3초마다 새 알림을 확인해요 (components/live.py)


@st.cache_data(ttl=_NOTI_TTL, show_spinner=False)
def _api_notifications(token: str) -> list[dict]:
    return api.call("GET", "/api/notifications", token=token)


def _noti_when(ts: str | None) -> str:
    """'2026-11-07T10:42:00+09:00' → '11/7 10:42'"""
    if not ts:
        return ""
    day, _, clock = ts.partition("T")
    _, mo, d = day.split("-")
    return f"{int(mo)}/{int(d)} {clock[:5]}"


def _server_notifications() -> list[Notification]:
    rows = _from_api("알림", _api_notifications, [])
    return [Notification(r["id"], r["kind"], r["text"], _noti_when(r.get("created_at")), r.get("meeting_id"),
                         not r.get("is_read"), r.get("folder_id")) for r in rows]


def mark_notification_read(nid: str) -> None:
    """알림을 눌러 읽음 (PATCH /api/notifications/{id}). 이 화면에서만 생긴 알림(live-)은 서버에 없어요."""
    if nid.startswith("live-") or not _token():
        return
    try:
        api.call("PATCH", f"/api/notifications/{nid}", {"read": True}, _token())
    except api.ApiError:
        return  # 읽음 표시는 화면(read_notifications)에도 남아서 다음에 다시 보내면 돼요
    _api_notifications.clear()


# ---------------------------------------------------------------- 외부 연동 (PostgreSQL)
@st.cache_data(ttl=10, show_spinner=False)
def _api_integrations(token: str) -> dict:
    org = api.call("GET", "/api/integrations/organization", token=token)["name"]
    return {"organization": org, "tools": api.call("GET", "/api/integrations/tools", token=token)}


def _last_sync(ts: str | None) -> str:
    return _noti_when(ts) if ts else ""


@st.cache_data(ttl=10, show_spinner=False)
def _api_failures(token: str, mid: str | None) -> list[dict]:
    return api.call("GET", "/api/integrations/failures" + (f"?meeting_id={mid}" if mid else ""), token=token)


def _failure(r: dict) -> ExportFailure:
    return ExportFailure(r["id"], r["tool_id"], r["meeting_id"], r["kind"], r["reason"], r.get("code") or "",
                         _noti_when(r.get("failed_at")), r.get("meeting_title") or "")


def _failures_changed() -> None:
    _api_failures.clear()
    _api_integrations.clear()
    _api_notifications.clear()
    meetings_changed()  # 실패 해결 → 회의 상태(done)도 다시 받음


def record_failure(mid: str, tool_id: str, kind: str, reason: str, code: str = "") -> ExportFailure:
    """보내기 실패 기록 (POST /api/integrations/failures) · tech 면 서버가 관리자에게 알림."""
    r = api.call("POST", "/api/integrations/failures",
                 {"meeting_id": mid, "tool_id": tool_id, "kind": kind, "reason": reason, "code": code}, _token())
    _failures_changed()
    return _failure(r)


def record_export(mid: str, tool_id: str) -> None:
    """보내기 성공 기록 (도구의 마지막 전송 시각)."""
    api.call("POST", "/api/integrations/exports", {"meeting_id": mid, "tool_id": tool_id}, _token())
    _api_integrations.clear()


# ---------------------------------------------------------------- integrations
def integrations() -> dict:
    """외부 도구 (GET /api/integrations/organization · /tools). 못 불러오면 목업 (화면 뼈대용)."""
    data = _from_api("외부 연동", _api_integrations, None)
    if data is None:
        raw = _load("integrations")
        return {"organization": raw["organization"], "tools": [IntegrationTool(**t) for t in raw["tools"]],
                "initial_status": raw["initial_status"], "failures": []}
    tools = [IntegrationTool(t["id"], t["mark"], t["name"], t["description"], t["target"], t["is_phase2"],
                             _last_sync(t.get("last_sync_at"))) for t in data["tools"]]
    return {"organization": data["organization"], "tools": tools,
            "initial_status": {t["id"]: t["status"] for t in data["tools"]}, "failures": []}


# ---------------------------------------------------------------- 서버 공용 저장소 (데모)
# 사용자 · 관리자 화면이 함께 보는 값. 실제 서비스에서는 서버 DB 에 있는 값이에요.
# 데모 서버(streamlit)를 다시 켜면 처음 상태로 돌아가요.
@st.cache_resource
def _server() -> dict:
    return {
        "notis": {"admin": [], "user": []},  # 이 앱에서만 생기는 알림 (예산 알림 등 · 나머지 알림은 서버에서)
        # 조직 (관리자 · 구성원 관리): 새로 추가한 구성원 · 팀/직무 변경 · 권한 · 상태
        "org": {"added": {}, "edits": {}, "roles": {}, "status": {}},
        "org_version": 0,  # 조직이 바뀔 때마다 +1 → 열려 있는 사용자 화면이 알아채고 새로 그림
        "budget": {"amount": admin()["budget"], "alert_at": admin()["alert_at"]},  # 월 예산 · 알림 기준 (%)
        "budget_alerted": set(),  # 이미 보낸 예산 알림 (같은 알림을 또 보내지 않게)
    }


def org() -> dict:
    """구성원 관리 값 (added · edits · roles · status). 바꾼 뒤에는 org_changed() 를 불러 주세요."""
    return _server()["org"]


def org_changed() -> None:
    _server()["org_version"] += 1


def org_version() -> int:
    return _server()["org_version"]


def org_tree() -> list[dict]:
    """조직도 (이름 · children). Neo4j 팀은 계층이 없어 팀만 한 줄로 나열해요."""
    return [{"name": t["name"]} for t in catalog("teams")]


def team_id_of(name: str) -> str | None:
    return next((t["team_id"] for t in catalog("teams") if t["name"] == name), None)


def job_catalog() -> list[str]:
    """직무 목록 (Neo4j Role)."""
    return [r["name"] for r in catalog("roles")]


def budget() -> dict:
    return _server()["budget"]


def budget_alert_once(key: str) -> bool:
    """이 예산 알림을 처음 보내는 거면 True (보낸 것으로 기록)."""
    sent = _server()["budget_alerted"]
    if key in sent:
        return False
    sent.add(key)
    return True


def tool_status(tool_id: str) -> str:
    return tool_statuses().get(tool_id, "off")


def tool_statuses() -> dict[str, str]:
    return dict(integrations()["initial_status"])


def set_tool_status(tool_id: str, status: str) -> None:
    """관리자: 도구 연결 상태 (PUT /api/integrations/tools/{id}/status). 실패하면 ApiError."""
    api.call("PUT", f"/api/integrations/tools/{tool_id}/status", {"status": status}, _token())
    _api_integrations.clear()


def _failure_rows(mid: str | None) -> list[dict]:
    """관리자는 전체, 사용자는 회의 단위로만 볼 수 있어요 (그 밖이면 빈 목록)."""
    token = _token()
    if not token:
        return []
    try:
        return _api_failures(token, mid)
    except api.ApiError:
        return []


def _remember(rows: list[dict]) -> list[dict]:
    """한 번 본 실패 기록은 id 로 다시 찾을 수 있게 (다시 보내기 · 관리자 문의 창은 id 만 알아요)."""
    seen = st.session_state.setdefault("_failure_rows", {})
    for r in rows:
        seen[r["id"]] = r
    return rows


def export_failures(mid: str | None = None) -> list[ExportFailure]:
    """전송 실패 기록 (GET /api/integrations/failures · mid 를 주면 그 회의 것만). 새로 생긴 기록이 앞.
    mid 없이 부르면 관리자는 전체, 사용자는 이 화면에서 본 적 있는 기록."""
    rows = _remember(_failure_rows(mid))
    if mid is None and not rows:
        rows = list(st.session_state.get("_failure_rows", {}).values())
    return [_failure(r) for r in rows]


def resolve_failure(fid: str) -> None:
    """다시 보내서 해결 (POST .../resolve) · 남은 실패가 없으면 서버가 회의를 done 으로 · 주관자에게 알림."""
    row = api.call("POST", f"/api/integrations/failures/{fid}/resolve", None, _token())
    _remember([row])  # 해결 시각이 바로 보이게 (is_resolved)
    _failures_changed()


def is_resolved(fid: str) -> bool:
    r = st.session_state.get("_failure_rows", {}).get(fid)
    return bool(r and r.get("resolved_at"))


def push_notification(audience: str, kind: str, text: str, when: str, meeting: str | None = None) -> None:
    """새 알림 (audience: admin | user)."""
    notis = _server()["notis"][audience]
    notis.insert(0, Notification(f"live-{audience}-{len(notis) + 1}", kind, text, when, meeting, True))


def live_notifications(audience: str) -> list[Notification]:
    """새 알림 확인용 (components/live.py 가 몇 초마다 개수를 봄): 서버 알림 + 이 앱 알림."""
    return notifications(audience)


def tool(tool_id: str) -> IntegrationTool:
    return next(t for t in integrations()["tools"] if t.id == tool_id)


# ---------------------------------------------------------------- 관리자 (비용 · 구성원 · MCP)
@lru_cache(maxsize=1)
def admin() -> dict:
    """관리자 화면 목업: 관리자 계정 · 월 예산 · 월별 비용 · 팀별/기능별 비중 · 구성원 이메일·최근 접속."""
    return _load("admin")


# ---------------------------------------------------------------- notifications
def notifications(audience: str = "user") -> list[Notification]:
    """알림 목록 (GET /api/notifications · 최근 순) + 이 앱에서만 생긴 알림(예산 알림 등)."""
    return list(_server()["notis"][audience]) + _server_notifications()



# ---------------------------------------------------------------- 팀 (공유 대상 · 조직도)
def teams() -> dict[str, Team]:
    """팀 id → 팀 (구성원 · 직무). Neo4j 팀 순서 그대로."""
    members: dict[str, list[TeamMember]] = {t["team_id"]: [] for t in catalog("teams")}
    for r in people_rows():
        if r["team_id"]:
            members.setdefault(r["team_id"], []).append(TeamMember(r["user_id"], r["name"], r["job"] or ""))
    names = {t["team_id"]: t["name"] for t in catalog("teams")}
    return {tid: Team(tid, names.get(tid, tid), tuple(mbs)) for tid, mbs in members.items()}


def team_candidates() -> dict[str, list[Candidate]]:
    """팀 id → 그 팀 구성원의 담당자 점수 (조직도 + 후보 점수를 이름으로 연결)."""
    by_name = {c.name: c for c in assignee_config()["candidates"]}

    def cand(mb: TeamMember, team: str) -> Candidate:
        c = by_name.get(mb.name)
        if c:  # 관리자가 바꾼 팀 · 직무 반영
            return replace(c, team=team, role=mb.role)
        # 점수 기록이 없는 사람 (새로 추가한 구성원 등): 진행 중 업무 0건 · 기본 점수
        return Candidate(f"c-{mb.id}", mb.name, team, mb.role, 40, 0, 50, 100, 0, False, ())

    return {tid: [cand(mb, t.name) for mb in t.members] for tid, t in teams().items()}
