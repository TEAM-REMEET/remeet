"""회의 결과 JSON을 Neo4j ontology에 적재한다.

입력 회의 파일은 ``data/summary_action/<회의ID>/<회의ID>_llm_output.json``을 사용한다.
회의록에는 프로젝트/참석자/날짜가 없으므로 다음 규칙으로 보완한다.

* ``TS3003a``와 같은 회의 ID에서 프로젝트 ID ``TS3003``을 만든다.
* 같은 프로젝트의 활성 사용자를 참석자로 연결한다.
* 회의 파일의 정렬 순서를 ``meeting_order``로 저장하고, 시간 비교가 필요한
  기존 로직을 위해 동일한 기준일의 초 단위 timestamp를 만든다.

실행 (ontology 폴더에서):
    uv run python scripts/load.py
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
driver = None

NEXT_TOPIC_MATCH = 0.85
MIN_AGENDAS_FOR_CENTER = 5
SAME_CENTERED = 0.15
GRAY_CENTERED = 0.0
SAME_RAW = 0.80
GRAY_RAW = 0.65
REVIEW_QUEUE: list[dict[str, Any]] = []
BASE_MEETING_TIME = datetime(2004, 10, 11)

_model = None


def get_driver():
    global driver
    if driver is None:
        if not NEO4J_PASSWORD:
            raise RuntimeError("NEO4J_PASSWORD가 설정되지 않았습니다.")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    return driver


def embed(text: str) -> list[float]:
    """BGE-M3로 텍스트 임베딩을 만든다."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("BAAI/bge-m3")
    return _model.encode(text, normalize_embeddings=True).tolist()


def _texts(items: list[dict[str, Any]] | None) -> list[str]:
    """회의록의 ``[{text: ...}]`` 형태를 Neo4j 문자열 목록으로 바꾼다."""
    return [item["text"] for item in items or [] if item.get("text")]


def _evidence(items: list[dict[str, Any]] | None) -> list[int]:
    ids: set[int] = set()
    for item in items or []:
        ids.update(item.get("evidence_utterance_indices", []))
    return sorted(ids)


def normalize_meeting(
    raw: dict[str, Any],
    *,
    meeting_id: str,
    meeting_order: int,
    attendee_ids: list[str],
) -> dict[str, Any]:
    """``*_llm_output.json`` 구조를 로더가 사용하는 내부 구조로 정규화한다.

    major_discussions는 길어서 통째로 넣지 않고, 태그로 두 가지만 꺼낸다.
      discussion_tag == "액션"   → ActionItem (assignee, due 포함)
      decision_tag  == "결정"   → AgendaItem.decisions (액션도 대부분 "결정"이라 여기에도 들어간다)
    unresolved_issues → AgendaItem.unresolved_issues, next_meeting_topics → AgendaItem.next_topics
    """
    project_id = re.sub(r"[a-z]$", "", meeting_id)  # TS3003a → TS3003
    if project_id == meeting_id:
        project_id = f"project-{meeting_id}"

    held_at = BASE_MEETING_TIME + timedelta(seconds=meeting_order)
    agendas: list[dict[str, Any]] = []
    for order, agenda in enumerate(raw.get("agendas", []), 1):
        items = agenda.get("major_discussions", [])
        decisions = [i for i in items if i.get("decision_tag") == "결정"]
        actions = [i for i in items if i.get("discussion_tag") == "액션" and i.get("text")]
        background = agenda.get("discussion_background", [])
        unresolved = agenda.get("unresolved_issues", [])
        next_topics = agenda.get("next_meeting_topics", [])
        agendas.append(
            {
                "source_agenda_id": f"a{order}",  # llm_output에는 안건 ID가 없어서 순번으로 만든다
                "title": agenda.get("agenda_title", "제목 없음"),
                "background": _texts(background),
                "decisions": _texts(decisions),
                "unresolved_issues": _texts(unresolved),
                "next_topics": _texts(next_topics),
                "evidence_utterance_ids": sorted(
                    set(_evidence(background) + _evidence(decisions) + _evidence(actions)
                        + _evidence(unresolved) + _evidence(next_topics))
                ),
                "actions": [
                    {"task": i["text"], "owner": i.get("assignee"), "due": i.get("due")}
                    for i in actions
                ],
            }
        )

    return {
        "meeting_id": meeting_id,
        "project_id": project_id,
        "title": raw.get("title") or f"{meeting_id} 회의",
        "held_at": held_at.isoformat(),
        "meeting_order": meeting_order,
        "attendees": attendee_ids,
        "agendas": agendas,
    }


