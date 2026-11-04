"""회의 결과 JSON(``*_summary.json``)을 Neo4j ontology에 적재한다. Topic 연결은 LLM이 판단한다.

입력: ``data/summary_action/<회의ID>/<회의ID>_summary.json``

그래프 구조 (임베딩은 쓰지 않는다)

    (Topic)-[:IN]->(Project)
    (Meeting)-[:IN]->(Project)
    (Person)-[:ATTENDED]->(Meeting)
    (Meeting)-[:HAS_AGENDA]->(Agenda)
    (Agenda)-[:ABOUT {confidence, linked_by: 'auto', reason}]->(Topic)
    (Agenda)-[:HAS_DISCUSSION]->(Discussion)          ← Discussion 1개 = 소주제 1개
    (Discussion)-[:PRODUCED]->(ActionItem)            ← 그 소주제의 "액션" 논의
    (Person)-[:MEMBER_OF]->(Team), (Person)-[:HAS_ROLE]->(Role), (Person)-[:HAS_SKILL {level, updated_at}]->(Skill)

    Project    {project_id, name}
    Topic      {topic_id, title, status, last_discussed_at}
    Meeting    {meeting_id, title, held_at}
    Agenda     {agenda_id, order, title, background[], decisions[], unresolved_issues[], next_topics[]}
    Discussion {discussion_id, order, title, discussions[], evidence[], discussion_tags[], decision_tags[]}
    ActionItem {action_item_id, description, status, due, owner_raw}

회의록에는 프로젝트/참석자가 없으므로 다음 규칙으로 보완한다.
* ``TS3003a``와 같은 회의 ID에서 프로젝트 ID ``TS3003``을 만든다.
* users.json의 ``meeting_attendees``를 참석자로 연결한다.
* 회의 순서는 held_at, 같으면 meeting_id 순으로 본다 (지금 데이터는 4개 회의 날짜가 모두 같다).

실행 (ontology 폴더에서):
    uv run python scripts/load.py --reset --effort low
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# 팀 백엔드의 OpenAI 클라이언트를 그대로 쓴다 (remeet/backend/src)
sys.path.insert(0, str(ROOT.parents[2] / "backend/src"))
from followup.adapters.llm.openai_client import OpenAIClient  # noqa: E402  # pyright: ignore[reportMissingImports]  (실행 때 위 sys.path로 찾는다)
from cost import record_run, traced_call  # noqa: E402  (같은 scripts 폴더의 비용 기록)

# shortcut: 프로젝트 이름은 원래 프로젝트를 만들 때 메타데이터로 넘어온다. 그 전까지 쓰는 더미 값.
#           메타데이터가 들어오면 이 표 대신 그 값을 Project.name에 넣는다.
PROJECT_NAMES = {"TS3003": "Real Reaction 신제품 TV 리모컨 개발 프로젝트"}

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
driver = None

_llm = None


def get_driver():
    global driver
    if driver is None:
        if not NEO4J_PASSWORD:
            raise RuntimeError("NEO4J_PASSWORD가 설정되지 않았습니다.")
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))
    return driver


# ─────────────────────────────── summary.json 정규화 ───────────────────────────────

def _active(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """검토 단계에서 빠진 항목(status != ACTIVE)은 적재하지 않는다."""
    return [item for item in items or [] if item.get("status", "ACTIVE") == "ACTIVE" and item.get("text")]


def _texts(items: list[dict[str, Any]]) -> list[str]:
    return [item["text"] for item in items]


def _discussion_line(item: dict[str, Any]) -> str:
    """논의 한 줄을 화면에 보여 줄 모양으로 만든다. 예) "[제안][결정] 단일 곡면 케이스를 유지하기로 함." """
    tags = "".join(f"[{tag}]" for tag in (item.get("discussion_tag"), item.get("decision_tag")) if tag)
    return f"{tags} {item['text']}" if tags else item["text"]


def _discussions(agenda: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """소주제마다 Discussion 노드 1개. 그 안의 논의 문장과 태그·근거는 같은 순서의 배열로 넣는다.

    Neo4j 속성에는 중첩 객체를 넣을 수 없어서 "논의 한 줄 = 배열의 같은 번호 칸"으로 맞춘다.
      discussions[i]     = "[제안][결정] 문장"
      evidence[i]        = "86,87,88"          (배열 안에 배열을 못 넣어서 쉼표로 이은 문자열)
      discussion_tags[i] = "제안"
      decision_tags[i]   = "결정" 또는 ""       (배열 안에 null을 못 넣어서 빈 문자열)
    "액션" 논의는 문장 배열에 넣지 않고 ActionItem으로만 만든다.
    """
    groups = []
    for st in agenda.get("subtopics", []):
        st_items = sorted((i for i in items if i.get("subtopic_id") == st["subtopic_id"]), key=lambda i: i["order"])
        if st_items:
            groups.append((st, st_items))
    # 소주제 순서 = 소주제에 속한 논의 중 가장 앞선 order 기준으로 1, 2, 3 …
    groups.sort(key=lambda g: g[1][0]["order"])

    nodes = []
    for order, (st, st_items) in enumerate(groups, 1):
        points = [i for i in st_items if i.get("discussion_tag") != "액션"]
        actions = [i for i in st_items if i.get("discussion_tag") == "액션"]
        nodes.append(
            {
                "id": st["subtopic_id"],
                "order": order,
                "title": st.get("title") or st_items[0].get("subtopic"),
                "discussions": [_discussion_line(i) for i in points],
                "evidence": [",".join(map(str, i.get("evidence_utterance_indices", []))) for i in points],
                "discussion_tags": [i.get("discussion_tag") or "" for i in points],
                "decision_tags": [i.get("decision_tag") or "" for i in points],
                "actions": [
                    {
                        "id": i["item_id"],              # 예) TS3003a_a1_d5
                        "description": i["text"],
                        "owner_raw": i.get("assignee"),  # recommend_agent.py가 explicit 담당자 판단에 읽는다
                        "due": i.get("due"),             # "다음 회의를 위해"처럼 날짜가 아닌 말이 많아 원문 그대로
                    }
                    for i in actions
                ],
            }
        )
    return nodes


def normalize_meeting(raw: dict[str, Any], *, attendee_ids: list[str]) -> dict[str, Any]:
    """``*_summary.json``을 로더가 쓰는 구조로 바꾼다.

    Meeting   : meeting_id, meeting_info.title → title, meeting_info.date → held_at
    Agenda    : agendas[].agenda_id → agenda_id
      discussion_background → background[]
      major_discussions 중 decision_tag == "결정" → decisions[]
      unresolved_issues → unresolved_issues[], next_meeting_topics → next_topics[]
    Discussion: agendas[].subtopics[] + major_discussions (위 _discussions 참고)
    """
    meeting_id = raw.get("meeting_id")
    if not meeting_id:
        raise ValueError("summary JSON에 meeting_id가 없습니다.")
    info = raw.get("meeting_info", {})

    project_id = re.sub(r"[a-z]$", "", meeting_id)  # TS3003a → TS3003
    if project_id == meeting_id:
        project_id = f"project-{meeting_id}"

    agendas = []
    for order, agenda in enumerate(raw.get("agendas", []), 1):
        items = _active(agenda.get("major_discussions"))
        agendas.append(
            {
                "id": agenda["agenda_id"],
                "order": agenda.get("agenda_number", order),
                "title": agenda.get("agenda_title", "제목 없음"),
                "background": _texts(_active(agenda.get("discussion_background"))),
                "decisions": _texts([i for i in items if i.get("decision_tag") == "결정"]),
                "unresolved_issues": _texts(_active(agenda.get("unresolved_issues"))),
                "next_topics": _texts(_active(agenda.get("next_meeting_topics"))),
                "discussions": _discussions(agenda, items),
            }
        )

    return {
        "meeting_id": meeting_id,
        "project_id": project_id,
        "title": info.get("title") or f"{meeting_id} 회의",
        "held_at": info["date"],
        "attendees": attendee_ids,
        "agendas": agendas,
    }


# ─────────────────────────────── 스키마, 조직 ───────────────────────────────

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


# ─────────────────────────────── 회의 적재 ───────────────────────────────

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
        # 회의 1건 = 트랜잭션 1개. 중간에 실패하면 그 회의 전체가 저장되지 않는다.
        session.execute_write(_load_meeting_tx, meeting)


def _load_meeting_tx(tx: Any, meeting: dict[str, Any]) -> None:
    tx.run(
        """
        MERGE (p:Project {project_id: $pid})
        ON CREATE SET p.name = $pname
        CREATE (mt:Meeting {meeting_id: $mid, title: $title, held_at: datetime($at)})-[:IN]->(p)
        WITH mt
        UNWIND $attendees AS uid
        MATCH (u:Person {user_id: uid})
        MERGE (u)-[:ATTENDED]->(mt)
        """,
        pid=meeting["project_id"],
        pname=PROJECT_NAMES.get(meeting["project_id"], meeting["project_id"]),
        mid=meeting["meeting_id"],
        title=meeting["title"],
        at=meeting["held_at"],
        attendees=meeting["attendees"],
    )

    for agenda in meeting["agendas"]:
        topic_id, score, reason = link_topic(tx, meeting, agenda)

        # Agenda + ABOUT
        tx.run(
            """
            MATCH (mt:Meeting {meeting_id: $mid}), (t:Topic {topic_id: $tid})
            CREATE (mt)-[:HAS_AGENDA]->(ag:Agenda {
                agenda_id: $aid, order: $order, title: $title, background: $background,
                decisions: $decisions, unresolved_issues: $unresolved, next_topics: $next_topics
            })-[:ABOUT {confidence: $score, linked_by: 'auto', reason: $reason}]->(t)
            SET t.last_discussed_at = mt.held_at
            """,
            mid=meeting["meeting_id"],
            tid=topic_id,
            aid=agenda["id"],
            order=agenda["order"],
            title=agenda["title"],
            background=agenda["background"],
            decisions=agenda["decisions"],
            unresolved=agenda["unresolved_issues"],
            next_topics=agenda["next_topics"],
            score=score,
            reason=reason,  # 왜 이 Topic에 붙었는지. scripts/report.py가 보여 준다
        )

        # Discussion(소주제) + 그 소주제에서 나온 ActionItem
        tx.run(
            """
            MATCH (ag:Agenda {agenda_id: $aid})
            UNWIND $discussions AS d
            CREATE (ag)-[:HAS_DISCUSSION]->(dn:Discussion {
                discussion_id: d.id, order: d.order, title: d.title,
                discussions: d.discussions, evidence: d.evidence,
                discussion_tags: d.discussion_tags, decision_tags: d.decision_tags
            })
            WITH dn, d
            UNWIND d.actions AS a
            CREATE (dn)-[:PRODUCED]->(:ActionItem {
                action_item_id: a.id, description: a.description,
                status: 'open', due: a.due, owner_raw: a.owner_raw
            })
            """,
            aid=agenda["id"],
            discussions=agenda["discussions"],
        )


# ─────────────────────────────── Topic 연결 (LLM) ───────────────────────────────

TOPIC_JUDGE_SYSTEM = """너는 회의 안건을 프로젝트의 Topic(여러 회의에 걸쳐 이어지는 논의 주제)에 연결하는 분류기다.

