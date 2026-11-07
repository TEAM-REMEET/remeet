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

# 주관자가 고른 사람으로 바꾼다: 예전 manual 배정을 지우고 새로 잇는다 (회의록 명시 담당자는 그대로).
# 추천 엣지에는 골랐는지 남긴다 (selected / not_selected, 비우면 pending으로) → 나중에 추천 품질을 볼 수 있게.
SET_ASSIGNEES = """
MATCH (ai:ActionItem {action_item_id: $id})
OPTIONAL MATCH (:Person)-[old:ASSIGNED_TO {assign_source: 'manual'}]->(ai)
DELETE old
WITH DISTINCT ai
OPTIONAL MATCH (c:Person)-[r:RECOMMENDED_FOR]->(ai)
SET r.status = CASE WHEN size($ids) = 0 THEN 'pending' WHEN c.user_id IN $ids THEN 'selected' ELSE 'not_selected' END
WITH DISTINCT ai
UNWIND $ids AS uid
MATCH (p:Person {user_id: uid})
WHERE NOT (p)-[:ASSIGNED_TO]->(ai)
CREATE (p)-[:ASSIGNED_TO {work_status: 'in_work', assign_source: 'manual', assigned_at: datetime()}]->(ai)"""

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


def set_assignees(item_id: str, user_ids: list[str]) -> list[dict[str, Any]] | None:
    """담당자를 user_ids로 확정한다. 없는 액션아이템이면 None, 있으면 확정 뒤 담당자 목록."""
    def work(tx):
        if not tx.run("MATCH (ai:ActionItem {action_item_id: $id}) RETURN ai", id=item_id).single():
            return None
        unknown = set(user_ids) - {r["id"] for r in tx.run(
            "MATCH (p:Person) WHERE p.user_id IN $ids RETURN p.user_id AS id", ids=user_ids)}
        if unknown:
            raise ValueError(f"없는 사람: {', '.join(sorted(unknown))}")
        tx.run(SET_ASSIGNEES, id=item_id, ids=user_ids).consume()
        return tx.run("MATCH (p:Person)-[a:ASSIGNED_TO]->(:ActionItem {action_item_id: $id}) "
                      "RETURN p.user_id AS user_id, p.name AS name, a.assign_source AS source ORDER BY user_id",
                      id=item_id).data()

    with get_driver().session() as session:
        return session.execute_write(work)


def minutes(meeting_id: str) -> dict[str, Any] | None:
    """최종 회의록: 회의 정보 · 안건 · 액션아이템과 확정된 담당자. 없는 회의면 None."""
    records, _, _ = get_driver().execute_query(READ_MEETING, mid=meeting_id, routing_="r")
    if not records:
        return None
    items = [{k: i[k] for k in ("item_id", "text", "due", "assignees")} for i in meeting_items(meeting_id)]
    return records[0].data() | {"action_items": items}
