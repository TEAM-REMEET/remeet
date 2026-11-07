"""회의에 물어보기 (온보딩 에이전트). 원본: playground/junbyong/onboarding_agent/onboarding_agent.py

  backfill  Agenda.decisions[] → Decision 노드 (DECIDED, ABOUT), 앞선 결정과 SUPERSEDES 판정(LLM)
  ask       decision / history / action / overview / people / skill 질문에 근거(source_id)가 달린 답변.
            근거 없으면 no_evidence

[ask 그래프] classify(LLM) → retrieve ─┬─▶ compose(LLM) → ground → cite → END
                                       └─▶ ground (근거 없음) → cite → END
  고른 Topic에서 근거·답이 안 나오면 widen(전체 Topic으로 1회 재조회) → compose → ground
LLM은 질문 해석과 문장화만 한다. Cypher는 고정 템플릿, grounding은 코드가 검사한다.

백엔드로 옮기며 바꾼 것
  - 원문 발화: 요약 결과 파일 위치를 settings.output_dir() 로, 화자 칸은 백엔드 요약 형식(speaker_display · speaker_job)도 읽음
  - 담당자 추천 결과: 추천 에이전트가 실제로 저장하는 속성(score · rank, 직급은 Person.position,
    스킬이 필요한 이유는 REQUIRES_SKILL.reason)으로 조회
  - CLI(main) · 평가(eval)는 원본에만 둠. 모델 이름은 settings(OPENAI_MODEL)
"""
from __future__ import annotations

from datetime import date, datetime
import functools
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

SCRIPT_DIR = Path(__file__).resolve().parent
PROMPTS = {name: (SCRIPT_DIR / f"prompts/{name}.md").read_text(encoding="utf-8")
           for name in ("supersede", "classify", "compose")}
INTENTS = ("decision", "history", "action", "overview", "people", "skill", "other")
RELATIONS = ("supersedes", "complements", "unrelated")
SUPERSEDE_MIN_CONFIDENCE = 0.7  # 미만이면 자동 반영하지 않고 HITL 후보로
ALLOWED_VISIBILITY = ["public"]  # shortcut: Meeting.visibility가 아직 없어 전부 public 취급, 팀 권한 생기면 질문자 기준으로
NO_EVIDENCE = "관련 회의 기록이 없어요. 담당자에게 문의해 주세요."
LLM_EFFORT = "low"  # 질문 해석 · 문장화만 하므로 낮게 (원본과 같은 값)
LLM_TIMEOUT = 60
LLM_RETRIES = 1

# ─────────────────────────────── 고정 쿼리 ───────────────────────────────

CONSTRAINT = "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.decision_id IS UNIQUE"
MEETINGS_QUERY = """
    MATCH (m:Meeting) RETURN m.meeting_id AS meeting_id, m.title AS title, toString(m.held_at) AS held_at
    ORDER BY held_at, meeting_id"""
TOPICS_QUERY = """
    MATCH (t:Topic) OPTIONAL MATCH (t)-[:SUBTOPIC_OF]->(p:Topic)
    RETURN t.topic_id AS topic_id, t.title AS title, p.topic_id AS parent_id ORDER BY topic_id"""
AGENDAS_QUERY = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:ABOUT]->(t:Topic)
    RETURN a.agenda_id AS agenda_id, a.title AS title, a.decisions AS decisions,
        t.topic_id AS topic_id, toString(m.held_at) AS held_at, EXISTS { (a)-[:DECIDED]->() } AS processed"""
DECISIONS_QUERY = """
    MATCH (a:Agenda)-[:DECIDED]->(d:Decision)-[:ABOUT]->(t:Topic)
    RETURN d.decision_id AS decision_id, d.content AS content, d.status AS status,
        toString(d.decided_at) AS decided_at, t.topic_id AS topic_id, a.agenda_id AS agenda_id"""
WRITE_DECISIONS = """
    UNWIND $rows AS r
    MATCH (a:Agenda {agenda_id: r.agenda_id}), (t:Topic {topic_id: r.topic_id})
    MERGE (d:Decision {decision_id: r.decision_id})
    ON CREATE SET d.content = r.content, d.decided_at = datetime(r.decided_at), d.status = 'active',
        d.confidence = r.confidence, d.verified_by = 'auto'
    MERGE (a)-[:DECIDED]->(d)
    MERGE (d)-[:ABOUT]->(t)"""
WRITE_SUPERSEDES = """
    UNWIND $rows AS r
    MATCH (n:Decision {decision_id: r.new_id}), (o:Decision {decision_id: r.old_id})
    MERGE (n)-[s:SUPERSEDES]->(o)
    SET s.reason = r.reason, s.confidence = r.confidence, s.detected_by = 'auto', o.status = 'superseded'"""
DECISION_EVIDENCE = """
    MATCH (d:Decision)-[:ABOUT]->(t:Topic) WHERE t.topic_id IN $topic_ids
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:DECIDED]->(d)
    WHERE coalesce(m.visibility, 'public') IN $visibility
    OPTIONAL MATCH (n:Decision)-[:SUPERSEDES]->(d)
    WITH d, a, m, head(collect(n {.decision_id, .content})) AS superseded_by
    OPTIONAL MATCH (a)-[:HAS_DISCUSSION]->(dn:Discussion)
    WITH d, a, m, superseded_by, collect(dn {.title, .discussions}) AS discussions
    RETURN d.decision_id AS source_id, 'decision' AS kind, d.content AS content, d.status AS status,
        toString(d.decided_at) AS decided_at, superseded_by, d.confidence AS confidence, d.verified_by AS verified_by,
        a.agenda_id AS agenda_id, a.title AS agenda_title, a.background AS background, discussions,
        a.unresolved_issues AS unresolved_issues, m.meeting_id AS meeting_id, m.title AS meeting_title,
        toString(m.held_at) AS held_at
    ORDER BY held_at DESC, source_id DESC LIMIT 40"""
HISTORY_EVIDENCE = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:ABOUT]->(t:Topic)
    WHERE coalesce(m.visibility, 'public') IN $visibility
      AND ($date_from IS NULL OR m.held_at >= datetime($date_from))
      AND ($date_to IS NULL OR m.held_at < datetime($date_to) + duration('P1D'))
      AND (size($topic_ids) = 0 OR t.topic_id IN $topic_ids)
      AND (size($meeting_ids) = 0 OR m.meeting_id IN $meeting_ids)
    OPTIONAL MATCH (a)-[:DECIDED]->(d:Decision)
    OPTIONAL MATCH (n:Decision)-[:SUPERSEDES]->(d)
    WITH m, a, collect(DISTINCT d {.decision_id, .content, .status, .confidence, .verified_by,
        decided_at: toString(d.decided_at), superseded_by: n {.decision_id, .content}}) AS decisions
    OPTIONAL MATCH (a)-[:HAS_DISCUSSION]->(dn:Discussion)
    OPTIONAL MATCH (dn)-[:PRODUCED]->(ai:ActionItem)
    RETURN a.agenda_id AS source_id, 'agenda' AS kind, a.agenda_id AS agenda_id,
        a.title AS agenda_title, a.background AS background, decisions,
        collect(DISTINCT dn.title) AS subtopics, collect(DISTINCT ai.description) AS actions,
        a.unresolved_issues AS unresolved_issues, a.next_topics AS next_topics,
        m.meeting_id AS meeting_id, m.title AS meeting_title,
        toString(m.held_at) AS held_at
    ORDER BY held_at, source_id LIMIT 30"""
