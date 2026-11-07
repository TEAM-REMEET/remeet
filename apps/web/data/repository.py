"""데이터 저장소.

구성원 · 팀 · 직무는 백엔드 API(Neo4j)에서, 나머지는 아직 data/mock/*.json 에서 읽어요.
실제 API 로 바꿀 때는 이 파일의 함수 시그니처만 유지하고 내부 구현을 교체하면 됩니다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import streamlit as st

from config import ASK_TIMEOUT, JIRA_EXPORT_TIMEOUT, MEETING_SUMMARY_TIMEOUT
from data import api

from data.models import (
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


# shortcut: 60초 캐시, 이 앱에서 바꾸면 바로 비우지만 다른 곳(DB 직접 수정)의 변경은 최대 60초 늦게 보임
@st.cache_data(ttl=60, show_spinner=False)
def _people_rows(token: str) -> list[dict]:
    return api.call("GET", "/api/people", token=token)


def people_rows() -> list[dict]:
    """구성원 전체 (GET /api/people): user_id · name · email · permission · position · is_active · team · job · skills."""
    return _people_rows(_token())


@st.cache_data(ttl=60, show_spinner=False)
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


def summarize_meeting(body: dict) -> dict:
    """회의 등록 입력 → 요약 · 액션 아이템 (POST /api/meetings, 응답은 MeetingSummary JSON). 실패하면 api.ApiError."""
    return api.call("POST", "/api/meetings", body, _token(), timeout=MEETING_SUMMARY_TIMEOUT)


def recommend_assignees(summary: dict) -> dict[str, dict]:
    """검토가 끝난 MeetingSummary → Neo4j 적재 · 담당자 추천 (POST /api/meetings/{id}/recommendations).
    돌려줌: {item_id: 추천 결과}. 몇십 초 ~ 몇 분 걸림. 실패하면 api.ApiError (같은 요청을 다시 보내도 됨)."""
    res = api.call("POST", f"/api/meetings/{summary['meeting_id']}/recommendations", summary, _token(),
                   timeout=MEETING_SUMMARY_TIMEOUT)
    return {item["item_id"]: item for item in res["items"]}


def set_assignees(item_id: str, user_ids: list[str]) -> list[dict]:
    """주관자가 고른 담당자를 Neo4j에 확정 (PUT /api/action-items/{id}/assignee). 실패하면 api.ApiError."""
    return api.call("PUT", f"/api/action-items/{item_id}/assignee", {"user_ids": user_ids}, _token())


def recommendation_config(item: dict) -> dict:
    """추천 결과 한 건 → 담당자 지정 창이 쓰는 모양 (assignee_config()와 같은 키)."""
    def candidate(c: dict) -> Candidate:
        match = " · ".join(x for x in (", ".join(c["skills"]), f"{c['team']} 소속" if c["team"] else "",
                                       f"진행 중 업무 {c['open_tasks']}건") if x)
        return Candidate(c["user_id"], c["name"], c["team"] or "", c["role"] or "", round(c["score"]),
                         round(c["skill_score"]), round(c["role_team_score"] or 0), round(c["workload_score"]),
                         c["open_tasks"], c["attended"], tuple(c["skills"]), match)

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


def _meeting(d: dict) -> Meeting:
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
        todos=tuple(Todo(t["text"], _owners(t), t["due"], tuple(t["src"]), t["urgent"]) for t in d["todos"]),
        shared=ShareInfo(shared["from"], shared["scope"], shared["received"], shared["lock_raw"]) if shared else None,
        branch_of=(d.get("branch_of") or {}).get("meeting"),
    )


@lru_cache(maxsize=1)
def _base_meetings() -> dict[str, Meeting]:
    return {d["id"]: _meeting(d) for d in _load("meetings")}


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
    for mid, todos in _added_todos().items():
        if mid in out and todos:
            out[mid] = replace(out[mid], todos=out[mid].todos + tuple(_fresh_todo(t) for t in todos))
    return out


def meeting(mid: str) -> Meeting:
    return meetings()[mid]


def received_meeting_ids() -> list[str]:
    """공유 받은 회의 (최근 받은 순)."""
    shared = [m for m in meetings().values() if m.shared]
    return [m.id for m in sorted(shared, key=lambda m: m.shared.received, reverse=True)]


# ---------------------------------------------------------------- folders
@lru_cache(maxsize=1)
def meeting_types() -> dict[str, MeetingType]:
    raw = _load("folders")["types"]
    return {k: MeetingType(k, v["name"], v["meta"], v["hidden_count"]) for k, v in raw.items()}


@lru_cache(maxsize=1)
def base_folders() -> dict[str, Folder]:
    raw = _load("folders")["folders"]
    return {
        k: Folder(
            id=k,
            name=v["name"],
            host=v["host"],
            members=tuple(v["members"]),
            notes=tuple(v["notes"]),
            brief=tuple(BriefItem(b["label"], b["text"]) for b in v.get("brief", [])),
        )
        for k, v in raw.items()
    }


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
                    timeout=JIRA_EXPORT_TIMEOUT)


# ---------------------------------------------------------------- integrations
@lru_cache(maxsize=1)
def integrations() -> dict:
    raw = _load("integrations")
    return {
        "organization": raw["organization"],
        "tools": [IntegrationTool(**t) for t in raw["tools"]],
        "initial_status": raw["initial_status"],
        # 외부 도구로 보내다 실패한 기록 (단순 이슈 · 기술 이슈)
        "failures": [ExportFailure(**f) for f in raw.get("failures", [])],
    }


# ---------------------------------------------------------------- 서버 공용 저장소 (데모)
# 사용자 · 관리자 화면이 함께 보는 값. 실제 서비스에서는 서버 DB 에 있는 값이에요.
# 데모 서버(streamlit)를 다시 켜면 처음 상태로 돌아가요.
@st.cache_resource
def _server() -> dict:
    return {
        "tools": dict(integrations()["initial_status"]),  # 조직의 외부 도구 연결 상태 (관리자가 바꿈)
        "failures": {},  # 결과 전달 중에 새로 생긴 전송 실패
        "resolved": set(),  # 관리자가 다시 보내 해결한 실패 id
        "notis": {"admin": [], "user": []},  # 새로 생긴 알림 (관리자 · 사용자)
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
    return _server()["tools"].get(tool_id, "off")


def tool_statuses() -> dict[str, str]:
    return dict(_server()["tools"])


def set_tool_status(tool_id: str, status: str) -> None:
    _server()["tools"][tool_id] = status


def add_export_failure(f: ExportFailure) -> None:
    _server()["failures"][f.id] = f
    _server()["resolved"].discard(f.id)


def resolve_failure(fid: str) -> None:
    _server()["resolved"].add(fid)


def is_resolved(fid: str) -> bool:
    return fid in _server()["resolved"]


def export_failures(mid: str | None = None) -> list[ExportFailure]:
    """전송 실패 기록 (mid 를 주면 그 회의 것만). 새로 생긴 기록이 앞."""
    items = list(reversed(_server()["failures"].values())) + integrations()["failures"]
    return [f for f in items if mid is None or f.meeting == mid]


def push_notification(audience: str, kind: str, text: str, when: str, meeting: str | None = None) -> None:
    """새 알림 (audience: admin | user)."""
    notis = _server()["notis"][audience]
    notis.insert(0, Notification(f"live-{audience}-{len(notis) + 1}", kind, text, when, meeting, True))


def live_notifications(audience: str) -> list[Notification]:
    return list(_server()["notis"][audience])


def tool(tool_id: str) -> IntegrationTool:
    return next(t for t in integrations()["tools"] if t.id == tool_id)


# ---------------------------------------------------------------- 관리자 (비용 · 구성원 · MCP)
@lru_cache(maxsize=1)
def admin() -> dict:
    """관리자 화면 목업: 관리자 계정 · 월 예산 · 월별 비용 · 팀별/기능별 비중 · 구성원 이메일·최근 접속."""
    return _load("admin")


# ---------------------------------------------------------------- notifications
@lru_cache(maxsize=1)
def _mock_notifications() -> list[Notification]:
    return [Notification(**n) for n in _load("notifications")]


def notifications(audience: str = "user") -> list[Notification]:
    """알림 목록: 새로 생긴 알림이 앞. 관리자는 새로 생긴 알림만."""
    live = live_notifications(audience)
    return live if audience == "admin" else live + _mock_notifications()



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
