"""Meeting-level assignee recommendation agent (test version, LangGraph).

Graph (one thread per meeting_id):
  load ─▶ interpret ─▶ score ─▶ [explain] ─▶ [save] ─▶ [review] ─▶ END
   └─ no open action items ─▶ END
  load       open action items of the meeting (REQUIRES_SKILL, ASSIGNED_TO, FOLLOWS_UP history)
  interpret  spec per item: REQUIRES_SKILL from the DB, else LLM; no skills → flagged for the manager
  score      3_recommend_assignee.rank_candidates (deterministic, unchanged) + workload balancing
  explain    LLM explanation only; scores and order are fixed before this     (skipped by --no-explain)
  save       RECOMMENDED_FOR {status: pending, scores}                         (--save)
  review     interrupt(): the manager picks per item, then ASSIGNED_TO        (--review)

python 4_recommend_agent.py --meeting-id meeting:TS3003:001 --no-explain
python 4_recommend_agent.py --meeting-id meeting:TS3003:001 --save --review
python 4_recommend_agent.py --self-check        # whole graph with fake DB tools, no Neo4j or LLM

Required: neo4j, python-dotenv, langchain-openai, langgraph.
Run 1_import_csv.py (user, project, meeting layers) first.
"""

from __future__ import annotations

import argparse
from collections import Counter
import getpass
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


SCRIPT_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("recommend", SCRIPT_DIR / "3_recommend_assignee.py")
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)

SOURCE = "recommend_agent"
DEFAULT_JSON_OUT = "recommend_agent_result.json"

ITEMS_QUERY = """
    MATCH (:Entity:Meeting {id: $meeting_id})-[:HAS_AGENDA]->(ag:AgendaItem)-[:PRODUCED]->(ai:ActionItem)
    WHERE coalesce(ai.status, 'open') <> 'completed'
    OPTIONAL MATCH (ai)-[rs:REQUIRES_SKILL]->(s:Entity)
    WITH ag, ai, [x IN collect({skill_id: s.id, importance: rs.importance}) WHERE x.skill_id IS NOT NULL] AS skills
    OPTIONAL MATCH (u:Entity)-[:ASSIGNED_TO]->(ai)
    WITH ag, ai, skills, [x IN collect(u.id) WHERE x IS NOT NULL] AS assignees
    OPTIONAL MATCH (ai)-[:FOLLOWS_UP]->(:ActionItem)<-[:ASSIGNED_TO]-(pu:Entity)
    RETURN ai.id AS id, ai.description AS description, ag.title AS agenda, ag.order AS ord,
        skills, assignees, [x IN collect(DISTINCT pu.id) WHERE x IS NOT NULL] AS previous_assignees
    ORDER BY ord, id"""
# Re-running replaces only undecided recommendations; accepted/rejected ones stay as history.
CLEAR_PENDING = """MATCH ()-[r:RECOMMENDED_FOR {status: 'pending'}]->(a:Entity)
    WHERE a.id IN $ids DELETE r"""
SAVE_RECOMMENDATIONS = """UNWIND $rows AS row
    MATCH (u:Entity {id: row.user_id}), (a:Entity {id: row.action_item_id})
    MERGE (u)-[r:RECOMMENDED_FOR]->(a)
    ON CREATE SET r.status = 'pending'
    SET r.score = row.total, r.skill_score = row.skill_score, r.role_team_score = row.role_team_score,
        r.workload_score = row.workload_score, r.rank = row.rank,
        r.source_type = $source, r.recommended_at = datetime()
    RETURN count(r) AS written"""
ASSIGN = """MATCH (u:Entity {id: $user_id}), (a:Entity {id: $action_item_id})
    MERGE (u)-[r:ASSIGNED_TO]->(a)
    ON CREATE SET r.work_status = 'in_work', r.assign_source = $assign_source, r.source_type = $source
    WITH u, a
    OPTIONAL MATCH (x)-[rf:RECOMMENDED_FOR]->(a)
    SET rf.status = CASE WHEN x = u THEN 'accepted' ELSE 'rejected' END
    RETURN count(*) AS n"""