# 확정 담당자(ASSIGNED_TO), 회의에서 말한 담당자 원문(owner_raw), 담당자 추천 에이전트의 pending 추천(RECOMMENDED_FOR).
ACTION_EVIDENCE = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:ABOUT]->(t:Topic)
    WHERE t.topic_id IN $topic_ids AND coalesce(m.visibility, 'public') IN $visibility
    MATCH (a)-[:HAS_DISCUSSION]->(dn:Discussion)-[:PRODUCED]->(ai:ActionItem)
    OPTIONAL MATCH (p:Person)-[asg:ASSIGNED_TO]->(ai)
    WITH m, a, dn, ai, [x IN collect(DISTINCT CASE WHEN p IS NULL THEN null
        ELSE {user_id: p.user_id, name: p.name, work_status: asg.work_status} END) WHERE x IS NOT NULL] AS assignees
    OPTIONAL MATCH (c:Person)-[r:RECOMMENDED_FOR {status: 'pending'}]->(ai)
    OPTIONAL MATCH (c)-[h:HAS_SKILL]->(s:Skill)
    WITH m, a, dn, ai, assignees, c, r, [x IN collect({name: s.name, level: h.level}) WHERE x.name IS NOT NULL] AS skills
    WITH m, a, dn, ai, assignees, [x IN collect(CASE WHEN c IS NULL THEN null
        ELSE {user_id: c.user_id, name: c.name, position: c.position, rank: r.rank,
              total: r.score, skills: skills} END) WHERE x IS NOT NULL] AS recommended
    RETURN ai.action_item_id AS source_id, 'action' AS kind, ai.description AS task, toString(ai.due) AS due,
        ai.status AS status, ai.owner_raw AS owner_raw, assignees, recommended, dn.title AS subtopic,
        a.agenda_id AS agenda_id, a.title AS agenda_title,
        m.meeting_id AS meeting_id, m.title AS meeting_title, toString(m.held_at) AS held_at
    ORDER BY held_at, source_id LIMIT 20"""
# 아래 3개(overview / people / skill)는 근거 회의를 refs 목록으로 돌려준다. ground가 refs로 출처를 만든다.
OVERVIEW_EVIDENCE = """
    MATCH (t:Topic) WHERE size($topic_ids) = 0 OR t.topic_id IN $topic_ids
    OPTIONAL MATCH (t)-[:IN]->(pr:Project)
    OPTIONAL MATCH (t)-[:SUBTOPIC_OF]->(pt:Topic)
    OPTIONAL MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:ABOUT]->(t)
    WHERE coalesce(m.visibility, 'public') IN $visibility
    OPTIONAL MATCH (a)-[:DECIDED]->(d:Decision {status: 'active'})
    WITH t, pr, pt, m, a, collect(d.content) AS decisions
    WITH t, pr, pt, [x IN collect(CASE WHEN a IS NULL THEN null ELSE {meeting_id: m.meeting_id,
        meeting_title: m.title, held_at: toString(m.held_at), agenda_id: a.agenda_id,
        agenda_title: a.title, current_decisions: decisions} END) WHERE x IS NOT NULL] AS refs
    RETURN t.topic_id AS source_id, 'topic' AS kind, t.title AS topic, t.status AS topic_status,
        toString(t.last_discussed_at) AS last_discussed_at, pt.title AS parent_topic,
        pr.project_id AS project_id, pr.name AS project, refs
    ORDER BY source_id"""
# shortcut: CONTRIBUTED_TO가 없어 "주제 관련자" = 그 주제 회의 참석자 + 그 주제 액션아이템 추천 후보, 발언 기록 생기면 교체
PEOPLE_EVIDENCE = """
    MATCH (p:Person) WHERE coalesce(p.is_active, true)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(tm:Team)
    OPTIONAL MATCH (p)-[:HAS_ROLE]->(ro:Role)
    OPTIONAL MATCH (p)-[h:HAS_SKILL]->(s:Skill)
    WITH p, collect(DISTINCT tm.name) AS teams, collect(DISTINCT ro.name) AS roles,
        [x IN collect(DISTINCT {name: s.name, level: h.level}) WHERE x.name IS NOT NULL] AS skills
    OPTIONAL MATCH (p)-[:ATTENDED]->(m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:ABOUT]->(t:Topic)
    WHERE t.topic_id IN $topic_ids AND coalesce(m.visibility, 'public') IN $visibility
    WITH p, teams, roles, skills, [x IN collect(DISTINCT CASE WHEN a IS NULL THEN null ELSE {
        meeting_id: m.meeting_id, meeting_title: m.title, held_at: toString(m.held_at),
        agenda_id: a.agenda_id, agenda_title: a.title} END) WHERE x IS NOT NULL] AS refs
    OPTIONAL MATCH (p)-[:RECOMMENDED_FOR {status: 'pending'}]->(ai:ActionItem)<-[:PRODUCED]-(:Discussion)<-[:HAS_DISCUSSION]-(:Agenda)-[:ABOUT]->(t2:Topic)
    WHERE t2.topic_id IN $topic_ids
    WITH p, teams, roles, skills, refs, collect(DISTINCT ai.description) AS recommended_tasks
    WHERE size($topic_ids) = 0 OR size(refs) > 0 OR size(recommended_tasks) > 0
    RETURN p.user_id AS source_id, 'person' AS kind, p.name AS name, p.position AS position, teams, roles,
        skills, recommended_tasks, refs
    ORDER BY size(refs) + size(recommended_tasks) DESC, source_id LIMIT 30"""
SKILL_EVIDENCE = """
    MATCH (p:Person)-[h:HAS_SKILL]->(s:Skill) WHERE coalesce(p.is_active, true)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(tm:Team)
    WITH s, p, h, collect(tm.name) AS teams
    OPTIONAL MATCH (p)-[r:RECOMMENDED_FOR {status: 'pending'}]->(ai:ActionItem)<-[:PRODUCED]-(:Discussion)<-[:HAS_DISCUSSION]-(a:Agenda)-[:ABOUT]->(t:Topic),
        (m:Meeting)-[:HAS_AGENDA]->(a)
    WHERE (size($topic_ids) = 0 OR t.topic_id IN $topic_ids) AND coalesce(m.visibility, 'public') IN $visibility
    OPTIONAL MATCH (ai)-[rq:REQUIRES_SKILL]->(s)  // 이 업무에 이 스킬이 필요한 이유 (추천 에이전트가 저장)
    WITH s, {name: p.name, position: p.position, level: h.level, teams: teams} AS holder,
        collect(CASE WHEN ai IS NULL THEN null ELSE {task: ai.description, reason: rq.reason, total: r.score,
            recommended: p.name, meeting_id: m.meeting_id, meeting_title: m.title, held_at: toString(m.held_at),
            agenda_id: a.agenda_id, agenda_title: a.title} END) AS used
    WITH s, collect(holder) AS holders, reduce(acc = [], u IN collect(used) | acc + u) AS uses
    // 같은 업무는 추천 후보 수만큼 반복되므로, 업무마다 총점 1순위 후보의 추천 이유 하나만 남긴다
    WITH s, holders, [task IN apoc.coll.toSet([u IN uses | u.task]) |
        apoc.coll.sortMaps([u IN uses WHERE u.task = task], 'total')[0]] AS refs
    WHERE size($topic_ids) = 0 OR size(refs) > 0
    RETURN s.skill_id AS source_id, 'skill' AS kind, s.name AS skill, holders, size(refs) AS task_count,
        apoc.coll.sortMaps(refs, 'total')[..5] AS refs
    ORDER BY task_count DESC, skill LIMIT 20"""  # 업무에 많이 쓰인 스킬 20개, 스킬당 업무 5개 (compose 입력 크기 제한)
# "더 물어보려면" 후보: 근거 안건 회의의 참석자 + 그 안건 할 일의 담당자·추천 후보. 고르는 건 compose(LLM), 검증은 코드.
# shortcut: 참석자만 후보라 회의에 없던 추천 후보는 빠짐, 발언 기록(CONTRIBUTED_TO) 생기면 그걸로 교체
CONTACTS_QUERY = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda) WHERE a.agenda_id IN $agenda_ids
    MATCH (p:Person)-[:ATTENDED]->(m) WHERE coalesce(p.is_active, true)
    OPTIONAL MATCH (p)-[:HAS_ROLE]->(r:Role)
    OPTIONAL MATCH (p)-[:RECOMMENDED_FOR|ASSIGNED_TO]->(ai:ActionItem)<-[:PRODUCED]-(:Discussion)<-[:HAS_DISCUSSION]-(a)
    RETURN p.user_id AS user_id, p.name AS name, p.position AS position, collect(DISTINCT r.name) AS roles,
        collect(DISTINCT a.agenda_id) AS agenda_ids, collect(DISTINCT ai.description) AS tasks
    ORDER BY user_id"""
