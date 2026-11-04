"""회의 결과 JSON을 Neo4j ontology에 적재한다.

입력 회의 파일은 ``data/summary_action/<회의ID>/<회의ID>_summary.json``을 사용한다.
회의록에는 프로젝트/참석자가 없으므로 다음 규칙으로 보완한다.

* ``TS3003a``와 같은 회의 ID에서 프로젝트 ID ``TS3003``을 만든다.
* 같은 프로젝트의 활성 사용자를 참석자로 연결한다.
* 회의 ID 순서를 ``meeting_order``로 저장한다. 회의 날짜(held_at)가 같을 수 있어서 순서 비교는 이 값으로 한다.

실행 (ontology 폴더에서):
    uv run python scripts/load_llm.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import sys

import numpy as np
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# 팀 백엔드의 OpenAI 클라이언트를 그대로 쓴다 (remeet/backend/src)
sys.path.insert(0, str(ROOT.parents[2] / "backend/src"))
from followup.adapters.llm.openai_client import OpenAIClient  # noqa: E402  # pyright: ignore[reportMissingImports]  (실행 때 위 sys.path로 찾는다)
from cost import record_run, traced_call  # noqa: E402  (같은 scripts 폴더의 비용 기록)

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
driver = None

NEXT_TOPIC_MATCH = 0.85

_model = None
_llm = None


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


def _active(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """검토 단계에서 빠진 항목(status != ACTIVE)은 적재하지 않는다."""
    return [item for item in items or [] if item.get("status", "ACTIVE") == "ACTIVE" and item.get("text")]


def _texts(items: list[dict[str, Any]]) -> list[str]:
    return [item["text"] for item in items]


def _evidence(items: list[dict[str, Any]]) -> list[int]:
    ids: set[int] = set()
    for item in items:
        ids.update(item.get("evidence_utterance_indices", []))
    return sorted(ids)


def _discussion_line(item: dict[str, Any]) -> str:
    """주요 논의 항목 하나를 웹 화면과 같은 한 줄로 만든다.

    예) "[케이스] [제안][결정] LCD와 도움말 기능을 돋보이게 하기 위해 단일 곡면 케이스를 유지하기로 함."
    Neo4j 속성에는 중첩 객체를 넣을 수 없어서 문자열 목록으로 넣는다.
    근거 발화 번호는 안건의 evidence_utterance_ids에 모아 두므로 줄마다 붙이지 않는다.
    """
    tags = "".join(f"[{tag}]" for tag in (item.get("discussion_tag"), item.get("decision_tag")) if tag)
    prefix = f"[{item['subtopic']}] " if item.get("subtopic") else ""
    return f"{prefix}{tags} {item['text']}" if tags else f"{prefix}{item['text']}"


def normalize_meeting(
    raw: dict[str, Any],
    *,
    meeting_order: int,
    attendee_ids: list[str],
) -> dict[str, Any]:
    """``*_summary.json`` 구조를 로더가 사용하는 내부 구조로 정규화한다.

    Meeting   : meeting_id, meeting_info.title → title, meeting_info.date → held_at
    AgendaItem: agendas[].agenda_id → agenda_item_id (agenda_index의 agenda_id와 같다)
      discussion_background → background
      major_discussions     → discussion (한 줄씩 통째로) +
                              decision_tag == "결정" → decisions, discussion_tag == "액션" → ActionItem
      unresolved_issues     → unresolved_issues
      next_meeting_topics   → next_topics
    """
    meeting_id = raw.get("meeting_id")
    if not meeting_id:
        raise ValueError("summary JSON에 meeting_id가 없습니다.")
    info = raw.get("meeting_info", {})

    project_id = re.sub(r"[a-z]$", "", meeting_id)  # TS3003a → TS3003
    if project_id == meeting_id:
        project_id = f"project-{meeting_id}"

    agendas: list[dict[str, Any]] = []
    for agenda in raw.get("agendas", []):
        items = _active(agenda.get("major_discussions"))
        decisions = [i for i in items if i.get("decision_tag") == "결정"]
        actions = [i for i in items if i.get("discussion_tag") == "액션"]
        background = _active(agenda.get("discussion_background"))
        unresolved = _active(agenda.get("unresolved_issues"))
        next_topics = _active(agenda.get("next_meeting_topics"))
        agendas.append(
            {
                "source_agenda_id": agenda["agenda_id"],
                "title": agenda.get("agenda_title", "제목 없음"),
                "background": _texts(background),
                "discussion": [_discussion_line(i) for i in items],
                "decisions": _texts(decisions),
                "unresolved_issues": _texts(unresolved),
                "next_topics": _texts(next_topics),
                "evidence_utterance_ids": sorted(
                    set(_evidence(background) + _evidence(items)
                        + _evidence(unresolved) + _evidence(next_topics))
                ),
                "actions": [
                    {"id": i["item_id"], "task": i["text"], "owner": i.get("assignee"), "due": i.get("due")}
                    for i in actions
                ],
            }
        )

    return {
        "meeting_id": meeting_id,
        "project_id": project_id,
        "title": info.get("title") or f"{meeting_id} 회의",
        # 지금 데이터는 4개 회의 날짜가 모두 같아서, 회의 순서는 held_at이 아니라 meeting_order로 비교한다
        "held_at": info["date"],
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
        agenda_id = agenda["source_agenda_id"]  # 예) TS3003a_a1
        context = " ".join(
            agenda["background"] + agenda["decisions"] + agenda["unresolved_issues"]
        )
        vec = embed(f"{agenda['title']} {context}")
        topic_id, score, linked_by, reason = link_topic(tx, meeting, agenda_id, agenda, vec)
        tx.run(
            """
            MATCH (mt:Meeting {meeting_id: $mid}), (t:Topic {topic_id: $tid})
            CREATE (mt)-[:HAS_AGENDA]->(ai:AgendaItem {
                agenda_item_id: $aid, source_agenda_id: $source_aid,
                order: $order, title: $title, background: $background,
                discussion: $discussion, decisions: $decisions,
                unresolved_issues: $unresolved, next_topics: $next_topics,
                evidence_utterance_ids: $evidence, embedding: $vec
            })-[:ABOUT {confidence: $score, linked_by: $linked_by, reason: $reason}]->(t)
            SET t.last_discussed_at = mt.held_at
            """,
            mid=meeting["meeting_id"],
            tid=topic_id,
            aid=agenda_id,
            source_aid=agenda["source_agenda_id"],
            order=order,
            title=agenda["title"],
            background=agenda["background"],
            discussion=agenda["discussion"],
            decisions=agenda["decisions"],
            unresolved=agenda["unresolved_issues"],
            next_topics=agenda["next_topics"],
            evidence=agenda["evidence_utterance_ids"],
            vec=vec,
            score=score,
            linked_by=linked_by,
            reason=reason,  # 왜 이 Topic에 붙었는지. scripts/report.py가 보여 준다
        )
        refresh_topic_embedding(tx, topic_id)

        # due는 "다음 회의를 위해"처럼 날짜가 아닌 말이 많아서, 원문은 due_raw에 두고
        # YYYY-MM-DD 꼴일 때만 date로 바꿔 due에 넣는다.
        actions = [
            {
                "id": action["id"],  # summary.json의 item_id (예: TS3003a_a1_d3)
                "task": action["task"],
                "owner_raw": action.get("owner"),  # recommend_agent.py가 읽는다
                "due_raw": action.get("due"),
                "due": action["due"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", action.get("due") or "") else None,
            }
            for action in agenda.get("actions", [])
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
    tx: Any, meeting: dict[str, Any], agenda_id: str, agenda: dict[str, Any], vec: list[float]
) -> tuple[str, float | None, str, str]:
    """이번 안건을 연결할 Topic을 정한다. (topic_id, 점수, 연결 방법, 이유)를 돌려준다.

    ① 이전 회의 next_topics와 제목이 확실히 맞으면 그 Topic (LLM 호출 없음)
    ② 아니면 LLM이 열린 Topic 목록을 보고 "기존 Topic" 또는 "새 Topic"을 고른다
    """
    hit = match_by_next_topics(tx, meeting, agenda["title"])
    return hit or match_by_llm(tx, meeting["project_id"], agenda_id, agenda, vec)


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
    return row["topic_id"], best_score, "next_topics", f"이전 회의 예고: {next_topic}"


TOPIC_JUDGE_SYSTEM = """너는 회의 안건을 프로젝트의 Topic(여러 회의에 걸쳐 이어지는 논의 주제)에 연결하는 분류기다.

