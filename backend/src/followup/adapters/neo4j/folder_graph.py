"""폴더 그래프 탭 원자료: 프로젝트 한 개 범위의 노드 · 엣지 (Neo4j Browser 처럼 그대로 보여 주기 위함).

범위 = 프로젝트의 회의(PostgreSQL 에 있는 것만) + 회의에서 이어지는 안건 · 주제 · 소주제 · 할 일 · 결정
     + 그 회의에 참석했거나 할 일을 맡은 사람. 프로젝트 노드는 뺀다 (그 폴더 안에서 보는 화면이라 모든 노드가 이어져 복잡하기만 함).
속성은 여기서 거르지 않고 application/folder_graph 가 노드 종류별로 보여 줄 것만 고른다 (Person.password_hash 등).
"""

from __future__ import annotations

from typing import Any

from followup.adapters.neo4j.driver import get_driver

EDGE_TYPES = ["HAS_AGENDA", "ABOUT", "HAS_DISCUSSION", "PRODUCED", "DECIDED", "ATTENDED", "ASSIGNED_TO",
              "FOLLOWS_UP", "SUPERSEDES"]

READ_GRAPH = """
MATCH (p:Project {project_id: $pid})<-[:IN]-(mt:Meeting) WHERE mt.meeting_id IN $mids
MATCH (mt)-[:HAS_AGENDA|ABOUT|HAS_DISCUSSION|PRODUCED|DECIDED*0..3]->(n)
WITH collect(DISTINCT n) AS ns
WITH ns + COLLECT { MATCH (u:Person)-[:ATTENDED|ASSIGNED_TO]->(x) WHERE x IN ns RETURN DISTINCT u } AS nodes
CALL (nodes) {
    UNWIND nodes AS a
    MATCH (a)-[r]->(b) WHERE type(r) IN $types AND b IN nodes
    RETURN collect({from: elementId(a), to: elementId(b), type: type(r)}) AS edges
}
RETURN [n IN nodes | {id: elementId(n), label: labels(n)[0], props: properties(n)}] AS nodes, edges"""


def read_graph(project_id: str, meeting_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    records = get_driver().execute_query(READ_GRAPH, pid=project_id, mids=meeting_ids, types=EDGE_TYPES).records
    return records[0].data() if records else {"nodes": [], "edges": []}