# 원문 연결: 논의 문장(discussions[i])마다 근거 발화 번호(evidence[i] = "109,110,121")가 짝으로 있다
DISCUSSIONS_QUERY = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:Agenda)-[:HAS_DISCUSSION]->(dn:Discussion) WHERE a.agenda_id IN $agenda_ids
    RETURN m.meeting_id AS meeting_id, m.title AS meeting_title, a.agenda_id AS agenda_id,
        dn.discussion_id AS discussion_id, dn.title AS discussion_title, dn.discussions AS points, dn.evidence AS evidence
    ORDER BY agenda_id, dn.order"""
# shortcut: 발화 원문이 Neo4j에 없어 요약 결과 파일(settings.output_dir())에서 읽는다, Utterance 노드가 적재되면 쿼리로 교체


# ─────────────────────────────── 공통 ───────────────────────────────

def response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM이 빈 답변을 반환했습니다.")
    return content.strip()


@functools.lru_cache(maxsize=32)
def load_minutes(meeting_id: str) -> dict:
    """요약 파이프라인 결과 파일에서
    utterances: 발화 번호 → {i, speaker, role, text} (번호는 회의마다 1부터)
    items: 항목 ID(액션아이템 ID와 같음) → {text, utterances}. DB의 액션 전용 Discussion에는 발화 번호가 없어서 여기서 찾는다."""
    from followup import settings

    data = json.loads((settings.output_dir() / meeting_id / f"{meeting_id}_summary.json").read_text(encoding="utf-8"))
    items = {}

    def walk(o):
        if isinstance(o, dict):
            if o.get("item_id") and o.get("evidence_utterance_indices"):
                items.setdefault(o["item_id"], {"text": o.get("text"),
                                                "utterances": [int(n) for n in o["evidence_utterance_indices"]]})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data.get("agendas"))
    # 화자: playground 요약은 speaker · speaker_role, 백엔드 요약은 speaker_display(또는 speaker_name) · speaker_job
    return {"utterances": {u["utterance_index"]: {"i": u["utterance_index"],
                                                  "speaker": u.get("speaker") or u.get("speaker_display") or u.get("speaker_name"),
                                                  "role": u.get("speaker_role") or u.get("speaker_job"), "text": u.get("text")}
                           for u in data["transcript"]},
            "items": items}


def agenda_no(agenda_id: str) -> int | None:
    tail = (agenda_id or "").rsplit("_a", 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else None


def split_tags(point: str) -> dict:
    """'[제안][결정] 휠을 쓰기로 함.' → {tags: ['제안', '결정'], point: '휠을 쓰기로 함.'} (웹에서 라벨로 쓰게)"""
    head = re.match(r"(\[[^\]]*\]\s*)*", point or "").group(0)
    return {"tags": re.findall(r"\[([^\]]*)\]", head), "point": (point or "")[len(head):]}


def bigrams(text: str) -> set[str]:
    t = re.sub(r"\[[^\]]*\]|\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def mentions(text: str, content: str | None) -> bool:
    """문장이 그 내용을 말하는지: 내용의 2글자 조각 중 30% 이상이 문장에 있으면 말한 것으로 본다."""
    b = bigrams(content)
    return bool(b) and len(bigrams(text) & b) / len(b) >= 0.3


def call_json(llm, prompt: str, payload: dict) -> dict:
    response = llm.bind(response_format={"type": "json_object"}).invoke(
        [("system", prompt), ("human", json.dumps(payload, ensure_ascii=False, default=str))])
    out = json.loads(response_text(response))
    if not isinstance(out, dict):
        raise ValueError("LLM 결과가 JSON 객체가 아닙니다.")
    return out


def lineage(topics: list[dict], topic_ids) -> list[str]:
    """Topic + 상위 Topic + 하위 Topic 전부. 지금 DB에는 SUBTOPIC_OF가 없어 Topic 자기 자신만 나온다."""
    parent = {t["topic_id"]: t["parent_id"] for t in topics}
    out = set()
    for tid in topic_ids:
        while tid and tid not in out:  # 상위로
            out.add(tid)
            tid = parent.get(tid)
    frontier = set(out)
    while frontier:  # 하위로
        frontier = {c for c, p in parent.items() if p in frontier and c not in out}
        out |= frontier
    return sorted(out)


# ─────────────────────────────── 1) backfill ───────────────────────────────

def judge(llm, new: dict, candidates: list[dict]) -> list[dict]:
    """LLM 판정 중 candidates에 있는 ID, 정해진 relation만 남긴다."""
    out = call_json(llm, PROMPTS["supersede"], {
        "new_decision": {k: new[k] for k in ("decision_id", "content", "decided_at")},
        "candidates": [{k: c[k] for k in ("decision_id", "content", "decided_at")} for c in candidates]})
    known = {c["decision_id"] for c in candidates}
    kept = []
    for j in out.get("judgements") or []:
        if isinstance(j, dict) and j.get("old_decision_id") in known and j.get("relation") in RELATIONS:
            try:
                confidence = min(max(float(j.get("confidence", 0)), 0.0), 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            kept.append({"old_id": j["old_decision_id"], "relation": j["relation"],
                         "reason": str(j.get("reason") or ""), "confidence": confidence})
    return kept


def backfill(tools: dict, llm, write: bool) -> dict:
    """회의 시간순으로 decisions[]를 Decision으로 승격. 이미 처리된 안건은 건너뛰어 재실행해도 안전하다."""
    topics = tools["topics"]()
    pool = tools["decisions"]()  # 이미 적재된 Decision도 비교 대상
    new, supersedes, hitl = [], [], []
    for ag in sorted(tools["agendas"](), key=lambda a: (a["held_at"], a["agenda_id"])):
        if ag["processed"]:
            continue
        family = set(lineage(topics, [ag["topic_id"]]))
        for i, content in enumerate(ag["decisions"] or [], 1):
            d = {"decision_id": f"{ag['agenda_id']}-d{i}", "content": content, "decided_at": ag["held_at"],
                 "status": "active", "topic_id": ag["topic_id"], "agenda_id": ag["agenda_id"],
                 "confidence": 1.0}  # decisions[]는 요약 단계에서 확정 결정으로 뽑힌 값
            candidates = [p for p in pool if p["status"] == "active" and p["topic_id"] in family
                          and p["agenda_id"] != ag["agenda_id"]]
            if candidates:
                try:
                    judgements = judge(llm, d, candidates)
                except Exception as exc:
                    hitl.append({"new_id": d["decision_id"], "reason": f"판정 실패 ({type(exc).__name__})"})
                    judgements = []
                for j in judgements:
                    if j["relation"] != "supersedes":
                        continue
                    row = {"new_id": d["decision_id"], **j}
                    if j["confidence"] >= SUPERSEDE_MIN_CONFIDENCE:
                        supersedes.append(row)
                        next(p for p in pool if p["decision_id"] == j["old_id"])["status"] = "superseded"
                    else:
                        hitl.append(row)
            pool.append(d)
            new.append(d)
    if write and new:
        tools["write"](new, supersedes)
    return {"new": new, "supersedes": supersedes, "hitl": hitl, "written": write and bool(new)}


# ─────────────────────────────── 2) ask 그래프 ───────────────────────────────

class AskState(TypedDict, total=False):
    question: str
    today: str
    topics: list[dict]
    intent: str
    topic_ids: list[str]
    date_from: str | None
    date_to: str | None
    meetings: list[dict]
    meeting_ids: list[str]
    evidence: list[dict]
    sentences: list[dict]
    people: list[dict]
    contacts: list[dict]
    follow_ups: list[dict]
    widened: bool
    compose_error: bool
    warnings: list[str]
    answer: dict


def as_date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat() if value else None
    except ValueError:
        return None


def ground(s: AskState) -> dict:
    """문장마다 source_ids가 실제 조회 결과 안에 있는지 코드로 검사. 근거 없는 문장은 버린다."""
    evidence = s.get("evidence") or []
    by_id = {e["source_id"]: e for e in evidence}
    sentences = s.get("sentences") or []
    kept = [x for x in sentences if x["source_ids"] and set(x["source_ids"]) <= by_id.keys()]
    dropped = len(sentences) - len(kept)
    warnings = list(s.get("warnings") or [])
    if dropped:
        warnings.append(f"근거가 확인되지 않은 문장 {dropped}개를 뺐습니다.")
    used = list(dict.fromkeys(i for x in kept for i in x["source_ids"]))

    # decision_id → (결정, 근거 source_id). 문장이 그 결정 내용을 실제로 말했을 때만 결정 이력·배지에 넣는다.
    # 안건만 인용한 답("1차 회의 주제는 …")에 결정이 전부 딸려 나오거나, LLM이 같은 안건의 엉뚱한 결정 ID를
    # 단 경우("가격은 25유로" ← "회의록을 올리기로 함")에 그 결정이 이력에 뜨지 않게 한다
    decisions = {}
    for x in kept:
        for sid in x["source_ids"]:
            e = by_id[sid]
            for d in ([e] if e["kind"] == "decision" else e.get("decisions") or []):
                if mentions(x["text"], d.get("content")):
                    decisions.setdefault(d.get("decision_id") or d["source_id"], (d, sid))
    current = [d for d, _ in decisions.values() if d["status"] == "active"]
    history = [d for d, _ in decisions.values() if d["status"] == "superseded"]
    for d, sid in decisions.values():  # superseded 경고는 LLM이 아니라 코드가 붙인다
        if d["status"] == "superseded":
            newer = (d.get("superseded_by") or {}).get("content")
            kept.append({"text": f"참고: '{d['content']}' 결정은 이후 " + (f"'{newer}'(으)로 " if newer else "")
                                 + "변경됐어요.", "source_ids": [sid]})

    if not kept:
        status = "error" if "답변 생성 실패" in " ".join(warnings) else "no_evidence"
    else:
        status = "partial" if dropped else "grounded"
    order = {m["meeting_id"]: i for i, m in enumerate(s.get("meetings") or [], 1)}  # 날짜가 같아도 회의 차수로 정렬
    refs = [r for sid in used for r in by_id[sid].get("refs", [by_id[sid]])]  # refs: 여러 회의를 근거로 가진 행
    sources = list({r["agenda_id"]: {**{k: r[k] for k in (
        "meeting_id", "meeting_title", "held_at", "agenda_id", "agenda_title")},
        "meeting_order": order.get(r["meeting_id"]), "agenda_no": agenda_no(r["agenda_id"])} for r in refs}.values())
    pick = lambda d: {"decision_id": d.get("decision_id") or d["source_id"],
                      **{k: d.get(k) for k in ("content", "status", "decided_at")}}
    # 화면의 "결정 이력": 결정 + 그 결정이 나온 회의. 회의 정보는 근거 행(e)에서 가져온다
    timeline = sorted(({
        **pick(d), "decided_at": str(d.get("decided_at") or e.get("held_at") or "")[:10] or None,
        "superseded_by": (d.get("superseded_by") or {}).get("decision_id"),
        "confidence": d.get("confidence"), "verified_by": d.get("verified_by"),
        "meeting_id": e.get("meeting_id"), "meeting_title": e.get("meeting_title"),
        "meeting_order": order.get(e.get("meeting_id")),
        "agenda_id": e.get("agenda_id"), "agenda_no": agenda_no(e.get("agenda_id") or "")}
        for d, sid in decisions.values() for e in [by_id[sid]]),
        key=lambda x: (x["meeting_order"] or 0, x["decision_id"]))
    confidences = [x["confidence"] for x in timeline if x["confidence"] is not None]
    badge = {"status": "active" if current else "superseded",
             "confidence": min(confidences) if confidences else None,
             # 사람이 전부 확인했을 때만 human. 지금 DB는 전부 auto
             "verified_by": "human" if all(x["verified_by"] == "human" for x in timeline) else "auto",
             } if timeline else None
    people = {p["user_id"]: p for p in s.get("people") or []}
    # 근거 안건에 할 일이 없는 사람(참석만 한 사람)을 LLM이 "담당"이라고 쓰면 "참석"으로 바로잡는다
    contacts = [{**{k: people[c["user_id"]][k] for k in ("user_id", "name", "position", "roles")},
                 "why": c["why"] if people[c["user_id"]].get("tasks") else c["why"].replace("담당", "참석")}
                for c in s.get("contacts") or [] if c["user_id"] in people] if kept else []
    follow_ups = [f["text"] for f in s.get("follow_ups") or []
                  if f["source_ids"] and set(f["source_ids"]) <= by_id.keys()] if kept else []
    return {"answer": {
        "question": s["question"], "intent": s.get("intent"),
        "slots": {"topic_ids": s.get("topic_ids", []), "meeting_ids": s.get("meeting_ids", []),
                  "date_from": s.get("date_from"), "date_to": s.get("date_to")},
        "answer_status": status,
        "summary": " ".join(x["text"] for x in kept) if kept else NO_EVIDENCE,
        "sentences": kept,
        "badge": badge,
        "decision_timeline": timeline,
        "current_decisions": [pick(d) for d in current],
        "decision_history": [pick(d) for d in history],
        "sources": sources,
        "contacts": contacts,
        "follow_ups": follow_ups,
        "warnings": warnings,
    }}


def build_ask_graph(tools: dict, llm):
    def classify(s: AskState) -> dict:
        try:
            out = call_json(llm, PROMPTS["classify"], {
                "question": s["question"], "today": s["today"],
                "topics": [{"topic_id": t["topic_id"], "title": t["title"]} for t in s["topics"]],
                "meetings": [{"order": i, **m} for i, m in enumerate(s["meetings"], 1)]})
        except Exception as exc:
            return {"intent": "other", "topic_ids": [], "meeting_ids": [],
                    "warnings": [f"질문 해석 실패 ({type(exc).__name__})"]}
        known = {t["topic_id"] for t in s["topics"]}
        known_meetings = {m["meeting_id"] for m in s["meetings"]}
        return {"intent": out.get("intent") if out.get("intent") in INTENTS else "other",
                "topic_ids": [t for t in out.get("topic_ids") or [] if t in known],
                "meeting_ids": [m for m in out.get("meeting_ids") or [] if m in known_meetings],
                "date_from": as_date(out.get("date_from")), "date_to": as_date(out.get("date_to")),
                "warnings": []}

    def retrieve(s: AskState) -> dict:
        family = lineage(s["topics"], s["topic_ids"])
        try:
            # 주제 없는 질문("지금까지 정해진 것", "아직 안 정해진 것")은 전체 회의에서 찾는다
            if s["intent"] == "decision":
                return {"evidence": tools["decision_evidence"](family or [t["topic_id"] for t in s["topics"]])}
            if s["intent"] == "history":
                # 회의를 지정했으면 주제로 좁히지 않고 그 회의 안건 전체를 본다 ("2차 회의에서 뭘 정했어요?"가
                # 고른 주제와 겹치는 안건만 보던 문제). 회의 하나의 안건은 몇 개뿐이라 compose가 질문에 맞게 고른다
                meeting_ids = s.get("meeting_ids") or []
                return {"evidence": tools["history_evidence"](s.get("date_from"), s.get("date_to"),
                                                              [] if meeting_ids else family, meeting_ids)}
            if s["intent"] == "action":  # 주제 없는 질문("지금 누가 뭘 하고 있어요?")은 전체 액션아이템에서 찾는다
                # shortcut: 쿼리 LIMIT 20이라 액션아이템이 많아지면 일부만 보임, 그때 status·기간 필터 추가
                return {"evidence": tools["action_evidence"](family or [t["topic_id"] for t in s["topics"]])}
            if s["intent"] in ("overview", "people", "skill"):  # 주제가 없으면 전체(빈 목록)로 찾는다
                return {"evidence": tools[f"{s['intent']}_evidence"](family)}
        except Exception as exc:
            return {"evidence": [], "warnings": s["warnings"] + [f"근거 조회 실패 ({type(exc).__name__})"]}
        return {"evidence": []}

    def widen(s: AskState) -> dict:
        """classify가 Topic을 잘못 고르면 근거가 엇나간다. Topic 제한 없이 한 번만 더 찾는다."""
        out = retrieve({**s, "topic_ids": [t["topic_id"] for t in s["topics"]]})
        return {"evidence": out["evidence"], "widened": True, "sentences": [],
                "warnings": out.get("warnings", s["warnings"]) + ["질문한 주제에서 못 찾아 전체 회의에서 다시 찾았어요."]}

    def compose(s: AskState) -> dict:
        agenda_ids = sorted({r["agenda_id"] for e in s["evidence"] for r in e.get("refs", [e]) if r.get("agenda_id")})
        warnings = s["warnings"]
        try:
            people = tools["contacts"](agenda_ids) if agenda_ids else []
        except Exception as exc:  # 담당자 후보는 부가 정보라 실패해도 답변은 낸다
            people, warnings = [], warnings + [f"문의 대상 조회 실패 ({type(exc).__name__})"]
        try:
            out = call_json(llm, PROMPTS["compose"], {"question": s["question"], "intent": s["intent"],
                                                      "evidence": s["evidence"], "people": people})
        except Exception as exc:
            return {"sentences": [], "compose_error": True,
                    "warnings": warnings + [f"답변 생성 실패 ({type(exc).__name__})"]}
        cited = lambda xs: [{"text": str(x.get("text") or "").strip(),
                             "source_ids": [str(i) for i in x.get("source_ids") or []]}
                            for x in xs or [] if isinstance(x, dict) and str(x.get("text") or "").strip()]
        contacts = [{"user_id": str(c.get("user_id")), "why": str(c.get("why") or "").strip()}
                    for c in out.get("contacts") or [] if isinstance(c, dict)][:2]
        return {"sentences": cited(out.get("sentences")), "people": people, "contacts": contacts,
                "follow_ups": cited(out.get("follow_ups"))[:2], "warnings": warnings}

    def cite(s: AskState) -> dict:
        """문장마다 원문 근거(citations: 논의 문장 + 발화 번호)를 붙이고, 인용된 발화 원문만 transcripts에 담는다.
        문장의 근거 안건에 속한 논의 문장 중 문장과 글자가 많이 겹치는 것 2개를 고른다 (LLM 호출 없음)."""
        a = s["answer"]
        by_id = {e["source_id"]: e for e in s.get("evidence") or []}
        agendas = lambda st: {r["agenda_id"] for sid in st["source_ids"] if sid in by_id
                              for r in by_id[sid].get("refs", [by_id[sid]]) if r.get("agenda_id")}
        wanted = sorted({x for st in a["sentences"] for x in agendas(st)})
        warnings = a["warnings"]
        try:
            rows = tools["discussions"](wanted) if wanted else []
        except Exception as exc:  # 원문 연결은 부가 정보라 실패해도 답변은 낸다
            rows, warnings = [], warnings + [f"원문 근거 조회 실패 ({type(exc).__name__})"]
        points = [{**{k: r[k] for k in ("meeting_id", "meeting_title", "agenda_id", "discussion_id", "discussion_title")},
                   "agenda_no": agenda_no(r["agenda_id"]), **split_tags(p),
                   "utterances": [int(x) for x in str(ev).split(",") if x.strip().isdigit()]}
                  for r in rows for p, ev in zip(r["points"] or [], r["evidence"] or [])]
        loaded = {}

        def minutes(mid):  # 회의마다 한 번만 읽고, 실패하면 경고만 남긴다
            nonlocal warnings
            if mid not in loaded:
                try:
                    loaded[mid] = tools["minutes"](mid)
                except (OSError, KeyError, ValueError) as exc:
                    loaded[mid] = None
                    warnings = warnings + [f"{mid} 원문을 읽지 못했어요 ({type(exc).__name__})"]
            return loaded[mid]

        sentences = []
        for st in a["sentences"]:
            own = []  # 액션아이템은 자기 발화 번호가 원문 파일에 있다 (DB의 액션 전용 Discussion에는 없음)
            for sid in st["source_ids"]:
                e = by_id.get(sid) or {}
                item = e.get("kind") == "action" and ((minutes(e["meeting_id"]) or {}).get("items") or {}).get(sid)
                if item:
                    own.append({"meeting_id": e["meeting_id"], "meeting_title": e.get("meeting_title"),
                                "agenda_id": e["agenda_id"], "discussion_id": None,
                                "discussion_title": e.get("subtopic"), "agenda_no": agenda_no(e["agenda_id"]),
                                "tags": ["할 일"], "point": item["text"], "utterances": item["utterances"]})
            mine, words = agendas(st), bigrams(st["text"])
            ranked = sorted((p for p in points if p["agenda_id"] in mine and p["utterances"]),
                            key=lambda p: -len(words & bigrams(p["point"])))
            # 인용한 안건마다 가장 맞는 논의 1개씩은 꼭 넣고(안건이 빠지지 않게), 모자라면 다음 순위로 2개까지 채운다
            picked, covered = list(own), {c["agenda_id"] for c in own}
            for p in ranked:
                if p["agenda_id"] not in covered:
                    picked.append(p)
                    covered.add(p["agenda_id"])
            picked += [p for p in ranked if p not in picked][:max(0, 2 - len(picked))]
            sentences.append({**st, "citations": picked})
        transcripts = {}
        for mid in sorted({c["meeting_id"] for st in sentences for c in st["citations"]}):
            nums = sorted({n for st in sentences for c in st["citations"] if c["meeting_id"] == mid for n in c["utterances"]})
            utts = (minutes(mid) or {}).get("utterances")
            if utts:
                transcripts[mid] = [utts[n] for n in nums if n in utts]
        return {"answer": {**a, "sentences": sentences, "transcripts": transcripts, "warnings": warnings}}

    can_widen = lambda s: bool(s["topic_ids"]) and not s.get("widened") and s["intent"] != "other"
    after_retrieve = lambda s: "compose" if s["evidence"] else "widen" if can_widen(s) else "ground"
    after_compose = lambda s: "widen" if not s["sentences"] and not s.get("compose_error") and can_widen(s) else "ground"

    g = StateGraph(AskState)
    for name, node in [("classify", classify), ("retrieve", retrieve), ("widen", widen), ("compose", compose),
                       ("ground", ground), ("cite", cite)]:
        g.add_node(name, node)
    g.add_edge(START, "classify")
    g.add_edge("classify", "retrieve")
    g.add_conditional_edges("retrieve", after_retrieve, ["compose", "widen", "ground"])
    g.add_conditional_edges("widen", after_retrieve, ["compose", "ground"])
    g.add_conditional_edges("compose", after_compose, ["widen", "ground"])
    g.add_edge("ground", "cite")
    g.add_edge("cite", END)
    return g.compile()


def ask(graph, tools: dict, question: str, today: str | None = None) -> dict:
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return graph.invoke({"question": question, "today": today, "topics": tools["topics"](),
                         "meetings": tools["meetings"]()})["answer"]


# ─────────────────────────────── Neo4j ───────────────────────────────

def neo4j_tools(driver) -> dict:
    def q(query, **params):
        return [r.data() for r in driver.execute_query(query, params, routing_="r").records]

    def write_tx(tx, decisions, supersedes):
        tx.run(WRITE_DECISIONS, rows=decisions).consume()
        tx.run(WRITE_SUPERSEDES, rows=supersedes).consume()

    def write(decisions, supersedes):
        driver.execute_query(CONSTRAINT)
        with driver.session() as session:
            session.execute_write(write_tx, decisions, supersedes)

    return {
        "topics": lambda: q(TOPICS_QUERY),
        "meetings": lambda: q(MEETINGS_QUERY),
        "agendas": lambda: q(AGENDAS_QUERY),
        "decisions": lambda: q(DECISIONS_QUERY),
        "write": write,
        "decision_evidence": lambda topic_ids: q(DECISION_EVIDENCE, topic_ids=topic_ids,
                                                 visibility=ALLOWED_VISIBILITY),
        "history_evidence": lambda date_from, date_to, topic_ids, meeting_ids: q(
            HISTORY_EVIDENCE, date_from=date_from, date_to=date_to, topic_ids=topic_ids, meeting_ids=meeting_ids,
            visibility=ALLOWED_VISIBILITY),
        "action_evidence": lambda topic_ids: q(ACTION_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "overview_evidence": lambda topic_ids: q(OVERVIEW_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "people_evidence": lambda topic_ids: q(PEOPLE_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "skill_evidence": lambda topic_ids: q(SKILL_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "contacts": lambda agenda_ids: q(CONTACTS_QUERY, agenda_ids=agenda_ids),
        "discussions": lambda agenda_ids: q(DISCUSSIONS_QUERY, agenda_ids=agenda_ids),
        "minutes": load_minutes,
    }


# ─────────────────────────────── self-check ───────────────────────────────

class FakeLLM:
    """system 프롬프트로 노드를 구분해 정해 둔 답을 돌려준다."""

    def __init__(self, handlers: dict):
        self.handlers = handlers

    def bind(self, **_):
        return self

    def invoke(self, messages):
        name = next(k for k, v in PROMPTS.items() if v == messages[0][1])
        return SimpleNamespace(content=json.dumps(self.handlers[name](json.loads(messages[1][1]))))


def self_check() -> None:
    topics = [{"topic_id": "T1", "title": "프로모션", "parent_id": None},
              {"topic_id": "T2", "title": "프로모션 재개", "parent_id": "T1"},
              {"topic_id": "T3", "title": "결제", "parent_id": None}]
    assert lineage(topics, ["T2"]) == ["T1", "T2"] and lineage(topics, ["T1"]) == ["T1", "T2"]
    agendas = [{"agenda_id": "A1", "topic_id": "T1", "held_at": "2026-11-06", "decisions": ["보류"], "processed": False},
               {"agenda_id": "A2", "topic_id": "T2", "held_at": "2026-11-10", "decisions": ["재개"], "processed": False},
               {"agenda_id": "A3", "topic_id": "T3", "held_at": "2026-11-01", "decisions": ["원인 파악"], "processed": False},
               {"agenda_id": "A4", "topic_id": "T1", "held_at": "2026-11-12", "decisions": ["애매"], "processed": False}]

    def supersede(p):
        ids = [c["decision_id"] for c in p["candidates"]]
        if p["new_decision"]["content"] == "재개":
            return {"judgements": [{"old_decision_id": "A1-d1", "relation": "supersedes", "reason": "보류→재개", "confidence": 0.9},
                                   {"old_decision_id": "ZZ", "relation": "supersedes", "confidence": 1}]}  # 없는 ID는 버려야 함
        return {"judgements": [{"old_decision_id": i, "relation": "supersedes", "confidence": 0.5} for i in ids]}

    written = []
    tools = {"topics": lambda: topics, "agendas": lambda: agendas, "decisions": lambda: [],
             "write": lambda d, s: written.append((d, s))}
    llm = FakeLLM({"supersede": supersede})
    dry = backfill(tools, llm, write=False)
    assert not written and not dry["written"], "dry-run은 DB에 쓰지 않는다"
    assert [d["decision_id"] for d in dry["new"]] == ["A3-d1", "A1-d1", "A2-d1", "A4-d1"], "회의 시간순"
    assert [(s["new_id"], s["old_id"]) for s in dry["supersedes"]] == [("A2-d1", "A1-d1")]
    assert [h["old_id"] for h in dry["hitl"]] == ["A2-d1"], "낮은 confidence는 HITL로, 이미 superseded된 A1-d1은 후보에서 빠짐"
    assert backfill(tools, llm, write=True)["written"] and len(written) == 1
    done = dict(tools, agendas=lambda: [{**a, "processed": True} for a in agendas])
    assert backfill(done, llm, write=True)["new"] == [] and len(written) == 1, "재실행하면 새로 쓰는 것이 없다"

    a1 = {"source_id": "A1-d1", "kind": "decision", "content": "보류", "status": "superseded", "decided_at": "2026-11-06",
          "superseded_by": {"decision_id": "A2-d1", "content": "재개"}, "agenda_id": "A1", "agenda_title": "일정",
          "meeting_id": "M1", "meeting_title": "회의1", "held_at": "2026-11-06"}
    a2 = {**a1, "source_id": "A2-d1", "content": "재개", "status": "active", "superseded_by": None,
          "agenda_id": "A2", "meeting_id": "M2", "held_at": "2026-11-10"}
    calls = []

    def minutes(mid):
        if mid != "M2":
            raise OSError("원문 파일 없음")
        return {"utterances": {n: {"i": n, "speaker": "A", "role": "PM", "text": f"발화{n}"} for n in (5, 6, 9)},
                "items": {"A3-t1": {"text": "원인 파악하기로 함.", "utterances": [9]}}}

    ask_tools = {"topics": lambda: topics,
                 "discussions": lambda aids: [{"meeting_id": "M2" if a == "A2" else "M1", "meeting_title": "회의",
                                               "agenda_id": a, "discussion_id": f"{a}_st1", "discussion_title": "일정",
                                               "points": ["[정보] 점심 메뉴 공유", "[제안][결정] 프로모션을 재개하기로 함."],
                                               "evidence": ["9", "5,6"]} for a in aids],
                 "minutes": minutes,
                 "meetings": lambda: [{"meeting_id": "M1", "title": "1차 회의", "held_at": "2026-11-06"},
                                      {"meeting_id": "M2", "title": "2차 회의", "held_at": "2026-11-10"}],
                 "decision_evidence": lambda ids: calls.append(("decision", ids)) or ([] if ids == ["T3"] else [a1, a2]),
                 "history_evidence": lambda f, t, ids, mids: calls.append(("history", f, t, ids, mids)) or [
                     {**a1, "source_id": "A1", "kind": "agenda", "decisions": [
                         {"decision_id": "A1-d1", "content": "보류", "status": "superseded",
                          "superseded_by": {"decision_id": "A2-d1", "content": "재개"}}]},
                     {**a2, "source_id": "A2", "kind": "agenda", "decisions": [
                         {"decision_id": "A2-d9", "content": "결제 수단을 추가하기로 함.", "status": "active"}]}],
                 "action_evidence": lambda ids: calls.append(("action", ids)) or [
                     {**a1, "source_id": "A3-t1", "kind": "action", "task": "원인 파악", "due": "2026-10-27",
                      "agenda_id": "A3", "meeting_id": "M2", "assignees": [],
                      "recommended": [{"user_id": "u1", "name": "김결제", "slot": "senior", "skills": []}]}],
                 "people_evidence": lambda ids: calls.append(("people", ids)) or [
                     {"source_id": "u1", "kind": "person", "name": "김결제", "refs": [
                         {k: a1[k] for k in ("meeting_id", "meeting_title", "held_at", "agenda_id", "agenda_title")}]}],
                 "skill_evidence": lambda ids: calls.append(("skill", ids)) or [
                     {"source_id": "s1", "kind": "skill", "skill": "Figma", "holders": [], "refs": []}],
                 "overview_evidence": lambda ids: calls.append(("overview", ids)) or [],
                 "contacts": lambda aids: [{"user_id": "u1", "name": "김결제", "position": "senior",
                                            "roles": ["PM"], "agenda_ids": aids, "tasks": []}]}
    classify = {"프로모션 어떻게 됐어?": {"intent": "decision", "topic_ids": ["T2", "NOPE"]},
                "원인 파악 담당자?": {"intent": "action", "topic_ids": ["T3"]},
                "11월 진행 내용": {"intent": "history", "topic_ids": [], "date_from": "2026-11-01", "date_to": "2026-11-30"},
                "복지 포인트?": {"intent": "other", "topic_ids": []},
                "주제 모를 결정": {"intent": "decision", "topic_ids": []},
                "프로모션 누구한테 물어봐?": {"intent": "people", "topic_ids": ["T1"]},
                "신입 스킬?": {"intent": "skill", "topic_ids": []},
                "엉뚱한 주제로 고른 결정": {"intent": "decision", "topic_ids": ["T3"]},
                "1차 회의에서 뭐 했어?": {"intent": "history", "topic_ids": [], "meeting_ids": ["M1", "NOPE"]},
                "1차 회의 주제?": {"intent": "history", "topic_ids": ["T3"], "meeting_ids": ["M1"]}}
    compose_calls = []

    def compose(p):
        compose_calls.append(p["question"])
        if p["question"] == "1차 회의 주제?":  # 안건 2개를 인용하지만 결정 내용은 말하지 않는 답
            return {"sentences": [{"text": "주제는 일정과 결제였어요.", "source_ids": ["A1", "A2"]}],
                    "contacts": [{"user_id": "u1", "why": "PM · 안건 담당"}]}
        return {"sentences": [{"text": "프로모션은 재개됐어요.", "source_ids": ["A2-d1"]},
                              {"text": "지어낸 문장", "source_ids": ["BAD"]},
                              {"text": "근거 없는 문장", "source_ids": []}],
                "contacts": [{"user_id": "u1", "why": "PM"}, {"user_id": "GHOST", "why": "지어낸 사람"}],
                "follow_ups": [{"text": "재개 일정은요?", "source_ids": ["A2-d1"]},
                               {"text": "근거 없는 질문?", "source_ids": ["BAD"]}]} if p["intent"] == "decision" else \
            {"sentences": [{"text": "담당자는 미확정이고 김결제가 추천 후보예요.", "source_ids": ["A3-t1"]}]} \
            if p["intent"] == "action" else {"sentences": [{"text": "김결제에게 물어보세요.", "source_ids": ["u1"]}]} \
            if p["intent"] == "people" else {"sentences": [{"text": "Figma가 쓰여요.", "source_ids": ["s1"]}]} \
            if p["intent"] == "skill" else {"sentences": [{"text": "11/6에 보류했어요.", "source_ids": ["A1"]}]}

    graph = build_ask_graph(ask_tools, FakeLLM({"classify": lambda p: classify[p["question"]], "compose": compose}))
    d = ask(graph, ask_tools, "프로모션 어떻게 됐어?", "2026-11-03")
    assert calls[0] == ("decision", ["T1", "T2"]), "없는 topic_id는 버리고 계열(상위 포함)로 조회"
    assert d["answer_status"] == "partial" and [x["text"] for x in d["sentences"]] == ["프로모션은 재개됐어요."]
    assert [x["decision_id"] for x in d["current_decisions"]] == ["A2-d1"] and d["sources"][0]["agenda_id"] == "A2"
    assert [c["user_id"] for c in d["contacts"]] == ["u1"] and d["contacts"][0]["roles"] == ["PM"], "후보에 없는 사람은 버린다"
    assert d["follow_ups"] == ["재개 일정은요?"], "근거 없는 후속 질문은 버린다"
    assert d["decision_timeline"][0]["meeting_order"] == 2 and d["sources"][0]["meeting_order"] == 2
    assert d["badge"] == {"status": "active", "confidence": None, "verified_by": "auto"}
    c0 = d["sentences"][0]["citations"][0]
    assert c0["point"] == "프로모션을 재개하기로 함." and c0["tags"] == ["제안", "결정"] and c0["utterances"] == [5, 6], \
        "문장과 많이 겹치는 논의 문장을 먼저, 태그는 떼고"
    assert [u["i"] for u in d["transcripts"]["M2"]] == [5, 6, 9], "인용된 발화만 원문에 담는다"
    h = ask(graph, ask_tools, "11월 진행 내용", "2026-11-03")
    assert calls[1] == ("history", "2026-11-01", "2026-11-30", [], [])
    assert h["answer_status"] == "grounded" and "이후 '재개'(으)로 변경됐어요" in h["summary"], "superseded 경고는 코드가 붙인다"
    assert [x["decision_id"] for x in h["decision_history"]] == ["A1-d1"]
    assert h["decision_timeline"][0]["superseded_by"] == "A2-d1" and h["badge"]["status"] == "superseded"
    assert h["transcripts"] == {} and any("M1 원문을 읽지 못했어요" in w for w in h["warnings"]), \
        "원문 파일이 없어도 답변은 나오고 경고만 붙는다"
    act = ask(graph, ask_tools, "원인 파악 담당자?", "2026-11-03")
    assert calls[2] == ("action", ["T3"]) and act["answer_status"] == "grounded"
    assert act["sources"][0]["agenda_id"] == "A3" and act["current_decisions"] == [], "액션 근거는 결정으로 세지 않는다"
    ac = act["sentences"][0]["citations"][0]
    assert ac["tags"] == ["할 일"] and ac["utterances"] == [9] and [u["i"] for u in act["transcripts"]["M2"]] == [9], \
        "액션아이템은 원문 파일의 자기 발화 번호로 연결"
    ppl = ask(graph, ask_tools, "프로모션 누구한테 물어봐?", "2026-11-03")
    assert calls[3] == ("people", ["T1", "T2"]) and ppl["answer_status"] == "grounded"
    assert [x["agenda_id"] for x in ppl["sources"]] == ["A1"], "refs로 근거 회의를 출처로 만든다"
    sk = ask(graph, ask_tools, "신입 스킬?", "2026-11-03")
    assert calls[4] == ("skill", []) and sk["answer_status"] == "grounded" and sk["sources"] == [], "주제 없으면 전체 조회"
    n = ask(graph, ask_tools, "복지 포인트?", "2026-11-03")
    assert n["answer_status"] == "no_evidence" and n["summary"] == NO_EVIDENCE and n["sources"] == []
    assert n["badge"] is None and n["contacts"] == [] and n["follow_ups"] == [] and n["transcripts"] == {}
    assert compose_calls == ["프로모션 어떻게 됐어?", "11월 진행 내용", "원인 파악 담당자?",
                             "프로모션 누구한테 물어봐?", "신입 스킬?"], \
        "근거가 없으면 LLM 답변 생성을 부르지 않는다"
    w = ask(graph, ask_tools, "엉뚱한 주제로 고른 결정", "2026-11-03")
    assert calls[-2:] == [("decision", ["T3"]), ("decision", ["T1", "T2", "T3"])], "고른 Topic에 근거 없으면 전체로 1회 재조회"
    assert w["answer_status"] == "partial" and "전체 회의에서 다시 찾았어요" in " ".join(w["warnings"])
    ask(graph, ask_tools, "주제 모를 결정", "2026-11-03")
    assert calls[-1] == ("decision", ["T1", "T2", "T3"]), "주제 없는 결정 질문은 전체 Topic에서 찾는다"
    m1 = ask(graph, ask_tools, "1차 회의에서 뭐 했어?", "2026-11-03")
    assert calls[-1] == ("history", None, None, [], ["M1"]) and m1["slots"]["meeting_ids"] == ["M1"],         "없는 회의 ID는 버리고 회의로 조회"
    tp = ask(graph, ask_tools, "1차 회의 주제?", "2026-11-03")
    assert calls[-1] == ("history", None, None, [], ["M1"]), "회의를 지정하면 주제로 좁히지 않고 그 회의 전체를 본다"
    assert tp["decision_timeline"] == [] and tp["badge"] is None and tp["current_decisions"] == [], \
        "안건만 인용하고 결정 내용을 말하지 않으면 결정 이력·배지를 채우지 않는다"
    assert {c["agenda_id"] for c in tp["sentences"][0]["citations"]} == {"A1", "A2"}, "인용한 안건마다 원문 근거 1개 이상"
    assert tp["contacts"][0]["why"] == "PM · 안건 참석", "할 일 없는 사람을 '담당'이라고 쓰면 '참석'으로 고친다"
    print("self-check 통과: backfill 시간순·dry-run·SUPERSEDES·HITL·재실행 안전, Topic 계열 조회, "
          "근거 없는 문장 제거, superseded 경고, people/skill 경로, Topic 오분류 시 전체 재조회, 회의 지정·주제 없는 질문, no_evidence.")
