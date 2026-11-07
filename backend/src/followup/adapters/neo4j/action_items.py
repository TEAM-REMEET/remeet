"""회의의 액션아이템 조회 (담당자 · 선행 업무 담당자 · 추천 후보)와 담당자 확정.

추천 결과는 Neo4j에서 다시 읽는다 → 새로고침 뒤 같은 회의로 다시 요청해도 LLM을 또 부르지 않고 같은 결과가 나온다.
"""

from __future__ import annotations

from typing import Any

from followup.adapters.neo4j.driver import get_driver

# 진행 중 업무 = 완료되지 않은 배정 (recommend_agent.CANDIDATE_QUERY의 open_tasks와 같은 조건)
_OPEN_TASKS = """COUNT { MATCH (p)-[x:ASSIGNED_TO]->(t:ActionItem)
    WHERE coalesce(x.work_status, 'in_work') <> 'work_completed' AND coalesce(t.status, 'open') <> 'completed' }"""

READ_ITEMS = f"""
MATCH (mt:Meeting {{meeting_id: $mid}})-[:HAS_AGENDA]->(ag:Agenda)-[:HAS_DISCUSSION]->(dn:Discussion)
      -[:PRODUCED]->(ai:ActionItem)
RETURN ai.action_item_id AS item_id, ai.description AS text, ai.due AS due,
    ai.recommend_status AS recommend_status, ai.missing_skills AS missing_skills,
    COLLECT {{ MATCH (p:Person)-[a:ASSIGNED_TO]->(ai)
              RETURN p {{.user_id, .name, source: a.assign_source}} }} AS assignees,
    COLLECT {{ MATCH (ai)-[:FOLLOWS_UP]->(prev:ActionItem)<-[:ASSIGNED_TO]-(p:Person {{is_active: true}})
              MATCH (pm:Meeting)-[:HAS_AGENDA]->(:Agenda)-[:HAS_DISCUSSION]->(:Discussion)-[:PRODUCED]->(prev)
              RETURN p {{.user_id, .name,
                        team: COLLECT {{ MATCH (p)-[:MEMBER_OF]->(t:Team) RETURN t.name }}[0],
                        role: COLLECT {{ MATCH (p)-[:HAS_ROLE]->(r:Role) RETURN r.name }}[0],
                        task: prev.description, meeting: pm.title, date: toString(date(pm.held_at))}} }} AS predecessors,
    COLLECT {{ MATCH (p:Person)-[r:RECOMMENDED_FOR]->(ai)  // 고른 뒤(selected · not_selected)에도 후보는 그대로 보여 줌
              WITH p, r ORDER BY r.score DESC, p.user_id
              RETURN p {{.user_id, .name,
                        team: COLLECT {{ MATCH (p)-[:MEMBER_OF]->(t:Team) RETURN t.name }}[0],
                        role: COLLECT {{ MATCH (p)-[:HAS_ROLE]->(ro:Role) RETURN ro.name }}[0],
                        score: r.score, skill_score: r.skill_score, role_team_score: r.role_team_score,
                        workload_score: r.workload_score,
                        attended: EXISTS {{ (p)-[:ATTENDED]->(mt) }},
                        skills: COLLECT {{ MATCH (ai)-[:REQUIRES_SKILL]->(s:Skill)<-[h:HAS_SKILL]-(p)
                                          WHERE h.level > 0 RETURN s.name }},
                        open_tasks: {_OPEN_TASKS}}} }} AS candidates
ORDER BY ag.order, dn.order, item_id"""