class State(TypedDict, total=False):
    meeting_id: str
    catalog: dict
    items: list[dict]
    candidates: list[dict]
    workload_present: bool
    results: list[dict]


# ---------- pure steps ----------

def importance_label(value) -> str:
    """REQUIRES_SKILL.importance is numeric; map it onto rec.IMPORTANCE weights."""
    value = float(value or 0)
    return "core" if value >= rec.IMPORTANCE["core"] else "major" if value >= rec.IMPORTANCE["major"] else "minor"


def build_spec(item: dict, catalog: dict, llm) -> tuple[dict | None, list[str]]:
    """Returns (spec, warnings); spec None means the manager has to decide the skills."""
    if item["skills"]:
        raw = {"required_skills": [{"skill_id": s["skill_id"], "importance": importance_label(s["importance"])}
                                   for s in item["skills"]]}
    elif llm is None:
        return None, ["REQUIRES_SKILL이 없고 LLM을 쓰지 않아 요구 스킬을 정할 수 없습니다."]
    else:
        raw = rec.interpret_task(llm, item["description"], catalog)
    try:
        return rec.normalize_spec(raw, catalog)
    except ValueError as exc:
        return None, [str(exc)]


def recommend_meeting(items: list[dict], candidates: list[dict], top: int) -> list[dict]:
    """Rank each item, adding this meeting's earlier top picks to workload.
    Balancing only applies to someone with an equal-skill alternative, so it never
    pushes the only qualified person below less qualified ones."""
    picked = Counter()
    results = []
    for item in items:
        result = {"action_item_id": item["id"], "task": item["description"], "agenda": item.get("agenda"),
                  "spec": item.get("spec"), "recommendations": [], "excluded": [],
                  "warnings": list(item.get("warnings", []))}
        if item.get("assignees"):
            result["status"] = "already_assigned"
            result["warnings"].append("이미 담당자가 있어 추천하지 않습니다: " + ", ".join(item["assignees"]))
        elif item.get("spec") is None:
            result["status"] = "needs_manager_input"
        else:
            base, _ = rec.rank_candidates(item["spec"], candidates)
            skill = {r["user_id"]: r["skill_score"] for r in base}
            peers = Counter(skill.values())
            balanced = {uid for uid in skill if picked[uid] and peers[skill[uid]] > 1}
            # shortcut: assumes each top pick is accepted; re-run after review for exact balance.
            adjusted = [{**c, "open_tasks": c["open_tasks"] + picked[c["user_id"]]}
                        if c["user_id"] in balanced else c for c in candidates]
            ranked, excluded = rec.rank_candidates(item["spec"], adjusted)
            result.update(recommendations=ranked[:top], excluded=excluded, status="pending_manager_review")
            if balanced:
                result["warnings"].append("이 회의에서 먼저 추천된 업무를 업무량에 더해 계산했습니다 "
                                          "(스킬 점수가 같은 후보가 있는 경우만): " + ", ".join(sorted(balanced)))
            if len(ranked) < top:
                result["warnings"].append(f"요구 스킬을 가진 후보가 {len(ranked)}명뿐입니다.")
            if ranked:
                picked[ranked[0]["user_id"]] += 1
        for user_id in item.get("previous_assignees", []):
            result["warnings"].append(f"이전 업무(FOLLOWS_UP)의 담당자: {user_id} (점수에는 반영 안 됨)")
        results.append(result)
    return results


def print_item(result: dict) -> None:
    if result["status"] == "pending_manager_review":
        rec.print_result(result)
    else:
        print(f"\n업무: {result['task']}\n  [{result['status']}] " + " / ".join(result["warnings"]))


# ---------- graph ----------

