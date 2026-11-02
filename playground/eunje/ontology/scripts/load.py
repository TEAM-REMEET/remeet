"""회의 JSON → Neo4j 적재.

실행 (ontology 폴더에서):
    uv run python scripts/load.py

현재 동작: 스키마 → 조직(users.json) → 회의(data/meetings/*.json, 파일 이름순)
  1) Meeting + 참석자
  2) Topic 매칭 (기존 연결 / 하위 안건 / 새 Topic)
  3) AgendaItem + ABOUT
  4) ActionItem + PRODUCED
  5) 담당자 결정      ← 주석 처리 (②단계에서 사용)
  6) Topic 요약 갱신  ← 주석 처리 (③단계에서 사용, LLM 필요)
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent  # = ontology/
load_dotenv(ROOT / ".env")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", os.environ["NEO4J_PASSWORD"]))

# Topic 매칭 임계값. 점수는 Neo4j cosine 점수(0~1)이고, 아래 값은 출발점일 뿐이에요.
# 적재 로그에 찍히는 score를 기대 결과와 비교해서 조정하세요.
SAME_TOPIC = 0.85     # 이상: 기존 Topic에 연결
RELATED_TOPIC = 0.75  # 이상: 새 Topic을 만들고 가장 가까운 Topic의 하위 안건(SUBTOPIC_OF)으로
                      # 미만: 새 Topic (최상위)

# ─────────────────────────────── 임베딩 ───────────────────────────────

_model = None


def embed(text: str) -> list[float]:
    """BGE-M3로 1024차원 벡터를 만든다. 첫 호출 때 모델을 내려받는다(약 2GB)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("BAAI/bge-m3")
    return _model.encode(text, normalize_embeddings=True).tolist()


# ─────────────────────────────── 스키마, 조직 ───────────────────────────────

def run_schema():
    text = (ROOT / "schema/schema.cypher").read_text(encoding="utf-8")
    for stmt in text.split(";"):
        body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("//")).strip()
        if body:
            driver.execute_query(body)


def load_users(data: dict):
    driver.execute_query("""
        UNWIND $rows AS r
        MERGE (n:Team {team_id: r.team_id})
        SET n.name = r.name, n.is_active = coalesce(r.is_active, true)
    """, rows=data["teams"])
    driver.execute_query("""
        UNWIND $rows AS r
        MERGE (n:Role {role_id: r.role_id}) SET n.name = r.name
    """, rows=data["roles"])
    driver.execute_query("""
        UNWIND $rows AS r
        MERGE (n:Skill {skill_id: r.skill_id}) SET n.name = r.name
    """, rows=data["skills"])
    driver.execute_query("""
        UNWIND $rows AS r
        MERGE (p:Person {user_id: r.user_id})
        SET p.name = r.name, p.is_active = r.is_active, p.position = r.position
        WITH p, r
        OPTIONAL MATCH (t:Team {team_id: r.team_id})
        FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | MERGE (p)-[:MEMBER_OF]->(t))
        WITH p, r
        OPTIONAL MATCH (ro:Role {role_id: r.role_id})
        FOREACH (_ IN CASE WHEN ro IS NULL THEN [] ELSE [1] END | MERGE (p)-[:HAS_ROLE]->(ro))
        WITH p, r
        UNWIND r.skills AS s
        MATCH (k:Skill {skill_id: s.skill_id})
        MERGE (p)-[h:HAS_SKILL]->(k)
        SET h.level = s.level, h.updated_at = datetime()
    """, rows=data["people"])


# ─────────────────────────────── 회의 적재 ───────────────────────────────

def load_meeting(m: dict):
    found = driver.execute_query(
        "MATCH (mt:Meeting {meeting_id: $mid}) RETURN count(mt) AS c", mid=m["meeting_id"]
    ).records[0]["c"]
    if found:
        print(f"[건너뜀] {m['meeting_id']} 이미 적재됨")
        return
    print(f"\n=== {m['meeting_id']} {m['title']} ===")
    with driver.session() as s:
        # 회의 1건 = 트랜잭션 1개. 중간에 실패하면 그 회의 전체가 저장되지 않아요.
        s.execute_write(_load_meeting_tx, m)


