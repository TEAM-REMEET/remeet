"""폴더 그래프 탭: Neo4j 노드 · 엣지 → 화면용 (노드 종류별로 보여 줄 속성만, 사람이 읽기 좋은 이름 · 값으로).

여기에 없는 속성은 내보내지 않는다 (Person.password_hash · email, 추천 점수 같은 내부 값).
"""

from __future__ import annotations

from typing import Any

from followup.application.folder_wiki import discussion_lines

KIND = {"Project": "프로젝트", "Meeting": "회의", "Agenda": "안건", "Topic": "주제", "Discussion": "소주제",
        "ActionItem": "할 일", "Decision": "결정", "Person": "사람"}
# 노드 종류 → [(속성, 화면 이름)] · 첫 속성이 노드 이름(캡션)
FIELDS = {
    "Project": [("name", "이름"), ("created_at", "만든 날")],
    "Meeting": [("title", "제목"), ("held_at", "회의 날짜")],
    "Agenda": [("title", "안건"), ("background", "논의 배경"), ("decisions", "결정사항"),
               ("unresolved_issues", "미해결 이슈"), ("next_topics", "다음 회의 후보")],
    "Topic": [("title", "주제"), ("last_discussed_at", "마지막 논의")],
    "Discussion": [("title", "소주제")],  # 논의 문장은 lines 로 따로
    "ActionItem": [("description", "할 일"), ("owner_raw", "회의록 담당자"), ("due", "마감일"), ("status", "상태")],
    "Decision": [("content", "결정"), ("decided_at", "결정한 날"), ("status", "상태")],
    "Person": [("name", "이름"), ("position", "직급")],
}
STATUS = {"open": "진행 중", "completed": "완료", "active": "유효", "superseded": "이후 바뀜"}


def _value(key: str, v: Any) -> str | list[str] | None:
    if v is None or v == "" or v == []:
        return None
    if hasattr(v, "iso_format"):  # neo4j DateTime · Date → 날짜만
        return v.iso_format()[:10]
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")] or None
    if key == "status":
        return STATUS.get(v, str(v))
    return str(v)


def node(raw: dict[str, Any]) -> dict[str, Any] | None:
    label, props = raw["label"], raw["props"]
    if label not in FIELDS:
        return None
    fields = [{"name": name, "value": val} for key, name in FIELDS[label] if (val := _value(key, props.get(key))) is not None]
    caption = props.get(FIELDS[label][0][0]) or KIND[label]
    out = {"id": raw["id"], "label": label, "kind": KIND[label], "caption": str(caption), "fields": fields}
    if label in ("Agenda", "Discussion") and props.get("order") is not None:
        out["order"] = props["order"]  # 화면 번호 「안건-소주제」 (1-1.) 용
    if label == "Discussion":
        out["lines"] = [{k: ln[k] for k in ("tag", "decision_tag", "text")} for ln in discussion_lines(props)]
    return out


def _meeting_name(m: dict[str, Any]) -> tuple[str, str]:
    """(날짜, 「제목 (날짜)」) — 날짜는 정렬용."""
    date = next((f["value"] for f in m["fields"] if f["name"] == "회의 날짜"), "")
    return date, (f'{m["caption"]} ({date})' if date else m["caption"])


def build(raw: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """raw: adapters/neo4j/folder_graph.read_graph 결과.

    회의 노드는 그리지 않는다 (폴더 안이라 모든 안건 · 사람이 회의에 이어져 복잡하기만 함).
    대신 회의 엣지를 속성 한 줄로 옮긴다: 사람 「참석한 회의」 · 안건 「회의」.
    """
    nodes = [n for n in map(node, raw["nodes"]) if n]
    meetings = {n["id"]: _meeting_name(n) for n in nodes if n["label"] == "Meeting"}
    nodes = [n for n in nodes if n["label"] != "Meeting"]
    by_id = {n["id"]: n for n in nodes}
    attended: dict[str, list[tuple[str, str]]] = {}
    for e in raw["edges"]:
        if e["type"] == "ATTENDED" and e["to"] in meetings and e["from"] in by_id:
            attended.setdefault(e["from"], []).append(meetings[e["to"]])
        elif e["type"] == "HAS_AGENDA" and e["from"] in meetings and e["to"] in by_id:
            by_id[e["to"]]["fields"].append({"name": "회의", "value": meetings[e["from"]][1]})
    for pid, names in attended.items():
        by_id[pid]["fields"].append({"name": "참석한 회의", "value": [name for _, name in sorted(names)]})  # 날짜순
    ids = set(by_id)
    edges = [e for e in raw["edges"] if e["from"] in ids and e["to"] in ids]
    return {"nodes": nodes, "edges": edges}
