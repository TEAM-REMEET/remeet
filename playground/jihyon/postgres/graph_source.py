"""Neo4j 담당 데이터를 읽는 곳.

지금은 Neo4j 연결이 아직 없어서 목업 JSON(web/data/repository.py 의 목업 함수)을 그대로 돌려줍니다.
Neo4j 담당자가 이 파일의 함수 내용만 Cypher 쿼리로 바꾸면, PostgreSQL 쪽 코드는 손대지 않아도 됩니다.

    Neo4j 에서 오는 것
      people()         Person {user_id, name} + MEMBER_OF Team          → {user_id: Person}
      teams()          Team + MEMBER_OF + HAS_ROLE                     → 조직도 (공유 · 담당자 모달)
      assignee_config() RECOMMENDED_FOR · HAS_SKILL                    → 담당자 추천 후보
      graph_meetings() Meeting {title, held_at} + ATTENDED + Agenda · Discussion · ActionItem
                       → 회의 id 별 Meeting (PostgreSQL 이 관리 정보를 덧입힘)
      todo_done_map() / set_todo_done()   ActionItem.status
      personal_wiki() / folder_wiki()     위키 그래프

⚠️ web_patch.py 가 web 의 함수들을 PostgreSQL 버전으로 바꿔 끼우기 "전에" 이 파일이 import 되므로,
   여기서 잡아 둔 _json.xxx 는 항상 목업 버전입니다.
"""

from __future__ import annotations

import paths  # noqa: F401
from data import repository as _json  # web/data/repository.py (목업)

people = _json.people
people_by_short_name = _json.people_by_short_name
teams = _json.teams
assignee_config = _json.assignee_config
graph_meetings = _json._base_meetings   # 회의 id → Meeting (제목 · 날짜 · 참석자 · 안건 · 논의 · 결정 · 할 일)
personal_wiki = _json.personal_wiki
folder_wiki = _json.folder_wiki


def todo_done_map() -> dict[str, bool]:
    """완료된 할 일 {"m1:0": True}. Neo4j 로 바꾸면 예:
        MATCH (mt:Meeting)-[:HAS_AGENDA]->(:Agenda)-[:HAS_DISCUSSION]->(:Discussion)-[:PRODUCED]->(a:ActionItem {status: 'done'})
        RETURN mt.meeting_id, a.action_item_id
    """
    return {}


def set_todo_done(key: str, done: bool) -> None:
    """Neo4j 로 바꾸면 예:
        MATCH (a:ActionItem {action_item_id: $id}) SET a.status = CASE WHEN $done THEN 'done' ELSE 'open' END
    """