def _load_meeting_tx(tx, m: dict):
    # 1) Meeting + 참석자
    tx.run("""
        MERGE (p:Project {project_id: $pid})
        CREATE (mt:Meeting {meeting_id: $mid, title: $title, held_at: datetime($at)})-[:IN]->(p)
        WITH mt
        UNWIND $attendees AS uid
        MATCH (u:Person {user_id: uid})
        CREATE (u)-[:ATTENDED]->(mt)
    """, pid=m["project_id"], mid=m["meeting_id"], title=m["title"],
         at=m["held_at"], attendees=m["attendees"])

    for order, ag in enumerate(m["agendas"], 1):
        agenda_id = f"{m['meeting_id']}-a{order}"
        vec = embed(f"{ag['title']} {ag['background']}")

        # 2) Topic 매칭
        topic_id, score = link_topic(tx, m["project_id"], agenda_id, ag["title"], vec)

        # 3) AgendaItem + HAS_AGENDA + ABOUT
        tx.run("""
            MATCH (mt:Meeting {meeting_id: $mid}), (t:Topic {topic_id: $tid})
            CREATE (mt)-[:HAS_AGENDA]->(ai:AgendaItem {
                agenda_item_id: $aid, order: $order, title: $title,
                background: $background, discussion: $discussion,
                decisions: $decisions, next_topics: $next_topics, embedding: $vec
            })-[:ABOUT {confidence: $score, linked_by: 'auto'}]->(t)
            SET t.last_discussed_at = mt.held_at
        """, mid=m["meeting_id"], tid=topic_id, aid=agenda_id, order=order,
             title=ag["title"], background=ag["background"], discussion=ag["discussion"],
             decisions=ag["decisions"], next_topics=ag["next_topics"], vec=vec, score=score)

        # 4) ActionItem + PRODUCED
        actions = [
            {"id": f"{agenda_id}-t{j}", "task": a["task"], "due": a.get("due") or None}
            for j, a in enumerate(ag.get("actions", []), 1)
        ]
        tx.run("""
            MATCH (ai:AgendaItem {agenda_item_id: $aid})
            UNWIND $actions AS a
            CREATE (ai)-[:PRODUCED]->(:ActionItem {
                action_item_id: a.id, description: a.task, status: 'open', due: date(a.due)
            })
        """, aid=agenda_id, actions=actions)

        # 5) 담당자 결정 (②단계에서 주석 해제)
        # for a, src in zip(actions, ag.get("actions", [])):
        #     assign_owner(tx, a["id"], src.get("owner", ""), topic_id)

        # 6) Topic 누적 요약 갱신 (③단계에서 주석 해제, LLM 필요)
        # update_topic_summary(tx, topic_id, ag)


def link_topic(tx, project_id: str, agenda_id: str, title: str, vec: list[float]):
    """가장 가까운 Topic을 찾아 연결할 topic_id와 점수를 돌려줘요. 없으면 새로 만들어요."""
    # shortcut: 프로젝트의 Topic 전체와 직접 비교해요. Topic이 수천 개가 되면
    # db.index.vector.queryNodes('topic_embedding', ...)로 바꾸세요.
    hit = tx.run("""
        MATCH (t:Topic)-[:IN]->(:Project {project_id: $pid})
        WHERE t.status IN ['open', 'parked']
        RETURN t.topic_id AS topic_id, t.title AS title,
               vector.similarity.cosine(t.embedding, $vec) AS score
        ORDER BY score DESC LIMIT 1
    """, pid=project_id, vec=vec).single()

    if hit and hit["score"] >= SAME_TOPIC:
        print(f"  [기존 연결] {title}  →  {hit['title']}  (score {hit['score']:.3f})")
        return hit["topic_id"], hit["score"]

    # 새 Topic. 처음에는 summary가 없어서 이 AgendaItem의 벡터를 Topic 벡터로 써요.
    topic_id = f"tp-{agenda_id}"
    tx.run("""
        MATCH (p:Project {project_id: $pid})
        CREATE (:Topic {topic_id: $tid, title: $title, status: 'open', embedding: $vec})-[:IN]->(p)
    """, pid=project_id, tid=topic_id, title=title, vec=vec)

    if hit and hit["score"] >= RELATED_TOPIC:
        tx.run("""
            MATCH (c:Topic {topic_id: $child}), (p:Topic {topic_id: $parent})
            CREATE (c)-[:SUBTOPIC_OF]->(p)
        """, child=topic_id, parent=hit["topic_id"])
        print(f"  [하위 안건] {title}  ⊂  {hit['title']}  (score {hit['score']:.3f})")
    else:
        best = f"가장 가까움: {hit['title']} {hit['score']:.3f}" if hit else "비교 대상 없음"
        print(f"  [새 Topic]  {title}  ({best})")
    return topic_id, 1.0  # 새 Topic은 자기 자신과 연결이라 1.0