def build_graph(tools: dict, llm, top: int, explain: bool, save: bool, review: bool):
    """tools: catalog(), items(meeting_id), candidates(), save(rows, ids), assign(user_id, item_id, source).
    Passed in so the same graph runs on Neo4j or on fakes (--self-check)."""

    def load(state: State) -> dict:
        return {"catalog": tools["catalog"](), "items": tools["items"](state["meeting_id"])}

    def interpret_node(state: State) -> dict:
        items = []
        for item in state["items"]:
            try:
                spec, warnings = build_spec(item, state["catalog"], llm)
            except Exception as exc:  # one bad LLM answer must not stop the other items
                spec, warnings = None, [f"LLM 업무 해석 실패 ({type(exc).__name__})"]
            items.append({**item, "spec": spec, "warnings": warnings})
        return {"items": items}

    def score(state: State) -> dict:
        candidates, workload_present = tools["candidates"]()
        results = recommend_meeting(state["items"], candidates, top)
        for result in results:
            if result["status"] == "pending_manager_review":  # score caveats only where scores were shown
                result["warnings"] += rec.data_warnings(result["spec"], candidates, workload_present)
            print_item(result)
        return {"candidates": candidates, "workload_present": workload_present, "results": results}

    def explain_node(state: State) -> dict:
        results = []
        for result in state["results"]:
            result = {**result}
            if result["recommendations"]:
                try:
                    result["explanation"] = rec.explain(llm, result)
                    print(f"\n[{result['action_item_id']}] 추천 설명:\n{result['explanation']}")
                except Exception as exc:
                    print(f"LLM 설명 생성 실패 ({type(exc).__name__}).", file=sys.stderr)
            results.append(result)
        return {"results": results}

    def save_node(state: State) -> dict:
        pending = [r for r in state["results"] if r["status"] == "pending_manager_review"]
        rows = [{**c, "action_item_id": r["action_item_id"]} for r in pending for c in r["recommendations"]]
        tools["save"](rows, [r["action_item_id"] for r in pending])
        print(f"\nRECOMMENDED_FOR {len(rows)}개 저장 (status=pending)")
        return {}

    def review_node(state: State) -> dict:
        pending = [r for r in state["results"] if r["recommendations"]]
        if not pending:
            return {}
        # The graph pauses here; resume with Command(resume={action_item_id: rank or None}).
        # Code above this line re-runs on resume, so nothing before it may write.
        decisions = interrupt([{"action_item_id": r["action_item_id"], "task": r["task"],
                                "candidates": [{k: c[k] for k in ("rank", "user_id", "name", "total")}
                                               for c in r["recommendations"]]} for r in pending])
        history = {i["id"]: i["previous_assignees"] for i in state["items"]}
        results = []
        for result in state["results"]:
            choice = str(decisions.get(result["action_item_id"]) or "")
            chosen = next((c for c in result["recommendations"] if str(c["rank"]) == choice), None)
            if chosen:
                source = "history" if chosen["user_id"] in history.get(result["action_item_id"], []) else "manual"
                tools["assign"](chosen["user_id"], result["action_item_id"], source)
                result = {**result, "status": "assigned", "assigned_to": chosen["user_id"], "assign_source": source}
            results.append(result)
        return {"results": results}

    steps = [("interpret", interpret_node), ("score", score)]
    if explain and llm is not None:
        steps.append(("explain", explain_node))
    if save:
        steps.append(("save", save_node))
    if review:
        steps.append(("review", review_node))

    graph = StateGraph(State)
    graph.add_node("load", load)
    for name, node in steps:
        graph.add_node(name, node)
    graph.add_edge(START, "load")
    graph.add_conditional_edges("load", lambda s: "interpret" if s["items"] else END, ["interpret", END])
    for (a, _), (b, _) in zip(steps, steps[1:]):
        graph.add_edge(a, b)
    graph.add_edge(steps[-1][0], END)
    # shortcut: in-memory checkpoints die with the process; use a persistent saver when review moves to the web.
    return graph.compile(checkpointer=InMemorySaver())


