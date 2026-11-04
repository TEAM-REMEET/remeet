"""온보딩 질의 에이전트 (1차, 임시). 지시서: onboarding_agent_prompt.md를 줄인 범위.

1차 범위
  backfill  AgendaItem.decisions[] → Decision 노드 (DECIDED, ABOUT), 앞선 결정과 SUPERSEDES 판정(LLM)
  ask       decision / history / action / overview / people / skill 질문에 근거(source_id)가 달린 답변.
            근거 없으면 no_evidence
  eval      eval_questions.json (유형별 2~3문항)으로 근거 적중률(recall)과 no_evidence 정확도 측정

실행 (저장소 루트 .env 사용, eunje/ontology의 load.py로 적재된 Neo4j 필요):
    python onboarding_agent.py --self-check          # 가짜 DB·LLM으로 전체 검사
    python onboarding_agent.py backfill              # dry-run: DB에 쓰지 않고 결과만 출력
    python onboarding_agent.py backfill --write      # Decision / SUPERSEDES 적재 (재실행 안전)
    python onboarding_agent.py ask "블프 프로모션 어떻게 됐어요?"
    python onboarding_agent.py eval

[ask 그래프] classify(LLM) → retrieve ─┬─▶ compose(LLM) → ground → END
                                       └─▶ ground (근거 없음) → END
LLM은 질문 해석과 문장화만 한다. Cypher는 고정 템플릿, grounding은 코드가 검사한다.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
PROMPTS = {name: (SCRIPT_DIR / f"prompts/{name}.md").read_text(encoding="utf-8")
           for name in ("supersede", "classify", "compose")}
INTENTS = ("decision", "history", "action", "overview", "people", "skill", "other")
RELATIONS = ("supersedes", "complements", "unrelated")
SUPERSEDE_MIN_CONFIDENCE = 0.7  # 미만이면 자동 반영하지 않고 HITL 후보로
ALLOWED_VISIBILITY = ["public"]  # shortcut: Meeting.visibility가 아직 없어 전부 public 취급, 팀 권한 생기면 질문자 기준으로
NO_EVIDENCE = "관련 회의 기록이 없어요. 담당자에게 문의해 주세요."

# ─────────────────────────────── 고정 쿼리 ───────────────────────────────

CONSTRAINT = "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.decision_id IS UNIQUE"
TOPICS_QUERY = """
    MATCH (t:Topic) OPTIONAL MATCH (t)-[:SUBTOPIC_OF]->(p:Topic)
    RETURN t.topic_id AS topic_id, t.title AS title, p.topic_id AS parent_id ORDER BY topic_id"""
AGENDAS_QUERY = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:ABOUT]->(t:Topic)
    RETURN a.agenda_item_id AS agenda_item_id, a.title AS title, a.decisions AS decisions,
        t.topic_id AS topic_id, toString(m.held_at) AS held_at, EXISTS { (a)-[:DECIDED]->() } AS processed"""
DECISIONS_QUERY = """
    MATCH (a:AgendaItem)-[:DECIDED]->(d:Decision)-[:ABOUT]->(t:Topic)
    RETURN d.decision_id AS decision_id, d.content AS content, d.status AS status,
        toString(d.decided_at) AS decided_at, t.topic_id AS topic_id, a.agenda_item_id AS agenda_item_id"""
WRITE_DECISIONS = """
    UNWIND $rows AS r
    MATCH (a:AgendaItem {agenda_item_id: r.agenda_item_id}), (t:Topic {topic_id: r.topic_id})
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
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:DECIDED]->(d)
    WHERE coalesce(m.visibility, 'public') IN $visibility
    OPTIONAL MATCH (n:Decision)-[:SUPERSEDES]->(d)
    WITH d, a, m, head(collect(n {.decision_id, .content})) AS superseded_by
    RETURN d.decision_id AS source_id, 'decision' AS kind, d.content AS content, d.status AS status,
        toString(d.decided_at) AS decided_at, superseded_by,
        a.agenda_item_id AS agenda_item_id, a.title AS agenda_title, a.background AS background,
        a.discussion AS discussion, m.meeting_id AS meeting_id, m.title AS meeting_title,
        toString(m.held_at) AS held_at
    ORDER BY held_at DESC, source_id LIMIT 20"""