판단 기준:
- Topic 주제를 이어서 다루거나 관련 문제, 관련 대상을 다루면 기존 Topic이다.
  (예: 지난 회의 "버튼 배치 설계" → 이번 회의 "버튼 개수 축소 결정")
- 프로젝트 전체를 아우르는 넓은 Topic에 모든 안건을 몰아넣지 않는다.
- 확신이 없으면 새 Topic을 고른다. 따로 만든 Topic은 나중에 합치기 쉽지만, 잘못 합친 Topic은 떼어 내기 어렵다.

반드시 아래 JSON 하나만 출력한다.
{"decision": "existing" 또는 "new", "topic_id": "기존이면 후보의 topic_id, 새 Topic이면 null", "reason": "한 문장"}"""


LLM_MODEL = "gpt-6-luna"   # 모델은 고정하고 effort만 바꿔 가며 비교한다
LLM_EFFORT = "low"          # main()에서 --effort로 바꾼다


def get_llm() -> OpenAIClient:
    global _llm
    if _llm is None:
        _llm = OpenAIClient(model=LLM_MODEL, reasoning_effort=LLM_EFFORT)
    return _llm


def match_by_llm(
    tx: Any, project_id: str, agenda_id: str, agenda: dict[str, Any], vec: list[float]
) -> tuple[str, float | None, str, str]:
    """② LLM이 이번 안건과 열린 Topic 목록을 읽고 기존 Topic에 붙일지, 새 Topic을 만들지 고른다.

    임베딩은 "비슷한 단어를 쓰나"만 알아서, 한 프로젝트 안에서는 다른 주제끼리도 점수가 비슷하게 나온다.
    LLM은 배경·결정사항을 읽고 "같은 일이 이어지는가"를 판단할 수 있다.
    """
    # Topic마다 최근에 연결된 안건 3개를 함께 보여 준다 (제목만으로는 Topic이 무슨 일인지 알기 어렵다)
    # shortcut: 열린 Topic을 전부 프롬프트에 넣는다. Topic이 50개를 넘으면
    #           임베딩(t.embedding)으로 상위 10개만 추려서 넘긴다.
    topics = tx.run(
        """
        MATCH (t:Topic)-[:IN]->(:Project {project_id: $pid})
        WHERE t.status IN ['open', 'parked']
        OPTIONAL MATCH (t)<-[:ABOUT]-(ai:AgendaItem)<-[:HAS_AGENDA]-(mt:Meeting)
        WITH t, ai, mt ORDER BY mt.meeting_order DESC, ai.order DESC
        WITH t, collect({meeting: mt.meeting_id, title: ai.title, decisions: ai.decisions,
                         next_topics: ai.next_topics})[..3] AS recent_agendas
        RETURN t.topic_id AS topic_id, t.title AS title, recent_agendas
        """,
        pid=project_id,
    ).data()

    if topics:
        decision = _ask_llm(agenda, topics)
        candidate = {t["topic_id"]: t for t in topics}
        # LLM 출력은 믿지 않고 검사한다: 후보에 없는 topic_id면 새 Topic으로 처리
        if decision and decision.get("decision") == "existing" and decision.get("topic_id") in candidate:
            hit = candidate[decision["topic_id"]]
            print(f"  [LLM 연결] {agenda['title']} -> {hit['title']}  ({decision.get('reason', '')})")
            return hit["topic_id"], None, "llm", decision.get("reason", "")  # LLM은 신뢰할 만한 점수를 주지 않아서 confidence는 비워 둔다
        reason = decision.get("reason", "") if decision else "LLM 응답 형식 오류 → 새 Topic"
    else:
        reason = "비교할 Topic 없음"

    # 새 Topic. 벡터는 일단 이 안건 벡터이고, 안건이 붙을 때마다 refresh_topic_embedding이 평균으로 갱신한다.
    topic_id = f"tp-{agenda_id}"
    tx.run(
        """
        MATCH (p:Project {project_id: $pid})
        CREATE (:Topic {topic_id: $tid, title: $title, status: 'open', embedding: $vec})-[:IN]->(p)
        """,
        pid=project_id,
        tid=topic_id,
        title=agenda["title"],
        vec=vec,
    )
    print(f"  [새 Topic] {agenda['title']}  ({reason})")
    return topic_id, 1.0, "new", reason  # 새 Topic은 자기 자신과 연결이라 1.0


def _ask_llm(agenda: dict[str, Any], topics: list[dict[str, Any]]) -> dict[str, Any] | None:
    """LLM에 판단을 묻고 JSON을 돌려준다. 형식이 깨졌으면 None."""
    user = (
        "## 이번 안건\n"
        + json.dumps(
            {
                "title": agenda["title"],
                "background": agenda["background"],
                "decisions": agenda["decisions"],
                "unresolved_issues": agenda["unresolved_issues"],
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n\n## 열린 Topic 후보\n"
        + json.dumps(topics, ensure_ascii=False, indent=1)
    )
    content = traced_call(get_llm(), TOPIC_JUDGE_SYSTEM, user)
    found = re.search(r"\{.*\}", content, re.S)  # ```json 같은 감싸기가 붙어 와도 JSON 부분만 꺼낸다
    try:
        return json.loads(found.group()) if found else None
    except json.JSONDecodeError:
        return None


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
    global LLM_EFFORT
    parser = argparse.ArgumentParser(description="회의 데이터를 LLM Topic 판단으로 적재한다.")
    # 잘못된 값이면 --reset으로 데이터를 지우기 전에 여기서 멈춘다 (gpt-6-luna 기준, max는 없다)
    parser.add_argument("--effort", default=LLM_EFFORT, choices=["none", "low", "medium", "high", "xhigh"])
    parser.add_argument("--reset", action="store_true",
                        help="적재 전에 회의 데이터(Meeting, AgendaItem, ActionItem, Topic)를 지운다. "
                             "사람·팀·스킬은 남는다. 되돌릴 수 없다.")
    args = parser.parse_args()
    LLM_EFFORT = args.effort
    print(f"LLM: {LLM_MODEL} (reasoning_effort={LLM_EFFORT})")

    if args.reset:
        # 이미 적재된 회의는 건너뛰기 때문에, 모델을 바꿔 비교하려면 먼저 지워야 한다
        get_driver().execute_query("""
            MATCH (n) WHERE n:Meeting OR n:AgendaItem OR n:ActionItem OR n:Topic
            DETACH DELETE n
        """)
        print(f"[초기화] 회의 데이터 삭제: {NEO4J_URI}")

    run_schema()
    users = json.loads((ROOT / "data/users.json").read_text(encoding="utf-8"))
    users["skills"] = json.loads((ROOT / "data/skills.json").read_text(encoding="utf-8"))
    attendee_ids = load_users(users)
    raw_meetings = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "data/summary_action").glob("*/*_summary.json")
    ]
    if not raw_meetings:
        raise FileNotFoundError("data/summary_action/*/*_summary.json 파일이 없습니다.")
    raw_meetings.sort(key=lambda meeting: meeting["meeting_id"])
    status = "failed"
    try:
        for order, raw in enumerate(raw_meetings, 1):
            load_meeting(normalize_meeting(raw, meeting_order=order, attendee_ids=attendee_ids))
        status = "ok"
    finally:
        # 중간에 실패해도 그때까지 나간 호출 비용은 기록한다
        if _llm is not None and _llm.calls:
            record_run(Path(__file__).name, LLM_MODEL, LLM_EFFORT, _llm.calls, status)
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