# 담당자 · 마감일 확정 (「다음: 최종 회의록」 = 사람이 검토(HITL)해서 정한 값). 한 트랜잭션이라 전부 저장되거나 하나도 안 된다.
#   ASSIGNED_TO  고른 사람에 없는 배정은 지움 (회의록 담당자도 · 원래 회의록 표기는 ActionItem.owner_raw에 남아 있음)
#                그대로인 사람은 원래 assign_source(meeting_explicit · manual)를 남기고, 새 사람은 manual
#                최종 담당자 전부에 confirmed_at (= 주관자가 확인 · 확정한 배정)
#   RECOMMENDED_FOR.status  selected / not_selected → 나중에 추천 품질을 볼 수 있게
#   ActionItem   due(YYYY-MM-DD) · confirmed_at
CONFIRM_ITEMS = """
UNWIND $items AS it
MATCH (ai:ActionItem {action_item_id: it.item_id})
SET ai.due = it.due, ai.confirmed_at = datetime()
WITH ai, it
OPTIONAL MATCH (old:Person)-[o:ASSIGNED_TO]->(ai) WHERE NOT old.user_id IN it.user_ids
DELETE o
WITH DISTINCT ai, it
OPTIONAL MATCH (c:Person)-[r:RECOMMENDED_FOR]->(ai)
SET r.status = CASE WHEN c.user_id IN it.user_ids THEN 'selected' ELSE 'not_selected' END
WITH DISTINCT ai, it
UNWIND it.user_ids AS uid
MATCH (p:Person {user_id: uid})
MERGE (p)-[a:ASSIGNED_TO]->(ai)
ON CREATE SET a.work_status = 'in_work', a.assign_source = 'manual', a.assigned_at = datetime()
SET a.confirmed_at = datetime()"""

ITEM_IDS = """MATCH (:Meeting {meeting_id: $mid})-[:HAS_AGENDA]->()-[:HAS_DISCUSSION]->()-[:PRODUCED]->(ai:ActionItem)
RETURN ai.action_item_id AS id"""

READ_MEETING = """
MATCH (mt:Meeting {meeting_id: $mid})-[:IN]->(pr:Project)
RETURN mt.meeting_id AS meeting_id, mt.title AS title, toString(date(mt.held_at)) AS date,
       pr.project_id AS project_id,
       COLLECT { MATCH (p:Person)-[:ATTENDED]->(mt) RETURN p {.user_id, .name} } AS attendees,
       COLLECT { MATCH (mt)-[:HAS_AGENDA]->(ag:Agenda) WITH ag ORDER BY ag.order
                 RETURN ag {.agenda_id, .order, .title, .background, .decisions, .unresolved_issues, .next_topics} } AS agendas"""


def meeting_items(meeting_id: str) -> list[dict[str, Any]]:
    records, _, _ = get_driver().execute_query(READ_ITEMS, mid=meeting_id, routing_="r")
    return [r.data() for r in records]


def confirm_items(meeting_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """items = [{item_id, user_ids, due(YYYY-MM-DD)}] — 이 회의의 액션아이템 전부. 없는 회의면 None.
    빠진 액션아이템 · 다른 회의 액션아이템 · 없는 사람이 있으면 ValueError (아무것도 저장 안 함)."""
    def work(tx):
        if not tx.run("MATCH (m:Meeting {meeting_id: $mid}) RETURN m", mid=meeting_id).single():
            return None
        have = {r["id"] for r in tx.run(ITEM_IDS, mid=meeting_id)}
        sent = {i["item_id"] for i in items}
        errors = []
        if have - sent:
            errors.append(f"담당자 · 마감일이 빠진 액션 아이템: {', '.join(sorted(have - sent))}")
        if sent - have:
            errors.append(f"이 회의에 없는 액션 아이템: {', '.join(sorted(sent - have))}")
        ids = sorted({u for i in items for u in i["user_ids"]})
        unknown = set(ids) - {r["id"] for r in tx.run("MATCH (p:Person) WHERE p.user_id IN $ids RETURN p.user_id AS id", ids=ids)}
        if unknown:
            errors.append(f"없는 사람: {', '.join(sorted(unknown))}")
        if errors:
            raise ValueError(" / ".join(errors))
        tx.run(CONFIRM_ITEMS, items=items).consume()
        return True

    with get_driver().session() as session:
        found = session.execute_write(work)
    return meeting_items(meeting_id) if found else None


def minutes(meeting_id: str) -> dict[str, Any] | None:
    """최종 회의록: 회의 정보 · 안건 · 액션아이템과 확정된 담당자. 없는 회의면 None."""
    records, _, _ = get_driver().execute_query(READ_MEETING, mid=meeting_id, routing_="r")
    if not records:
        return None
    items = [{k: i[k] for k in ("item_id", "text", "due", "assignees")} for i in meeting_items(meeting_id)]
    return records[0].data() | {"action_items": items}