def run_schema() -> None:
    text = (ROOT / "schema/schema.cypher").read_text(encoding="utf-8")
    for statement in text.split(";"):
        body = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("//")
        ).strip()
        if body:
            get_driver().execute_query(body)


def load_users(data: dict[str, Any]) -> list[str]:
    get_driver().execute_query(
        """
        UNWIND $rows AS r
        MERGE (n:Team {team_id: r.team_id})
        SET n.name = r.name, n.is_active = coalesce(r.is_active, true)
        """,
        rows=data["teams"],
    )
    get_driver().execute_query(
        """
        UNWIND $rows AS r
        MERGE (n:Role {role_id: r.role_id}) SET n.name = r.name
        """,
        rows=data["roles"],
    )
    get_driver().execute_query(
        """
        UNWIND $rows AS r
        MERGE (n:Skill {skill_id: r.skill_id}) SET n += r
        """,
        rows=data["skills"],
    )
    get_driver().execute_query(
        """
        UNWIND $rows AS r
        MERGE (p:Person {user_id: r.user_id})
        SET p.name = r.name, p.is_active = r.is_active, p.position = r.position
        WITH p, r
        OPTIONAL MATCH (t:Team {team_id: r.team_id})
        FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END |
            MERGE (p)-[:MEMBER_OF]->(t))
        WITH p, r
        OPTIONAL MATCH (ro:Role {role_id: r.role_id})
        FOREACH (_ IN CASE WHEN ro IS NULL THEN [] ELSE [1] END |
            MERGE (p)-[:HAS_ROLE]->(ro))
        WITH p, r
        UNWIND r.skills AS s
        MATCH (k:Skill {skill_id: s.skill_id})
        MERGE (p)-[h:HAS_SKILL]->(k)
        SET h.level = s.level, h.updated_at = datetime()
        """,
        rows=data["people"],
    )
    return data["meeting_attendees"]  # 회의 참석자는 A~D뿐이고, 나머지는 추천 테스트용 가상 인물


def load_meeting(meeting: dict[str, Any]) -> None:
    found = get_driver().execute_query(
        "MATCH (mt:Meeting {meeting_id: $mid}) RETURN count(mt) AS c",
        mid=meeting["meeting_id"],
    ).records[0]["c"]
    if found:
        print(f"[건너뜀] {meeting['meeting_id']} 이미 적재됨")
        return
    print(f"\n=== {meeting['meeting_id']} {meeting['title']} ===")
    with get_driver().session() as session:
        session.execute_write(_load_meeting_tx, meeting)