def run(graph, meeting_id: str, decide) -> dict:
    """Runs one meeting; decide(payload) answers the review interrupt."""
    config = {"configurable": {"thread_id": meeting_id}}
    state = graph.invoke({"meeting_id": meeting_id}, config)
    while state.get("__interrupt__"):
        state = graph.invoke(Command(resume=decide(state["__interrupt__"][0].value)), config)
    return state


def ask_console(payload: list[dict]) -> dict:
    decisions = {}
    print("\n===== 관리자 검토 =====")
    for item in payload:
        print(f"\n[{item['action_item_id']}] {item['task']}")
        for c in item["candidates"]:
            print(f"  {c['rank']}. {c['name']} ({c['user_id']}) {c['total']:.1f}점")
        decisions[item["action_item_id"]] = input("배정할 순위 번호 (Enter=건너뜀): ").strip() or None
    return decisions


def neo4j_tools(session) -> dict:
    def save_tx(tx, rows, ids):
        tx.run(CLEAR_PENDING, ids=ids).consume()
        tx.run(SAVE_RECOMMENDATIONS, rows=rows, source=SOURCE).consume()

    return {
        "catalog": lambda: session.execute_read(rec.get_catalog),
        "items": lambda meeting_id: session.execute_read(
            lambda tx: tx.run(ITEMS_QUERY, meeting_id=meeting_id).data()),
        "candidates": lambda: session.execute_read(rec.get_candidates),
        "save": lambda rows, ids: session.execute_write(save_tx, rows, ids),
        "assign": lambda user_id, item_id, source: session.execute_write(
            lambda tx: tx.run(ASSIGN, user_id=user_id, action_item_id=item_id,
                              assign_source=source, source=SOURCE).consume()),
    }