# ─────────────────────────── 5) 담당자 결정 (②단계) ───────────────────────────
#
# HITL_QUEUE = []  # 사람이 확인해야 할 건. 실제로는 RDB 대기열에 저장
#
# def assign_owner(tx, action_id: str, owner: str, topic_id: str):
#     owner = owner.strip()
#
#     # (1) 회의에서 담당자가 언급됨 → 이름이 정확히 한 명과 맞으면 explicit
#     if owner:
#         uids = tx.run("""
#             MATCH (p:Person {name: $name, is_active: true}) RETURN p.user_id AS uid
#         """, name=owner).value("uid")
#         if len(uids) == 1:
#             _assign(tx, uids[0], action_id, "explicit")
#             print(f"    [explicit] {action_id} → {owner}")
#         else:
#             # "결제 쪽", "김 매니저", "제가", 퇴사자 등 → 사람이 확인
#             HITL_QUEUE.append({"action_item_id": action_id, "owner_raw": owner, "candidates": uids})
#             print(f"    [HITL] {action_id} owner='{owner}' 후보 {len(uids)}명")
#         return
#
#     # (2) 언급 없음 → 같은 Topic의 가장 최근 담당자(활성 사용자)
#     prev = tx.run("""
#         MATCH (:Topic {topic_id: $tid})<-[:ABOUT]-(ai:AgendaItem)<-[:HAS_AGENDA]-(mt:Meeting),
#               (ai)-[:PRODUCED]->(past:ActionItem)<-[:ASSIGNED_TO]-(p:Person {is_active: true})
#         WHERE past.action_item_id <> $aid
#         RETURN p.user_id AS uid, p.name AS name
#         ORDER BY mt.held_at DESC LIMIT 1
#     """, tid=topic_id, aid=action_id).single()
#     if prev:
#         _assign(tx, prev["uid"], action_id, "history")
#         print(f"    [history] {action_id} → {prev['name']}")
#         return
#
#     # (3) 이력도 없음 → 추천 시스템
#     # recommend(tx, action_id)  # 기존 추천 시스템(RECOMMENDED_FOR 생성)을 여기서 호출
#     print(f"    [추천 필요] {action_id}")
#
#
# def _assign(tx, user_id: str, action_id: str, source: str):
#     tx.run("""
#         MATCH (p:Person {user_id: $uid}), (a:ActionItem {action_item_id: $aid})
#         CREATE (p)-[:ASSIGNED_TO {work_status: 'in_work', assign_source: $src}]->(a)
#     """, uid=user_id, aid=action_id, src=source)


# ─────────────────────── 6) Topic 누적 요약 갱신 (③단계) ───────────────────────
#
# def update_topic_summary(tx, topic_id: str, ag: dict):
#     t = tx.run("""
#         MATCH (t:Topic {topic_id: $tid}) RETURN t.title AS title, t.summary AS summary
#     """, tid=topic_id).single()
#     new_summary = summarize(t["summary"] or "", ag)
#     tx.run("""
#         MATCH (t:Topic {topic_id: $tid}) SET t.summary = $summary, t.embedding = $vec
#     """, tid=topic_id, summary=new_summary, vec=embed(f"{t['title']} {new_summary}"))
#
#
# def summarize(old_summary: str, ag: dict) -> str:
#     prompt = f"""다음은 한 안건의 지금까지 누적 요약과 이번 회의 내용이에요.
# 둘을 합쳐 현재 진행 상황을 3~5문장으로 다시 써 주세요. 기존 결정사항은 빠뜨리지 마세요.
#
# [기존 요약]
# {old_summary or "(없음)"}
#
# [이번 회의]
# 제목: {ag['title']}
# 배경: {ag['background']}
# 논의: {ag['discussion']}
# 결정: {'; '.join(ag['decisions'])}
# """
#     raise NotImplementedError("팀에서 쓰는 LLM API로 prompt를 보내고 응답 텍스트를 돌려주세요")


# 스키마 적용 (schema.cypher)
# 조직 정보 적재 (users.json)
# 회의 적재 (data/meetings/*.json)
def main():
    run_schema()
    load_users(json.loads((ROOT / "data/users.json").read_text(encoding="utf-8")))
    for f in sorted((ROOT / "data/meetings").glob("*.json")):
        load_meeting(json.loads(f.read_text(encoding="utf-8")))
    driver.close()


if __name__ == "__main__":
    main()