HISTORY_EVIDENCE = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:ABOUT]->(t:Topic)
    WHERE coalesce(m.visibility, 'public') IN $visibility
      AND ($date_from IS NULL OR m.held_at >= datetime($date_from))
      AND ($date_to IS NULL OR m.held_at < datetime($date_to) + duration('P1D'))
      AND (size($topic_ids) = 0 OR t.topic_id IN $topic_ids)
    OPTIONAL MATCH (a)-[:DECIDED]->(d:Decision)
    OPTIONAL MATCH (n:Decision)-[:SUPERSEDES]->(d)
    WITH m, a, collect(DISTINCT d {.decision_id, .content, .status, superseded_by: n {.decision_id, .content}}) AS decisions
    OPTIONAL MATCH (a)-[:PRODUCED]->(ai:ActionItem)
    RETURN a.agenda_item_id AS source_id, 'agenda' AS kind, a.agenda_item_id AS agenda_item_id,
        a.title AS agenda_title, a.background AS background, decisions,
        collect(ai.description) AS actions, m.meeting_id AS meeting_id, m.title AS meeting_title,
        toString(m.held_at) AS held_at
    ORDER BY held_at, source_id LIMIT 30"""
# 확정 담당자(ASSIGNED_TO)와 담당자 추천 에이전트의 pending 추천(RECOMMENDED_FOR)을 함께 돌려준다.
ACTION_EVIDENCE = """
    MATCH (m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:ABOUT]->(t:Topic)
    WHERE t.topic_id IN $topic_ids AND coalesce(m.visibility, 'public') IN $visibility
    MATCH (a)-[:PRODUCED]->(ai:ActionItem)
    OPTIONAL MATCH (p:Person)-[asg:ASSIGNED_TO]->(ai)
    WITH m, a, ai, [x IN collect(DISTINCT CASE WHEN p IS NULL THEN null
        ELSE {user_id: p.user_id, name: p.name, work_status: asg.work_status} END) WHERE x IS NOT NULL] AS assignees
    OPTIONAL MATCH (c:Person)-[r:RECOMMENDED_FOR {status: 'pending'}]->(ai)
    OPTIONAL MATCH (c)-[h:HAS_SKILL]->(s:Skill)
    WITH m, a, ai, assignees, c, r, [x IN collect({name: s.name, level: h.level}) WHERE x.name IS NOT NULL] AS skills
    WITH m, a, ai, assignees, [x IN collect(CASE WHEN c IS NULL THEN null
        ELSE {user_id: c.user_id, name: c.name, position: r.position, slot: r.slot, is_main: r.is_main,
              total: r.total, reason: r.reason, skills: skills} END) WHERE x IS NOT NULL] AS recommended
    RETURN ai.action_item_id AS source_id, 'action' AS kind, ai.description AS task, toString(ai.due) AS due,
        ai.status AS status, assignees, recommended, a.agenda_item_id AS agenda_item_id, a.title AS agenda_title,
        m.meeting_id AS meeting_id, m.title AS meeting_title, toString(m.held_at) AS held_at
    ORDER BY held_at, source_id LIMIT 20"""
# 아래 3개(overview / people / skill)는 근거 회의를 refs 목록으로 돌려준다. ground가 refs로 출처를 만든다.
OVERVIEW_EVIDENCE = """
    MATCH (t:Topic) WHERE size($topic_ids) = 0 OR t.topic_id IN $topic_ids
    OPTIONAL MATCH (t)-[:IN]->(pr:Project)
    OPTIONAL MATCH (t)-[:SUBTOPIC_OF]->(pt:Topic)
    OPTIONAL MATCH (m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:ABOUT]->(t)
    WHERE coalesce(m.visibility, 'public') IN $visibility
    OPTIONAL MATCH (a)-[:DECIDED]->(d:Decision {status: 'active'})
    WITH t, pr, pt, m, a, collect(d.content) AS decisions
    WITH t, pr, pt, [x IN collect(CASE WHEN a IS NULL THEN null ELSE {meeting_id: m.meeting_id,
        meeting_title: m.title, held_at: toString(m.held_at), agenda_item_id: a.agenda_item_id,
        agenda_title: a.title, current_decisions: decisions} END) WHERE x IS NOT NULL] AS refs
    RETURN t.topic_id AS source_id, 'topic' AS kind, t.title AS topic, t.status AS topic_status,
        toString(t.last_discussed_at) AS last_discussed_at, pt.title AS parent_topic,
        pr.project_id AS project_id, refs
    ORDER BY source_id"""
# shortcut: CONTRIBUTED_TO가 없어 "주제 관련자" = 그 주제 회의 참석자 + 그 주제 액션아이템 추천 후보, 발언 기록 생기면 교체
PEOPLE_EVIDENCE = """
    MATCH (p:Person) WHERE coalesce(p.is_active, true)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(tm:Team)
    OPTIONAL MATCH (p)-[:HAS_ROLE]->(ro:Role)
    OPTIONAL MATCH (p)-[h:HAS_SKILL]->(s:Skill)
    WITH p, collect(DISTINCT tm.name) AS teams, collect(DISTINCT ro.name) AS roles,
        [x IN collect(DISTINCT {name: s.name, level: h.level}) WHERE x.name IS NOT NULL] AS skills
    OPTIONAL MATCH (p)-[:ATTENDED]->(m:Meeting)-[:HAS_AGENDA]->(a:AgendaItem)-[:ABOUT]->(t:Topic)
    WHERE t.topic_id IN $topic_ids AND coalesce(m.visibility, 'public') IN $visibility
    WITH p, teams, roles, skills, [x IN collect(DISTINCT CASE WHEN a IS NULL THEN null ELSE {
        meeting_id: m.meeting_id, meeting_title: m.title, held_at: toString(m.held_at),
        agenda_item_id: a.agenda_item_id, agenda_title: a.title} END) WHERE x IS NOT NULL] AS refs
    OPTIONAL MATCH (p)-[:RECOMMENDED_FOR {status: 'pending'}]->(ai:ActionItem)<-[:PRODUCED]-(:AgendaItem)-[:ABOUT]->(t2:Topic)
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
    OPTIONAL MATCH (p)-[r:RECOMMENDED_FOR {status: 'pending'}]->(ai:ActionItem)<-[:PRODUCED]-(a:AgendaItem)-[:ABOUT]->(t:Topic),
        (m:Meeting)-[:HAS_AGENDA]->(a)
    WHERE (size($topic_ids) = 0 OR t.topic_id IN $topic_ids) AND coalesce(m.visibility, 'public') IN $visibility
    WITH s, {name: p.name, position: p.position, level: h.level, teams: teams} AS holder,
        collect(CASE WHEN ai IS NULL THEN null ELSE {task: ai.description, reason: r.reason,
            meeting_id: m.meeting_id, meeting_title: m.title, held_at: toString(m.held_at),
            agenda_item_id: a.agenda_item_id, agenda_title: a.title} END) AS used
    WITH s, collect(holder) AS holders, reduce(acc = [], u IN collect(used) | acc + u) AS refs
    WHERE size($topic_ids) = 0 OR size(refs) > 0
    RETURN s.skill_id AS source_id, 'skill' AS kind, s.name AS skill, holders, refs
    ORDER BY size(refs) DESC, skill"""


# ─────────────────────────────── 공통 ───────────────────────────────

def response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM이 빈 답변을 반환했습니다.")
    return content.strip()


def call_json(llm, prompt: str, payload: dict) -> dict:
    response = llm.bind(response_format={"type": "json_object"}).invoke(
        [("system", prompt), ("human", json.dumps(payload, ensure_ascii=False, default=str))])
    out = json.loads(response_text(response))
    if not isinstance(out, dict):
        raise ValueError("LLM 결과가 JSON 객체가 아닙니다.")
    return out


def lineage(topics: list[dict], topic_ids) -> list[str]:
    """Topic + 상위 Topic + 하위 Topic 전부. 안건이 SUBTOPIC_OF로 이어져 있어 계열 단위로 찾는다."""
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
    for ag in sorted(tools["agendas"](), key=lambda a: (a["held_at"], a["agenda_item_id"])):
        if ag["processed"]:
            continue
        family = set(lineage(topics, [ag["topic_id"]]))
        for i, content in enumerate(ag["decisions"] or [], 1):
            d = {"decision_id": f"{ag['agenda_item_id']}-d{i}", "content": content, "decided_at": ag["held_at"],
                 "status": "active", "topic_id": ag["topic_id"], "agenda_item_id": ag["agenda_item_id"],
                 "confidence": 1.0}  # decisions[]는 요약 단계에서 확정 결정으로 뽑힌 값
            candidates = [p for p in pool if p["status"] == "active" and p["topic_id"] in family
                          and p["agenda_item_id"] != ag["agenda_item_id"]]
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
    evidence: list[dict]
    sentences: list[dict]
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

    decisions = {}  # decision_id → (결정, 근거 source_id)
    for sid in used:
        e = by_id[sid]
        for d in ([e] if e["kind"] == "decision" else e.get("decisions") or []):
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
    refs = [r for sid in used for r in by_id[sid].get("refs", [by_id[sid]])]  # refs: 여러 회의를 근거로 가진 행
    sources = list({r["agenda_item_id"]: {k: r[k] for k in (
        "meeting_id", "meeting_title", "held_at", "agenda_item_id", "agenda_title")} for r in refs}.values())
    pick = lambda d: {"decision_id": d.get("decision_id") or d["source_id"],
                      **{k: d.get(k) for k in ("content", "status", "decided_at")}}
    return {"answer": {
        "question": s["question"], "intent": s.get("intent"),
        "slots": {"topic_ids": s.get("topic_ids", []), "date_from": s.get("date_from"), "date_to": s.get("date_to")},
        "answer_status": status,
        "summary": " ".join(x["text"] for x in kept) if kept else NO_EVIDENCE,
        "sentences": kept,
        "current_decisions": [pick(d) for d in current],
        "decision_history": [pick(d) for d in history],
        "sources": sources,
        "warnings": warnings,
    }}


def build_ask_graph(tools: dict, llm):
    def classify(s: AskState) -> dict:
        try:
            out = call_json(llm, PROMPTS["classify"], {
                "question": s["question"], "today": s["today"],
                "topics": [{"topic_id": t["topic_id"], "title": t["title"]} for t in s["topics"]]})
        except Exception as exc:
            return {"intent": "other", "topic_ids": [], "warnings": [f"질문 해석 실패 ({type(exc).__name__})"]}
        known = {t["topic_id"] for t in s["topics"]}
        return {"intent": out.get("intent") if out.get("intent") in INTENTS else "other",
                "topic_ids": [t for t in out.get("topic_ids") or [] if t in known],
                "date_from": as_date(out.get("date_from")), "date_to": as_date(out.get("date_to")),
                "warnings": []}

    def retrieve(s: AskState) -> dict:
        family = lineage(s["topics"], s["topic_ids"])
        try:
            if s["intent"] == "decision" and family:
                return {"evidence": tools["decision_evidence"](family)}
            if s["intent"] == "history" and (family or s.get("date_from") or s.get("date_to")):
                return {"evidence": tools["history_evidence"](s.get("date_from"), s.get("date_to"), family)}
            if s["intent"] == "action":  # 주제 없는 질문("지금 누가 뭘 하고 있어요?")은 전체 액션아이템에서 찾는다
                # shortcut: 쿼리 LIMIT 20이라 액션아이템이 많아지면 일부만 보임, 그때 status·기간 필터 추가
                return {"evidence": tools["action_evidence"](family or [t["topic_id"] for t in s["topics"]])}
            if s["intent"] in ("overview", "people", "skill"):  # 주제가 없으면 전체(빈 목록)로 찾는다
                return {"evidence": tools[f"{s['intent']}_evidence"](family)}
        except Exception as exc:
            return {"evidence": [], "warnings": s["warnings"] + [f"근거 조회 실패 ({type(exc).__name__})"]}
        return {"evidence": []}

    def compose(s: AskState) -> dict:
        try:
            out = call_json(llm, PROMPTS["compose"], {"question": s["question"], "intent": s["intent"],
                                                      "evidence": s["evidence"]})
        except Exception as exc:
            return {"sentences": [], "warnings": s["warnings"] + [f"답변 생성 실패 ({type(exc).__name__})"]}
        sentences = [{"text": str(x.get("text") or "").strip(),
                      "source_ids": [str(i) for i in x.get("source_ids") or []]}
                     for x in out.get("sentences") or [] if isinstance(x, dict)]
        return {"sentences": [x for x in sentences if x["text"]]}

    g = StateGraph(AskState)
    for name, node in [("classify", classify), ("retrieve", retrieve), ("compose", compose), ("ground", ground)]:
        g.add_node(name, node)
    g.add_edge(START, "classify")
    g.add_edge("classify", "retrieve")
    g.add_conditional_edges("retrieve", lambda s: "compose" if s["evidence"] else "ground", ["compose", "ground"])
    g.add_edge("compose", "ground")
    g.add_edge("ground", END)
    return g.compile()


def ask(graph, tools: dict, question: str, today: str | None = None) -> dict:
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return graph.invoke({"question": question, "today": today, "topics": tools["topics"]()})["answer"]


# ─────────────────────────────── 3) eval ───────────────────────────────

def evaluate(graph, tools: dict, questions: list[dict], today: str | None = None) -> dict:
    rows = []
    for q in questions:
        a = ask(graph, tools, q["question"], today)
        cited = {src["agenda_item_id"] for src in a["sources"]}
        expected = set(q["expected_agenda_ids"])
        row = {"id": q["id"], "group": q.get("group", q["intent"]), "intent": q["intent"],
               "predicted_intent": a["intent"],
               "answer_status": a["answer_status"],
               "recall": round(len(expected & cited) / len(expected), 2) if expected else None,
               "no_evidence_ok": (a["answer_status"] == "no_evidence") == q["expect_no_evidence"],
               "superseded_ok": bool(a["decision_history"]) if q.get("expect_superseded") else None,
               "cited": sorted(cited), "summary": a["summary"]}
        rows.append(row)
    by_group = {}
    for group in dict.fromkeys(r["group"] for r in rows):
        rs = [r for r in rows if r["group"] == group]
        recalls = [r["recall"] for r in rs if r["recall"] is not None]
        by_group[group] = {"n": len(rs),
                           "intent_acc": round(sum(r["predicted_intent"] == r["intent"] for r in rs) / len(rs), 2),
                           "recall": round(sum(recalls) / len(recalls), 2) if recalls else None,
                           "no_evidence_acc": round(sum(r["no_evidence_ok"] for r in rs) / len(rs), 2)}
    return {"rows": rows, "by_group": by_group}


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
        "agendas": lambda: q(AGENDAS_QUERY),
        "decisions": lambda: q(DECISIONS_QUERY),
        "write": write,
        "decision_evidence": lambda topic_ids: q(DECISION_EVIDENCE, topic_ids=topic_ids,
                                                 visibility=ALLOWED_VISIBILITY),
        "history_evidence": lambda date_from, date_to, topic_ids: q(
            HISTORY_EVIDENCE, date_from=date_from, date_to=date_to, topic_ids=topic_ids,
            visibility=ALLOWED_VISIBILITY),
        "action_evidence": lambda topic_ids: q(ACTION_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "overview_evidence": lambda topic_ids: q(OVERVIEW_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "people_evidence": lambda topic_ids: q(PEOPLE_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
        "skill_evidence": lambda topic_ids: q(SKILL_EVIDENCE, topic_ids=topic_ids, visibility=ALLOWED_VISIBILITY),
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
    agendas = [{"agenda_item_id": "A1", "topic_id": "T1", "held_at": "2026-11-06", "decisions": ["보류"], "processed": False},
               {"agenda_item_id": "A2", "topic_id": "T2", "held_at": "2026-11-10", "decisions": ["재개"], "processed": False},
               {"agenda_item_id": "A3", "topic_id": "T3", "held_at": "2026-11-01", "decisions": ["원인 파악"], "processed": False},
               {"agenda_item_id": "A4", "topic_id": "T1", "held_at": "2026-11-12", "decisions": ["애매"], "processed": False}]

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
          "superseded_by": {"decision_id": "A2-d1", "content": "재개"}, "agenda_item_id": "A1", "agenda_title": "일정",
          "meeting_id": "M1", "meeting_title": "회의1", "held_at": "2026-11-06"}
    a2 = {**a1, "source_id": "A2-d1", "content": "재개", "status": "active", "superseded_by": None,
          "agenda_item_id": "A2", "meeting_id": "M2", "held_at": "2026-11-10"}
    calls = []
    ask_tools = {"topics": lambda: topics,
                 "decision_evidence": lambda ids: calls.append(("decision", ids)) or [a1, a2],
                 "history_evidence": lambda f, t, ids: calls.append(("history", f, t, ids)) or [
                     {**a1, "source_id": "A1", "kind": "agenda", "decisions": [
                         {"decision_id": "A1-d1", "content": "보류", "status": "superseded",
                          "superseded_by": {"decision_id": "A2-d1", "content": "재개"}}]}],
                 "action_evidence": lambda ids: calls.append(("action", ids)) or [
                     {**a1, "source_id": "A3-t1", "kind": "action", "task": "원인 파악", "due": "2026-10-27",
                      "agenda_item_id": "A3", "assignees": [],
                      "recommended": [{"user_id": "u1", "name": "김결제", "slot": "senior", "skills": []}]}],
                 "people_evidence": lambda ids: calls.append(("people", ids)) or [
                     {"source_id": "u1", "kind": "person", "name": "김결제", "refs": [
                         {k: a1[k] for k in ("meeting_id", "meeting_title", "held_at", "agenda_item_id", "agenda_title")}]}],
                 "skill_evidence": lambda ids: calls.append(("skill", ids)) or [
                     {"source_id": "s1", "kind": "skill", "skill": "Figma", "holders": [], "refs": []}],
                 "overview_evidence": lambda ids: calls.append(("overview", ids)) or []}
    classify = {"프로모션 어떻게 됐어?": {"intent": "decision", "topic_ids": ["T2", "NOPE"]},
                "원인 파악 담당자?": {"intent": "action", "topic_ids": ["T3"]},
                "11월 진행 내용": {"intent": "history", "topic_ids": [], "date_from": "2026-11-01", "date_to": "2026-11-30"},
                "복지 포인트?": {"intent": "other", "topic_ids": []},
                "주제 모를 결정": {"intent": "decision", "topic_ids": []},
                "프로모션 누구한테 물어봐?": {"intent": "people", "topic_ids": ["T1"]},
                "신입 스킬?": {"intent": "skill", "topic_ids": []}}
    compose_calls = []

    def compose(p):
        compose_calls.append(p["question"])
        return {"sentences": [{"text": "프로모션은 재개됐어요.", "source_ids": ["A2-d1"]},
                              {"text": "지어낸 문장", "source_ids": ["BAD"]},
                              {"text": "근거 없는 문장", "source_ids": []}]} if p["intent"] == "decision" else \
            {"sentences": [{"text": "담당자는 미확정이고 김결제가 추천 후보예요.", "source_ids": ["A3-t1"]}]} \
            if p["intent"] == "action" else {"sentences": [{"text": "김결제에게 물어보세요.", "source_ids": ["u1"]}]} \
            if p["intent"] == "people" else {"sentences": [{"text": "Figma가 쓰여요.", "source_ids": ["s1"]}]} \
            if p["intent"] == "skill" else {"sentences": [{"text": "11/6에 보류했어요.", "source_ids": ["A1"]}]}

    graph = build_ask_graph(ask_tools, FakeLLM({"classify": lambda p: classify[p["question"]], "compose": compose}))
    d = ask(graph, ask_tools, "프로모션 어떻게 됐어?", "2026-11-03")
    assert calls[0] == ("decision", ["T1", "T2"]), "없는 topic_id는 버리고 계열(상위 포함)로 조회"
    assert d["answer_status"] == "partial" and [x["text"] for x in d["sentences"]] == ["프로모션은 재개됐어요."]
    assert [x["decision_id"] for x in d["current_decisions"]] == ["A2-d1"] and d["sources"][0]["agenda_item_id"] == "A2"
    h = ask(graph, ask_tools, "11월 진행 내용", "2026-11-03")
    assert calls[1] == ("history", "2026-11-01", "2026-11-30", [])
    assert h["answer_status"] == "grounded" and "이후 '재개'(으)로 변경됐어요" in h["summary"], "superseded 경고는 코드가 붙인다"
    assert [x["decision_id"] for x in h["decision_history"]] == ["A1-d1"]
    act = ask(graph, ask_tools, "원인 파악 담당자?", "2026-11-03")
    assert calls[2] == ("action", ["T3"]) and act["answer_status"] == "grounded"
    assert act["sources"][0]["agenda_item_id"] == "A3" and act["current_decisions"] == [], "액션 근거는 결정으로 세지 않는다"
    ppl = ask(graph, ask_tools, "프로모션 누구한테 물어봐?", "2026-11-03")
    assert calls[3] == ("people", ["T1", "T2"]) and ppl["answer_status"] == "grounded"
    assert [x["agenda_item_id"] for x in ppl["sources"]] == ["A1"], "refs로 근거 회의를 출처로 만든다"
    sk = ask(graph, ask_tools, "신입 스킬?", "2026-11-03")
    assert calls[4] == ("skill", []) and sk["answer_status"] == "grounded" and sk["sources"] == [], "주제 없으면 전체 조회"
    for question in ("복지 포인트?", "주제 모를 결정"):
        n = ask(graph, ask_tools, question, "2026-11-03")
        assert n["answer_status"] == "no_evidence" and n["summary"] == NO_EVIDENCE and n["sources"] == []
    assert compose_calls == ["프로모션 어떻게 됐어?", "11월 진행 내용", "원인 파악 담당자?",
                             "프로모션 누구한테 물어봐?", "신입 스킬?"], \
        "근거가 없으면 LLM 답변 생성을 부르지 않는다"
    print("self-check 통과: backfill 시간순·dry-run·SUPERSEDES·HITL·재실행 안전, Topic 계열 조회, "
          "근거 없는 문장 제거, superseded 경고, people/skill 경로, no_evidence.")


# ─────────────────────────────── main ───────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="온보딩 질의 에이전트 (1차, 임시)")
    parser.add_argument("command", nargs="?", choices=["backfill", "ask", "eval"])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--write", action="store_true", help="backfill 결과를 DB에 적재 (없으면 dry-run)")
    parser.add_argument("--today", help="상대 날짜 기준일 (기본: 오늘, Asia/Seoul)")
    parser.add_argument("--model")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    if not args.command:
        parser.error("command(backfill / ask / eval) 또는 --self-check가 필요합니다.")
    if args.command == "ask" and not (args.question or "").strip():
        parser.error("ask에는 질문이 필요합니다.")

    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI
    from neo4j import GraphDatabase
    load_dotenv(REPO_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("NEO4J_PASSWORD"):
        print("OPENAI_API_KEY와 NEO4J_PASSWORD 설정이 필요합니다.", file=sys.stderr)
        return 1
    llm = ChatOpenAI(model=args.model or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-5.5",
                     timeout=60, max_retries=1)
    with GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                              auth=(os.getenv("NEO4J_USERNAME") or "neo4j", os.environ["NEO4J_PASSWORD"]),
                              notifications_disabled_classifications=["UNRECOGNIZED"]) as driver:
        driver.verify_connectivity()
        tools = neo4j_tools(driver)
        if args.command == "backfill":
            result = backfill(tools, llm, args.write)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("DB 적재 완료" if result["written"] else "새로 적재할 결정이 없습니다" if args.write
                  else "dry-run: DB에 쓰지 않았습니다 (--write로 적재)")
            return 0
        graph = build_ask_graph(tools, llm)
        if args.command == "ask":
            print(json.dumps(ask(graph, tools, args.question, args.today), ensure_ascii=False, indent=2))
            return 0
        questions = json.loads((SCRIPT_DIR / "eval_questions.json").read_text(encoding="utf-8"))
        result = evaluate(graph, tools, questions, args.today)
    out = SCRIPT_DIR / "eval_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in result["rows"]:
        print(f"{r['id']:<3} {r['group']:<12} 예측={r['predicted_intent']:<8} {r['answer_status']:<12} "
              f"recall={r['recall']} no_evidence_ok={r['no_evidence_ok']} superseded_ok={r['superseded_ok']}")
    print(json.dumps(result["by_group"], ensure_ascii=False, indent=2))
    print(f"결과 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