def _load_meeting_tx(tx: Any, meeting: dict[str, Any]) -> None:
    tx.run(
        """
        MERGE (p:Project {project_id: $pid})
        CREATE (mt:Meeting {
            meeting_id: $mid, title: $title, held_at: datetime($at),
            meeting_order: $meeting_order, source_format: 'final_meeting_record'
        })-[:IN]->(p)
        WITH mt
        UNWIND $attendees AS uid
        MATCH (u:Person {user_id: uid})
        MERGE (u)-[:ATTENDED]->(mt)
        """,
        pid=meeting["project_id"],
        mid=meeting["meeting_id"],
        title=meeting["title"],
        at=meeting["held_at"],
        meeting_order=meeting["meeting_order"],
        attendees=meeting["attendees"],
    )

    for order, agenda in enumerate(meeting["agendas"], 1):
        agenda_id = f"{meeting['meeting_id']}-{agenda['source_agenda_id'] or order}"
        context = " ".join(
            agenda["background"] + agenda["decisions"] + agenda["unresolved_issues"]
        )
        vec = embed(f"{agenda['title']} {context}")
        topic_id, score, linked_by = link_topic(
            tx, meeting, agenda_id, agenda["title"], vec
        )
        tx.run(
            """
            MATCH (mt:Meeting {meeting_id: $mid}), (t:Topic {topic_id: $tid})
            CREATE (mt)-[:HAS_AGENDA]->(ai:AgendaItem {
                agenda_item_id: $aid, source_agenda_id: $source_aid,
                order: $order, title: $title, background: $background,
                decisions: $decisions,
                unresolved_issues: $unresolved, next_topics: $next_topics,
                evidence_utterance_ids: $evidence, embedding: $vec
            })-[:ABOUT {confidence: $score, linked_by: $linked_by}]->(t)
            SET t.last_discussed_at = mt.held_at
            """,
            mid=meeting["meeting_id"],
            tid=topic_id,
            aid=agenda_id,
            source_aid=agenda["source_agenda_id"],
            order=order,
            title=agenda["title"],
            background=agenda["background"],
            decisions=agenda["decisions"],
            unresolved=agenda["unresolved_issues"],
            next_topics=agenda["next_topics"],
            evidence=agenda["evidence_utterance_ids"],
            vec=vec,
            score=score,
            linked_by=linked_by,
        )
        refresh_topic_embedding(tx, topic_id)

        # due는 "다음 회의를 위해"처럼 날짜가 아닌 말이 많아서, 원문은 due_raw에 두고
        # YYYY-MM-DD 꼴일 때만 date로 바꿔 due에 넣는다.
        actions = [
            {
                "id": f"{agenda_id}-t{index}",
                "task": action["task"],
                "owner_raw": action.get("owner"),  # recommend_agent.py가 읽는다
                "due_raw": action.get("due"),
                "due": action["due"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", action.get("due") or "") else None,
            }
            for index, action in enumerate(agenda.get("actions", []), 1)
        ]
        tx.run(
            """
            MATCH (ai:AgendaItem {agenda_item_id: $aid})
            UNWIND $actions AS a
            CREATE (ai)-[:PRODUCED]->(:ActionItem {
                action_item_id: a.id, description: a.task, owner_raw: a.owner_raw,
                status: 'open', due_raw: a.due_raw,
                due: CASE WHEN a.due IS NULL THEN NULL ELSE date(a.due) END
            })
            """,
            aid=agenda_id,
            actions=actions,
        )

def link_topic(
    tx: Any, meeting: dict[str, Any], agenda_id: str, title: str, vec: list[float]
) -> tuple[str, float, str]:
    hit = match_by_next_topics(tx, meeting, title)
    return hit or match_by_embedding(tx, meeting["project_id"], agenda_id, title, vec)


def match_by_next_topics(tx: Any, meeting: dict[str, Any], title: str):
    rows = tx.run(
        """
        MATCH (mt:Meeting)-[:IN]->(:Project {project_id: $pid}),
              (mt)-[:HAS_AGENDA]->(ai:AgendaItem)-[:ABOUT]->(t:Topic)
        WHERE mt.meeting_order < $meeting_order
          AND size(coalesce(ai.next_topics, [])) > 0
          AND t.status IN ['open', 'parked']
        RETURN ai.next_topics AS nexts, t.topic_id AS topic_id,
               t.title AS topic_title
        """,
        pid=meeting["project_id"],
        meeting_order=meeting["meeting_order"],
    ).data()
    best_score, best = -1.0, None
    title_vec = np.array(embed(title))
    for row in rows:
        for next_topic in row["nexts"]:
            score = float(np.dot(title_vec, embed(next_topic)))
            if score > best_score:
                best_score, best = score, (row, next_topic)
    if best is None or best_score < NEXT_TOPIC_MATCH:
        return None
    row, next_topic = best
    print(
        f"  [next_topics] {title} -> {row['topic_title']} "
        f"(예고: '{next_topic}', score {best_score:.3f})"
    )
    return row["topic_id"], best_score, "next_topics"


def match_by_embedding(tx: Any, project_id: str, agenda_id: str, title: str, vec: list[float]):
    topics = tx.run(
        """
        MATCH (t:Topic)-[:IN]->(:Project {project_id: $pid})
        WHERE t.status IN ['open', 'parked']
        RETURN t.topic_id AS topic_id, t.title AS title, t.embedding AS embedding
        """,
        pid=project_id,
    ).data()
    agenda_vecs = tx.run(
        """
        MATCH (:Project {project_id: $pid})<-[:IN]-(:Meeting)-[:HAS_AGENDA]->(ai:AgendaItem)
        RETURN ai.embedding AS e
        """,
        pid=project_id,
    ).value("e")
    if len(agenda_vecs) >= MIN_AGENDAS_FOR_CENTER:
        center = np.mean(agenda_vecs, axis=0)
        same, gray, mode = SAME_CENTERED, GRAY_CENTERED, "평균 뺌"
    else:
        center = np.zeros(len(vec))
        same, gray, mode = SAME_RAW, GRAY_RAW, "원래 점수"

    def similarity(other: list[float], candidate: list[float]) -> float:
        left, right = np.asarray(other) - center, np.asarray(candidate) - center
        return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right) + 1e-9))

    scored = sorted(
        ((similarity(vec, topic["embedding"]), topic) for topic in topics),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best = scored[0] if scored else (None, None)
    if best is not None and best_score >= same:
        print(f"  [기존 연결] {title} -> {best['title']} ({mode} {best_score:.3f})")
        return best["topic_id"], best_score, "embedding"

    topic_id = f"tp-{agenda_id}"
    tx.run(
        """
        MATCH (p:Project {project_id: $pid})
        CREATE (:Topic {topic_id: $tid, title: $title, status: 'open', embedding: $vec})-[:IN]->(p)
        """,
        pid=project_id,
        tid=topic_id,
        title=title,
        vec=vec,
    )
    if best is not None and best_score >= gray:
        REVIEW_QUEUE.append(
            {
                "topic_id": topic_id,
                "title": title,
                "candidates": [
                    {"topic_id": topic["topic_id"], "title": topic["title"], "score": round(score, 3)}
                    for score, topic in scored[:3]
                ],
            }
        )
        print(f"  [검토 필요] {title} ? {best['title']} ({mode} {best_score:.3f})")
    else:
        print(f"  [새 Topic] {title}")
    return topic_id, 1.0, "new"


def refresh_topic_embedding(tx: Any, topic_id: str) -> None:
    vectors = tx.run(
        """
        MATCH (:Topic {topic_id: $tid})<-[:ABOUT]-(ai:AgendaItem)
        RETURN ai.embedding AS e
        """,
        tid=topic_id,
    ).value("e")
    if not vectors:
        return
    vector = np.mean(vectors, axis=0)
    vector = vector / np.linalg.norm(vector)
    tx.run(
        "MATCH (t:Topic {topic_id: $tid}) SET t.embedding = $vec",
        tid=topic_id,
        vec=vector.tolist(),
    )


def main() -> None:
    run_schema()
    users = json.loads((ROOT / "data/users.json").read_text(encoding="utf-8"))
    users["skills"] = json.loads((ROOT / "data/skills.json").read_text(encoding="utf-8"))
    attendee_ids = load_users(users)
    # data/summary_action/<회의ID>/<회의ID>_llm_output.json. 폴더 이름이 회의 ID다.
    paths = sorted((ROOT / "data/summary_action").glob("*/*_llm_output.json"))
    if not paths:
        raise FileNotFoundError("data/summary_action/*/*_llm_output.json 파일이 없습니다.")
    for order, path in enumerate(paths, 1):
        raw = json.loads(path.read_text(encoding="utf-8"))
        load_meeting(
            normalize_meeting(
                raw, meeting_id=path.parent.name, meeting_order=order, attendee_ids=attendee_ids
            )
        )
    if REVIEW_QUEUE:
        print(f"\n검토 대기 Topic: {len(REVIEW_QUEUE)}건")
    if driver is not None:
        driver.close()


if __name__ == "__main__":
    main()