판단 기준:
- Topic 주제를 이어서 다루거나 관련 문제, 관련 대상을 다루면 기존 Topic이다.
  (예: 지난 회의 "버튼 배치 설계" → 이번 회의 "버튼 개수 축소 결정")
- 후보 Topic의 next_topics(다음 회의에서 다루기로 한 것)에 이번 안건이 예고되어 있으면 그 Topic일 가능성이 높다.
- 프로젝트 전체를 아우르는 넓은 Topic에 모든 안건을 몰아넣지 않는다.
- 확신이 없으면 새 Topic을 고른다. 따로 만든 Topic은 나중에 합치기 쉽지만, 잘못 합친 Topic은 떼어 내기 어렵다.

반드시 아래 JSON 하나만 출력한다.
{"decision": "existing" 또는 "new", "topic_id": "기존이면 후보의 topic_id, 새 Topic이면 null", "reason": "한 문장"}"""


LLM_MODEL = "gpt-6-luna"   # 모델은 고정하고 effort만 바꿔 가며 비교한다
LLM_EFFORT = "high"          # main()에서 --effort로 바꾼다


def get_llm() -> OpenAIClient:
    global _llm
    if _llm is None:
        _llm = OpenAIClient(model=LLM_MODEL, reasoning_effort=LLM_EFFORT)
    return _llm


def link_topic(tx: Any, meeting: dict[str, Any], agenda: dict[str, Any]) -> tuple[str, float | None, str]:
    """LLM이 이번 안건과 열린 Topic 목록을 읽고 기존 Topic에 붙일지, 새 Topic을 만들지 고른다.

    (topic_id, confidence, 이유)를 돌려준다. LLM은 믿을 만한 점수를 주지 않아서 기존 연결의 confidence는 비워 둔다.
    """
    # Topic마다 최근에 연결된 안건 3개(제목·결정·next_topics)를 함께 보여 준다
    # shortcut: 열린 Topic을 전부 프롬프트에 넣는다. Topic이 50개를 넘으면 후보를 줄이는 단계를 추가한다.
    topics = tx.run(
        """
        MATCH (t:Topic)-[:IN]->(:Project {project_id: $pid})
        WHERE t.status IN ['open', 'parked']
        OPTIONAL MATCH (t)<-[:ABOUT]-(ag:Agenda)<-[:HAS_AGENDA]-(mt:Meeting)
        WITH t, ag, mt ORDER BY mt.held_at DESC, mt.meeting_id DESC, ag.order DESC
        WITH t, collect({meeting: mt.meeting_id, title: ag.title, decisions: ag.decisions,
                         next_topics: ag.next_topics})[..3] AS recent_agendas
        RETURN t.topic_id AS topic_id, t.title AS title, recent_agendas
        """,
        pid=meeting["project_id"],
    ).data()

    if topics:
        decision = _ask_llm(agenda, topics)
        candidate = {t["topic_id"]: t for t in topics}
        # LLM 출력은 믿지 않고 검사한다: 후보에 없는 topic_id면 새 Topic으로 처리
        if decision and decision.get("decision") == "existing" and decision.get("topic_id") in candidate:
            hit = candidate[decision["topic_id"]]
            reason = decision.get("reason", "")
            print(f"  [LLM 연결] {agenda['title']} -> {hit['title']}  ({reason})")
            return hit["topic_id"], None, reason
        reason = decision.get("reason", "") if decision else "LLM 응답 형식 오류 → 새 Topic"
    else:
        reason = "비교할 Topic 없음"

    topic_id = f"tp-{agenda['id']}"
    tx.run(
        """
        MATCH (p:Project {project_id: $pid})
        CREATE (:Topic {topic_id: $tid, title: $title, status: 'open'})-[:IN]->(p)
        """,
        pid=meeting["project_id"],
        tid=topic_id,
        title=agenda["title"],
    )
    print(f"  [새 Topic] {agenda['title']}  ({reason})")
    return topic_id, 1.0, reason  # 새 Topic은 자기 자신과 연결이라 1.0


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


# ─────────────────────────────── 실행 ───────────────────────────────

def main() -> None:
    global LLM_EFFORT
    parser = argparse.ArgumentParser(description="회의 데이터를 LLM Topic 판단으로 적재한다.")
    # 잘못된 값이면 --reset으로 데이터를 지우기 전에 여기서 멈춘다 (Chat Completions API 기준, max는 없다)
    parser.add_argument("--effort", default=LLM_EFFORT, choices=["none", "low", "medium", "high", "xhigh"])
    parser.add_argument("--reset", action="store_true",
                        help="적재 전에 회의 데이터(Meeting, Agenda, Discussion, ActionItem, Topic)를 지운다. "
                             "예전 구조의 AgendaItem, Subtopic도 같이 지운다. 사람·팀·스킬은 남는다. 되돌릴 수 없다.")
    args = parser.parse_args()
    LLM_EFFORT = args.effort
    print(f"LLM: {LLM_MODEL} (reasoning_effort={LLM_EFFORT})")

    if args.reset:
        # 이미 적재된 회의는 건너뛰기 때문에, 다시 적재하려면 먼저 지워야 한다
        get_driver().execute_query("""
            MATCH (n) WHERE n:Meeting OR n:Agenda OR n:Discussion OR n:ActionItem OR n:Topic
                         OR n:AgendaItem OR n:Subtopic
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
    # 회의 순서: 날짜, 같으면 회의 ID
    raw_meetings.sort(key=lambda m: (m.get("meeting_info", {}).get("date", ""), m["meeting_id"]))
    status = "failed"
    try:
        for raw in raw_meetings:
            load_meeting(normalize_meeting(raw, attendee_ids=attendee_ids))
        status = "ok"
    finally:
        # 중간에 실패해도 그때까지 나간 호출 비용은 기록한다
        if _llm is not None and _llm.calls:
            record_run(Path(__file__).name, LLM_MODEL, LLM_EFFORT, _llm.calls, status)
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
