"""폴더 위키 (결정 흐름 · 미해결 이슈 보드) 원자료: 프로젝트의 (Topic, 안건) 한 줄씩, 회의 시간순.

Decision · SUPERSEDES 는 「회의에 물어보기」 backfill 이 만든 것만 있다 (돌기 전이면 superseded 는 빈 목록).
"""

from __future__ import annotations

from typing import Any

from followup.adapters.neo4j.driver import get_driver

READ_ROWS = """
MATCH (:Project {project_id: $pid})<-[:IN]-(mt:Meeting)-[:HAS_AGENDA]->(ag:Agenda)-[:ABOUT]->(t:Topic)
WHERE mt.meeting_id IN $mids
RETURN t.topic_id AS topic_id, t.title AS topic,
    mt.meeting_id AS meeting_id, mt.title AS meeting_title, toString(date(mt.held_at)) AS date,
    ag.decisions AS decisions, ag.unresolved_issues AS unresolved, ag.next_topics AS next_topics,
    COLLECT { MATCH (ag)-[:HAS_DISCUSSION]->(dn:Discussion) WITH dn ORDER BY dn.order
              RETURN dn {.discussions, .discussion_tags, .decision_tags, .evidence} } AS subtopics,
    COLLECT { MATCH (ag)-[:DECIDED]->(d:Decision)<-[:SUPERSEDES]-(n:Decision)
              RETURN {old: d.content, new: n.content} } AS superseded
ORDER BY mt.held_at, mt.meeting_id, ag.order"""


def read_rows(project_id: str, meeting_ids: list[str]) -> list[dict[str, Any]]:
    """meeting_ids: PostgreSQL 에 있는 회의만 (지운 회의는 Neo4j 에 남아 있어도 빼야 화면 이동이 404 가 안 남)."""
    return [r.data() for r in get_driver().execute_query(READ_ROWS, pid=project_id, mids=meeting_ids).records]
