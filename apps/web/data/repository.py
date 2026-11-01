"""목업 데이터 저장소.

data/mock/*.json 을 한 번만 읽어서 모델 객체로 돌려줍니다. 실제 API 로 바꿀 때는
이 파일의 함수 시그니처만 유지하고 내부 구현을 교체하면 됩니다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from data.models import (
    Agenda,
    AiToggle,
    BriefItem,
    Candidate,
    Discussion,
    Evidence,
    Folder,
    GraphEdge,
    GraphNode,
    IntegrationTool,
    KnowledgeGraph,
    Meeting,
    MeetingType,
    Notification,
    Paragraph,
    Person,
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
@lru_cache(maxsize=1)
def people() -> dict[str, Person]:
    return {k: Person(k, v["short"], v["name"], v["team"]) for k, v in _load("people").items()}


@lru_cache(maxsize=1)
def people_by_short_name() -> dict[str, str]:
    """원문 화자 이름(서윤) → 사람 id(yun)."""
    return {p.short: p.id for p in people().values()}


# ---------------------------------------------------------------- meetings
def _meeting(d: dict) -> Meeting:
    shared = d.get("shared")
    return Meeting(
        id=d["id"],
        title=d["title"],
        when=d["when"],
        date=d["date"],
        topic=d.get("topic"),
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
        agendas=tuple(
            Agenda(a["title"], a["status"], a["tone"], a["deep"], tuple(a["src"]), _evidence(a["points"]))
            for a in d["agendas"]
        ),
        todos=tuple(Todo(t["text"], t["owner"], t["due"], tuple(t["src"]), t["urgent"]) for t in d["todos"]),
        shared=ShareInfo(shared["from"], shared["scope"], shared["received"], shared["lock_raw"]) if shared else None,
        branch_of=(d.get("branch_of") or {}).get("meeting"),
    )


@lru_cache(maxsize=1)
def meetings() -> dict[str, Meeting]:
    return {d["id"]: _meeting(d) for d in _load("meetings")}


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
            type=v["type"],
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
def personal_wiki() -> KnowledgeGraph:
    return _graph(_load("wiki_personal"))


@lru_cache(maxsize=1)
def folder_wiki() -> KnowledgeGraph:
    return _graph(_load("wiki_folder"))


# ---------------------------------------------------------------- integrations
@lru_cache(maxsize=1)
def integrations() -> dict:
    raw = _load("integrations")
    return {
        "organization": raw["organization"],
        "tools": [IntegrationTool(**t) for t in raw["tools"]],
        "initial_status": raw["initial_status"],
        "ai_toggles": [AiToggle(**t) for t in raw["ai_toggles"]],
        "ai_default_folders": raw["ai_default_folders"],
    }


# ---------------------------------------------------------------- notifications
@lru_cache(maxsize=1)
def notifications() -> list[Notification]:
    return [Notification(**n) for n in _load("notifications")]



# ---------------------------------------------------------------- directory (공유 대상 팀·구성원)
@lru_cache(maxsize=1)
def teams() -> dict[str, Team]:
    return {
        t["id"]: Team(t["id"], t["name"], tuple(TeamMember(**mb) for mb in t["members"]))
        for t in _load("directory")
    }



def team_candidates() -> dict[str, list[Candidate]]:
    """팀 id → 그 팀 구성원의 담당자 점수 (조직도 + 후보 점수를 이름으로 연결)."""
    by_name = {c.name: c for c in assignee_config()["candidates"]}
    return {tid: [by_name[mb.name] for mb in t.members if mb.name in by_name] for tid, t in teams().items()}