def self_check() -> None:
    people = [{"user_id": uid, "name": uid, "is_active": True, "teams": [], "roles": [], "open_tasks": 0,
               "skills": [{"id": sid, "level": level, "verification_status": None} for sid, level in skills]}
              for uid, skills in (("A", [("MD-01", 4)]), ("B", [("MD-01", 4)]), ("C", []), ("D", [("MD-02", 3)]))]
    item = {"description": "트렌드 조사", "agenda": "시장", "ord": 1, "assignees": [], "previous_assignees": [],
            "skills": [{"skill_id": "MD-01", "importance": 0.5}]}
    only_d = [{"skill_id": "MD-02", "importance": 0.5}]
    items = [{**item, "id": "x1"}, {**item, "id": "x2"}, {**item, "id": "x3", "assignees": ["A"]},
             {**item, "id": "x4", "skills": []}, {**item, "id": "x5", "skills": only_d},
             {**item, "id": "x6", "skills": only_d}]
    saved, assigned = [], []
    tools = {
        "catalog": lambda: {"skills": [{"id": "MD-01"}, {"id": "MD-02"}], "roles": [], "teams": [],
                            "users": [{"id": u["user_id"]} for u in people]},
        "items": lambda meeting_id: items if meeting_id == "m1" else [],
        "candidates": lambda: (people, True),
        "save": lambda rows, ids: saved.append((rows, ids)),
        "assign": lambda *args: assigned.append(args),
    }
    graph = build_graph(tools, None, 3, explain=False, save=True, review=True)
    state = run(graph, "m1", lambda payload: {"x1": 1})
    status = {r["action_item_id"]: r for r in state["results"]}
    top = {k: [c["user_id"] for c in r["recommendations"]] for k, r in status.items()}
    assert top["x1"] == ["A", "B"] and top["x2"] == ["B", "A"], "equal-skill peers must be balanced"
    assert top["x5"] == ["D"] and top["x6"] == ["D"], "the only qualified person must not be pushed out"
    assert any(e["user_id"] == "C" and e["reason"] == "요구 스킬 보유 없음" for e in status["x1"]["excluded"])
    assert any("1명뿐" in w for w in status["x6"]["warnings"])
    assert status["x3"]["warnings"] == ["이미 담당자가 있어 추천하지 않습니다: A"], "no score caveats on skipped items"
    assert status["x4"]["status"] == "needs_manager_input"
    assert saved and saved[0][1] == ["x1", "x2", "x5", "x6"], "only pending items are saved"
    assert assigned == [("A", "x1", "manual")], "only the manager's pick is assigned, once"
    assert status["x1"]["status"] == "assigned" and status["x2"]["status"] == "pending_manager_review"
    assert run(graph, "empty", lambda p: {}).get("results") is None, "no items must end after load"
    print("\nself-check 통과: 동일 스킬끼리만 균형 조정, 유일 적임자 유지, 스킬 0점 제외, "
          "건너뛴 업무 경고 정리, interrupt 후 1건만 배정, 빈 회의 종료.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="회의 단위 담당자 추천 에이전트 (LangGraph 테스트)")
    parser.add_argument("--meeting-id")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--no-llm", action="store_true", help="LLM 해석·설명 모두 생략")
    parser.add_argument("--no-explain", action="store_true", help="LLM 추천 설명 생략")
    parser.add_argument("--save", action="store_true", help="RECOMMENDED_FOR(pending)를 DB에 저장")
    parser.add_argument("--review", action="store_true", help="그래프를 멈추고 관리자가 고르면 ASSIGNED_TO 생성")
    parser.add_argument("--json-out", type=Path, default=Path(DEFAULT_JSON_OUT))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--self-check", action="store_true", help="가짜 DB 도구로 그래프 전체 검사")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    if not args.meeting_id:
        parser.error("--meeting-id가 필요합니다.")
    if args.top < 1:
        parser.error("--top은 1 이상이어야 합니다.")
    if args.review and not sys.stdin.isatty():
        parser.error("--review는 터미널에서만 실행할 수 있습니다.")
    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import DriverError, Neo4jError
        if not rec.load_env(args.env_file):
            print("지정한 .env 파일이 없습니다.", file=sys.stderr)
            return 1
    except ImportError:
        print("neo4j와 python-dotenv가 필요합니다. 프로젝트 requirements.txt를 설치하세요.", file=sys.stderr)
        return 1
    llm = None
    if not args.no_llm:
        if os.getenv("OPENAI_API_KEY"):
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=args.model or os.getenv("OPENAI_MODEL") or "gpt-5.5", timeout=60, max_retries=1)
        else:
            print("OPENAI_API_KEY가 없어 LLM 없이 실행합니다 (REQUIRES_SKILL 없는 업무는 확인 필요로 표시).",
                  file=sys.stderr)
    password = os.getenv("NEO4J_PASSWORD")
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Neo4j 비밀번호: ")
    if not password:
        print("NEO4J_PASSWORD 설정이 필요합니다.", file=sys.stderr)
        return 1

    try:
        with GraphDatabase.driver(os.getenv("NEO4J_URI") or "neo4j://localhost:7687",
                                  auth=(os.getenv("NEO4J_USERNAME") or "neo4j", password)) as driver:
            driver.verify_connectivity()
            with driver.session(database=os.getenv("NEO4J_DATABASE") or "neo4j",
                                notifications_disabled_classifications=["UNRECOGNIZED"]) as session:
                graph = build_graph(neo4j_tools(session), llm, args.top,
                                    explain=not args.no_explain, save=args.save, review=args.review)
                state = run(graph, args.meeting_id, ask_console)
    except (DriverError, Neo4jError, OSError) as exc:
        print(f"Neo4j 처리 실패 ({type(exc).__name__}). 접속 설정·DB 권한을 확인하세요.", file=sys.stderr)
        return 1
    if not state.get("results"):
        print(f"{args.meeting_id}에 미완료 액션 아이템이 없습니다.", file=sys.stderr)
        return 1

    json_out = args.json_out if args.json_out.is_absolute() else SCRIPT_DIR / args.json_out
    json_out.write_text(json.dumps({"meeting_id": args.meeting_id, "items": state["results"]},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n결과 저장: {json_out}")
    print("※ 추천은 후보 제안입니다. 배정은 --review에서 관리자가 고른 것만 DB에 기록됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
