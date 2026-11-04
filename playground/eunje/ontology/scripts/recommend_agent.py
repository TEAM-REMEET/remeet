"""담당자 추천 에이전트 (LangGraph). 설계: 담당자 추천 에이전트 설계 md.

실행 (ontology 폴더에서, load.py 적재 후):
    uv run python scripts/recommend_agent.py            # 담당자 미결정 + 추천 없는 액션아이템
    uv run python scripts/recommend_agent.py --force    # 이미 추천된 건도 다시 (pending만 교체)
    uv run python scripts/recommend_agent.py --self-check   # 가짜 DB·LLM으로 그래프 전체 검사

[run 그래프]  load_items ──Send(액션아이템별 병렬)──▶ process_item ──▶ END
[item 그래프] fetch_context → interpret(LLM) → validate → score → explain(LLM) → save
              (validate 실패 시 interpret 1회 재시도, 그래도 실패하면 give_up → save)

점수·순위는 Python 함수로 계산한다 (점수 계산 섹션). LLM은 해석과 설명만 한다.
ASSIGNED_TO는 만들지 않고 RECOMMENDED_FOR(status='pending')만 남긴다.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import operator
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Annotated, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent  # = ontology/

INTERPRET_PROMPT = (SCRIPT_DIR / "prompts/interpret.md").read_text(encoding="utf-8")
EXPLAIN_PROMPT = (SCRIPT_DIR / "prompts/explain.md").read_text(encoding="utf-8")
RESULTS_DIR = ROOT / "data" / "recommend_results"

POSITIONS = ("senior", "mid", "junior")
PER_POSITION = 2
MAIN_SLOTS = {"senior": 1, "mid": 1, "junior": 2}
MAX_INTERPRET = 2  # 최초 1회 + 재시도 1회
DISCLAIMER = "추천은 후보 제안이며, 최종 배정은 관리자가 검토 후 결정합니다."

# ─────────────────────────────── 고정 쿼리 (LLM은 ID만 넘김) ───────────────────────────────

CATALOG_QUERIES = {
    "skills": "MATCH (s:Skill) RETURN s.skill_id AS id, s.name AS name ORDER BY id",
    "roles": "MATCH (r:Role) RETURN r.role_id AS id, r.name AS name ORDER BY id",
    "teams": "MATCH (t:Team) RETURN t.team_id AS id, t.name AS name ORDER BY id",
    "users": "MATCH (p:Person) RETURN p.user_id AS id, p.name AS name ORDER BY id",
}
ITEMS_QUERY = """
    MATCH (ai:ActionItem {status: 'open'})
    WHERE NOT ()-[:ASSIGNED_TO]->(ai) AND ($force OR NOT ()-[:RECOMMENDED_FOR]->(ai))
    RETURN ai.action_item_id AS id ORDER BY id"""
# 액션아이템은 소주제(Discussion)에서 나온다: Meeting → Agenda → Discussion → ActionItem
# discussion은 그 소주제의 논의 문장들이라, 이 할 일이 어떤 논의 끝에 나왔는지 LLM이 볼 수 있다.
CONTEXT_QUERY = """
    MATCH (pr:Project)<-[:IN]-(mt:Meeting)-[:HAS_AGENDA]->(ag:Agenda)
          -[:HAS_DISCUSSION]->(dn:Discussion)-[:PRODUCED]->(ai:ActionItem {action_item_id: $id})
    OPTIONAL MATCH (ag)-[:ABOUT]->(tp:Topic)
    OPTIONAL MATCH (p:Person)-[:ATTENDED]->(mt)
    RETURN ai.action_item_id AS action_item_id, ai.description AS title, ai.due AS due,
        ai.owner_raw AS owner_raw, mt.meeting_id AS meeting_id, mt.title AS meeting_title,
        toString(mt.held_at) AS held_at, pr.project_id AS project_id, pr.name AS project_name,
        ag.title AS agenda_title, ag.background AS background, ag.decisions AS decisions,
        dn.title AS discussion_title, dn.discussions AS discussion,
        tp.title AS topic_title,
        [x IN collect(DISTINCT p {.user_id, .name}) WHERE x IS NOT NULL] AS attendees"""
# rank_candidates가 기대하는 모양(teams/roles/skills의 id)으로 맞춰 돌려준다.
CANDIDATE_QUERY = """
    MATCH (p:Person)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
    OPTIONAL MATCH (p)-[:HAS_ROLE]->(r:Role)
    WITH p, [x IN collect(DISTINCT t {id: t.team_id, .name}) WHERE x IS NOT NULL] AS teams,
            [x IN collect(DISTINCT r {id: r.role_id, .name}) WHERE x IS NOT NULL] AS roles
    OPTIONAL MATCH (p)-[h:HAS_SKILL]->(s:Skill)
    WITH p, teams, roles, [x IN collect({id: s.skill_id, name: s.name, level: h.level,
                                         verification_status: h.verification_status})
                           WHERE x.id IS NOT NULL] AS skills
    OPTIONAL MATCH (p)-[a:ASSIGNED_TO]->(ai:ActionItem)
    WHERE coalesce(a.work_status, 'in_work') <> 'work_completed' AND coalesce(ai.status, 'open') <> 'completed'
    OPTIONAL MATCH (mt:Meeting)-[:HAS_AGENDA]->(:Agenda)-[:HAS_DISCUSSION]->(:Discussion)-[:PRODUCED]->(ai)
    RETURN p.user_id AS user_id, p.name AS name, p.is_active AS is_active, p.position AS position,
        teams, roles, skills, count(DISTINCT ai) AS open_tasks, collect(mt.meeting_id) AS open_task_meetings
    ORDER BY user_id"""
# 다시 추천하면 pending만 지운다. 관리자가 처리한(accepted/rejected) 추천은 남는다.
CLEAR_PENDING = """MATCH ()-[r:RECOMMENDED_FOR {status: 'pending'}]->(:ActionItem {action_item_id: $id})
    DELETE r"""
SAVE_RECOMMENDATIONS = """
    MATCH (a:ActionItem {action_item_id: $id})
    SET a.recommend_status = $status, a.recommend_run_id = $run_id,
        a.recommended_at = datetime(), a.recommend_warnings = $warnings
    WITH a
    UNWIND $rows AS row
    MATCH (p:Person {user_id: row.user_id})
    CREATE (p)-[r:RECOMMENDED_FOR]->(a)
    SET r = row, r.run_id = $run_id, r.status = 'pending', r.created_at = datetime()"""


# ─────────────────────────────── 점수 계산 (junbyong/graph_rag_test/3_recommend_assignee.py에서 그대로 옮김) ───────────────────────────────

IMPORTANCE = {"core": 0.5, "major": 0.3, "minor": 0.2}
IMPORTANCE_KO = {"core": "핵심", "major": "주요", "minor": "보조"}
LEVEL_MAX = 4
WEIGHTS = {"skill": 0.5, "role_team": 0.2, "workload": 0.3}
ROLE_TEAM_DIRECT, ROLE_TEAM_NEAR = 100.0, 70.0
WORKLOAD_DATA_QUERY = "MATCH ()-[a:ASSIGNED_TO]-(:ActionItem) RETURN count(a) > 0 AS present"


def normalize_spec(raw: dict, catalog: dict) -> tuple[dict, list[str]]:
    """Keep only catalog IDs; anything dropped becomes a warning for the manager."""
    known = {name: {row["id"] for row in rows} for name, rows in catalog.items()}
    warnings: list[str] = []

    def ids(value, table: str, field: str) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        kept = []
        for item in value:
            if item in known[table]:
                if item not in kept:
                    kept.append(item)
            else:
                warnings.append(f"{field}: DB에 없는 ID라서 무시했습니다: {item}")
        return kept

    skills: dict[str, dict] = {}
    for item in raw.get("required_skills") or []:
        if not isinstance(item, dict):
            continue
        skill_id, importance = item.get("skill_id"), item.get("importance")
        if skill_id not in known["skills"]:
            warnings.append(f"required_skills: DB에 없는 스킬이라서 무시했습니다: {skill_id}")
            continue
        if importance not in IMPORTANCE:
            warnings.append(f"required_skills: {skill_id}의 중요도 '{importance}'를 알 수 없어 minor로 처리했습니다.")
            importance = "minor"
        previous = skills.get(skill_id)
        if previous is None or IMPORTANCE[importance] > IMPORTANCE[previous["importance"]]:
            skills[skill_id] = {"skill_id": skill_id, "importance": importance,
                                "reason": item.get("reason") or ""}
    unmatched = [s for s in raw.get("unmatched_skills") or [] if isinstance(s, str) and s.strip()]
    if unmatched:
        warnings.append("DB 스킬 목록에 없는 필요 기술(점수에 반영 안 됨): " + ", ".join(unmatched))
    if not skills:
        raise ValueError("요구 스킬을 DB 스킬 목록에서 하나도 찾지 못해 스킬 점수를 계산할 수 없습니다.")

    team = ids(raw.get("target_team_id"), "teams", "target_team_id")
    if len(team) > 1:
        warnings.append(f"target_team_id가 여러 개라서 첫 번째({team[0]})만 사용합니다.")
    target_roles = ids(raw.get("target_role_ids"), "roles", "target_role_ids")
    adjacent = [r for r in ids(raw.get("adjacent_role_ids"), "roles", "adjacent_role_ids")
                if r not in target_roles]
    spec = {
        "required_skills": list(skills.values()),
        "unmatched_skills": unmatched,
        "target_team_id": team[0] if team else None,
        "target_role_ids": target_roles,
        "adjacent_role_ids": adjacent,
        "role_team_required": bool(raw.get("role_team_required")),
        "explicit_assignee_ids": ids(raw.get("explicit_assignee_ids"), "users", "explicit_assignee_ids"),
        "notes": raw.get("notes") or "",
    }
    if spec["role_team_required"] and not (spec["target_team_id"] or spec["target_role_ids"]):
        warnings.append("필수 역할·팀 조건이 표시됐지만 유효한 팀/역할이 없어 필수 조건을 적용하지 않습니다.")
        spec["role_team_required"] = False
    return spec, warnings


def skill_weights(required_skills: list[dict]) -> dict[str, float]:
    """Importance 0.5/0.3/0.2, then normalized so the weights sum to 1.0."""
    total = sum(IMPORTANCE[s["importance"]] for s in required_skills)
    return {s["skill_id"]: IMPORTANCE[s["importance"]] / total for s in required_skills}


def skill_score(weights: dict[str, float], levels: dict[str, int]) -> float:
    return 100 * sum(w * min(levels.get(skill_id, 0), LEVEL_MAX) / LEVEL_MAX
                     for skill_id, w in weights.items())


def has_role_team_target(spec: dict) -> bool:
    return bool(spec["target_team_id"] or spec["target_role_ids"])


def role_team_score(spec: dict, team_ids: set[str], role_ids: set[str]) -> float | None:
    """None when the task names no target team/role, so the score is omitted."""
    team, roles = spec["target_team_id"], set(spec["target_role_ids"])
    if not (team or roles):
        return None
    in_team = team in team_ids if team else None
    role_match = bool(roles & role_ids) if roles else None
    adjacent = bool(set(spec["adjacent_role_ids"]) & role_ids)
    if roles and team:
        if role_match and in_team:
            return ROLE_TEAM_DIRECT
        return ROLE_TEAM_NEAR if role_match or in_team or adjacent else 0.0
    if roles:
        if role_match:
            return ROLE_TEAM_DIRECT
        return ROLE_TEAM_NEAR if adjacent else 0.0
    return ROLE_TEAM_DIRECT if in_team else 0.0


def meets_requirement(spec: dict, team_ids: set[str], role_ids: set[str]) -> bool:
    if not spec["role_team_required"]:
        return True
    if spec["target_team_id"] and spec["target_team_id"] not in team_ids:
        return False
    if spec["target_role_ids"] and not set(spec["target_role_ids"]) & role_ids:
        return False
    return True


def workload_score(open_tasks: int) -> float:
    return 100 / (1 + open_tasks)


def total_score(skill: float, role_team: float | None, workload: float) -> float:
    if role_team is None:
        return (skill * WEIGHTS["skill"] + workload * WEIGHTS["workload"]) / (
            WEIGHTS["skill"] + WEIGHTS["workload"])
    return (skill * WEIGHTS["skill"] + role_team * WEIGHTS["role_team"]
            + workload * WEIGHTS["workload"])


def rank_candidates(spec: dict, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    weights = skill_weights(spec["required_skills"])
    explicit = set(spec["explicit_assignee_ids"])
    ranked, excluded = [], []
    for c in candidates:
        team_ids = {t["id"] for t in c["teams"]}
        role_ids = {r["id"] for r in c["roles"]}
        person = {"user_id": c["user_id"], "name": c["name"],
                  "teams": c["teams"], "roles": c["roles"]}
        if c["user_id"] in explicit:
            excluded.append({**person, "status": "explicit", "reason": "회의에서 담당자로 명시됨"})
            continue
        if c["is_active"] is not True:
            excluded.append({**person, "status": "excluded", "reason": "비활성 팀원"})
            continue
        if not meets_requirement(spec, team_ids, role_ids):
            excluded.append({**person, "status": "excluded", "reason": "필수 역할·팀 조건 불일치"})
            continue
        levels = {s["id"]: s["level"] or 0 for s in c["skills"]}
        s_score = skill_score(weights, levels)
        if s_score == 0:
            # Workload alone must not make someone a candidate for work they have no skill for.
            excluded.append({**person, "status": "excluded", "reason": "요구 스킬 보유 없음"})
            continue
        rt_score = role_team_score(spec, team_ids, role_ids)
        w_score = workload_score(c["open_tasks"])
        ranked.append({
            **person,
            "total": round(total_score(s_score, rt_score, w_score), 1),
            "skill_score": round(s_score, 1),
            "role_team_score": rt_score,
            "workload_score": round(w_score, 1),
            "open_tasks": c["open_tasks"],
            "skill_evidence": [
                {"skill_id": skill_id, "importance": IMPORTANCE_KO[imp],
                 "weight": round(weights[skill_id], 3), "level": levels.get(skill_id, 0),
                 "verification_status": next((s["verification_status"] for s in c["skills"]
                                              if s["id"] == skill_id), None)}
                for skill_id, imp in ((s["skill_id"], s["importance"]) for s in spec["required_skills"])
            ],
        })
    ranked.sort(key=lambda r: (-r["total"], -r["skill_score"], -r["workload_score"], r["user_id"]))
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    return ranked, excluded


def data_warnings(spec: dict, candidates: list[dict], workload_present: bool) -> list[str]:
    warnings = []
    for s in spec["required_skills"]:
        if not any(sk["id"] == s["skill_id"] and (sk["level"] or 0) > 0
                   for c in candidates for sk in c["skills"]):
            warnings.append(f"요구 스킬 {s['skill_id']}을(를) 가진 팀원이 없습니다.")
    if not workload_present:
        warnings.append("DB에 ASSIGNED_TO 업무 데이터가 없어 모든 후보의 업무량 점수가 100점으로 같습니다.")
    if not has_role_team_target(spec):
        warnings.append("목표 역할·팀이 명시되지 않아 역할·팀 점수를 생략하고 (스킬×0.5+업무량×0.3)÷0.8로 계산했습니다.")
    if any(sk.get("verification_status") == "inferred_unverified"
           for c in candidates for sk in c["skills"]):
        warnings.append("스킬 숙련도는 검증 전 테스트 추론치입니다.")
    return warnings


def response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM이 빈 답변을 반환했습니다.")
    return content.strip()


# ─────────────────────────────── 순수 함수 ───────────────────────────────

def select_by_position(ranked: list[dict]) -> tuple[dict, list[dict], list[str]]:
    """전체 순위에서 직급별 상위 2명, 메인은 senior 1 / mid 1 / junior 2. 부족한 직급은 비워 둔다."""
    by_position = {p: [] for p in POSITIONS}
    warnings = []
    for r in ranked:
        if r.get("position") not in by_position:
            warnings.append(f"{r['name']}({r['user_id']})의 직급 '{r.get('position')}'을(를) 알 수 없어 선발에서 뺐습니다.")
        elif len(by_position[r["position"]]) < PER_POSITION:
            by_position[r["position"]].append(r["user_id"])
    for p, ids in by_position.items():
        if len(ids) < PER_POSITION:
            warnings.append(f"{p} 직급 후보가 {len(ids)}명입니다 (목표 {PER_POSITION}명). 다른 직급으로 채우지 않았습니다.")
    main = [{"slot": p, "user_id": uid} for p in POSITIONS for uid in by_position[p][:MAIN_SLOTS[p]]]
    return by_position, [{"order": i, **m} for i, m in enumerate(main, 1)], warnings


def scores_of(spec: dict, c: dict) -> dict:
    """rank_candidates와 같은 계산. 제외된 사람에게는 참고용 점수로만 쓴다."""
    team_ids, role_ids = {t["id"] for t in c["teams"]}, {r["id"] for r in c["roles"]}
    skill = skill_score(skill_weights(spec["required_skills"]),
                            {s["id"]: s["level"] or 0 for s in c["skills"]})
    role_team = role_team_score(spec, team_ids, role_ids)
    workload = workload_score(c["open_tasks"])
    return {"total": round(total_score(skill, role_team, workload), 1), "skill": round(skill, 1),
            "role_team": role_team, "workload": round(workload, 1)}


def call_json(llm, prompt: str, payload: dict) -> dict:
    response = llm.bind(response_format={"type": "json_object"}).invoke(
        [("system", prompt), ("human", json.dumps(payload, ensure_ascii=False, default=str))])
    out = json.loads(response_text(response))
    if not isinstance(out, dict):
        raise ValueError("LLM 결과가 JSON 객체가 아닙니다.")
    return out


def build_output(s: dict) -> dict:
    """담당자 지정 모달용 JSON. 화면은 점수를 계산하지 않고 이 값을 그대로 쓴다."""
    ctx = s.get("context") or {"action_item_id": s["item_id"]}
    spec, catalog = s.get("spec"), s["catalog"]
    by_position, main = s.get("by_position") or {}, s.get("main") or []
    ranked = {r["user_id"]: r for r in s.get("ranked") or []}
    excluded = {e["user_id"]: e["reason"] for e in s.get("excluded") or []}
    explained = s.get("explained") or {}
    pool = {uid: (p, i) for p, ids in by_position.items() for i, uid in enumerate(ids, 1)}
    main_slot = {m["user_id"]: m["slot"] for m in main}
    required = {x["skill_id"] for x in spec["required_skills"]} if spec else set()
    attendees = {a["user_id"] for a in ctx.get("attendees", [])}
    skill_names = {k["id"]: k["name"] for k in catalog["skills"]}

    people, teams = {}, {}
    for c in s.get("candidates") or []:
        uid = c["user_id"]
        scores = scores_of(spec, c) if spec else None
        position, rank_in_position = pool.get(uid, (None, None))
        people[uid] = {
            "user_id": uid, "name": c["name"],
            "team": next(({"team_id": t["id"], "name": t["name"]} for t in c["teams"]), None),
            "teams": [{"team_id": t["id"], "name": t["name"]} for t in c["teams"]],
            "role": next(({"role_id": r["id"], "name": r["name"]} for r in c["roles"]), None),
            "position": c.get("position"), "is_attendee": uid in attendees,
            "eligible": uid in ranked, "excluded_reason": excluded.get(uid),
            "recommendation": {"is_main": uid in main_slot, "slot": main_slot.get(uid),
                               "rank_in_position": rank_in_position,
                               "overall_rank": ranked[uid]["rank"] if uid in ranked else None},
            "scores": scores,
            "open_tasks": {"total": c["open_tasks"],
                           "from_this_meeting": c.get("open_task_meetings", []).count(ctx.get("meeting_id"))},
            "skills": [{"skill_id": k["id"], "name": k["name"], "level": k["level"],
                        "matched": k["id"] in required and (k["level"] or 0) > 0,
                        "verification_status": k.get("verification_status")} for k in c["skills"]],
            "reason": explained.get("reasons", {}).get(uid),
        }
        for t in c["teams"]:
            teams.setdefault(t["id"], {"team_id": t["id"], "name": t["name"], "member_ids": []})["member_ids"].append(uid)
    for t in teams.values():  # 추천 후보 → 지정 가능 → 총점 순
        t["member_ids"].sort(key=lambda u: (u not in pool, u not in ranked,
                                            -((people[u]["scores"] or {}).get("total") or 0), u))

    return {
        "action_item": {k: v for k, v in ctx.items() if k != "attendees"},
        "recommendation": {
            "run_id": s["run_id"], "status": s["status"],
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "selection_rule": "by_position",
            "explanation": explained.get("explanation") or DISCLAIMER,
            "manager_checks": explained.get("manager_checks", []),
            "warnings": s.get("warnings", []),
        },
        "score_axes": [
            {"key": "skill", "label": "스킬", "weight": WEIGHTS["skill"], "applied": bool(spec)},
            {"key": "role_team", "label": "부서·역할", "weight": WEIGHTS["role_team"],
             "applied": bool(spec) and has_role_team_target(spec)},
            {"key": "workload", "label": "업무 여유", "weight": WEIGHTS["workload"], "applied": bool(spec)},
        ],
        "required_skills": [{**x, "name": skill_names.get(x["skill_id"])} for x in spec["required_skills"]] if spec else [],
        "unmatched_skills": spec["unmatched_skills"] if spec else [],
        "main_recommendations": main,
        "candidates_by_position": by_position,
        "teams": sorted(teams.values(), key=lambda t: t["team_id"]),
        "people": people,
    }


def recommend_rows(output: dict) -> list[dict]:
    """RECOMMENDED_FOR 속성. 후보 풀(최대 6명)만."""
    rows = []
    for position, ids in output["candidates_by_position"].items():
        for uid in ids:
            p = output["people"][uid]
            rows.append({"user_id": uid, "position": position,
                         "rank_in_position": p["recommendation"]["rank_in_position"],
                         "is_main": p["recommendation"]["is_main"], "slot": p["recommendation"]["slot"],
                         "total": p["scores"]["total"], "skill_score": p["scores"]["skill"],
                         "role_team_score": p["scores"]["role_team"], "workload_score": p["scores"]["workload"],
                         "open_tasks": p["open_tasks"]["total"], "reason": p["reason"]})
    return rows


# ─────────────────────────────── 그래프 ───────────────────────────────

class ItemState(TypedDict, total=False):
    item_id: str
    run_id: str
    catalog: dict
    context: dict
    candidates: list[dict]
    workload_present: bool
    attempts: int
    raw_spec: dict | None
    failure: str | None
    failure_kind: str | None  # 'llm' | 'skill'
    spec: dict
    ranked: list[dict]
    excluded: list[dict]
    by_position: dict
    main: list[dict]
    explained: dict
    warnings: list[str]
    status: str
    output: dict


class RunState(TypedDict, total=False):
    run_id: str
    force: bool
    catalog: dict
    item_ids: list[str]
    results: Annotated[list[dict], operator.add]


def build_graph(tools: dict, llm, out_dir: Path):
    """tools: catalog(), items(force), context(id), candidates() -> (rows, workload_present),
    save(id, run_id, status, warnings, rows). 주입받아서 같은 그래프가 Neo4j와 가짜(--self-check)에서 돈다."""

    def fetch_context(s: ItemState) -> dict:
        try:
            context = tools["context"](s["item_id"])
            candidates, workload_present = tools["candidates"]()
        except Exception as exc:
            return {"status": "error", "warnings": [f"맥락·후보 조회 실패 ({type(exc).__name__})"]}
        if context is None:
            return {"status": "error", "warnings": ["액션아이템의 회의 맥락을 찾지 못했습니다."]}
        return {"context": context, "candidates": candidates, "workload_present": workload_present,
                "attempts": 0, "warnings": []}

    def interpret(s: ItemState) -> dict:
        previous = ({"spec": s.get("raw_spec"), "failure": s["failure"]}
                    if s.get("failure_kind") == "skill" else None)
        payload = {"action_item": s["context"], "catalog": s["catalog"], "previous_attempt": previous}
        try:
            raw = call_json(llm, INTERPRET_PROMPT, payload)
        except Exception as exc:
            return {"attempts": s["attempts"] + 1, "raw_spec": None, "failure_kind": "llm",
                    "failure": f"LLM 해석 호출 실패 ({type(exc).__name__})"}
        return {"attempts": s["attempts"] + 1, "raw_spec": raw, "failure": None, "failure_kind": None}

    def validate(s: ItemState) -> dict:
        if s.get("failure_kind") == "llm":
            return {}
        try:
            spec, warnings = normalize_spec(s["raw_spec"], s["catalog"])
        except ValueError as exc:
            return {"failure": str(exc), "failure_kind": "skill"}
        return {"spec": spec, "warnings": s["warnings"] + warnings}

    def give_up(s: ItemState) -> dict:
        status = "error" if s.get("failure_kind") == "llm" else "skill_unclear"
        return {"status": status, "warnings": s["warnings"] + [f"{s['failure']} (해석 {s['attempts']}회 시도)"]}

    def score(s: ItemState) -> dict:
        ranked, excluded = rank_candidates(s["spec"], s["candidates"])
        position = {c["user_id"]: c.get("position") for c in s["candidates"]}
        ranked = [{**r, "position": position[r["user_id"]]} for r in ranked]
        by_position, main, warnings = select_by_position(ranked)
        warnings = s["warnings"] + data_warnings(s["spec"], s["candidates"], s["workload_present"]) + warnings
        return {"ranked": ranked, "excluded": excluded, "by_position": by_position, "main": main,
                "warnings": warnings, "status": "pending_manager_review" if main else "no_candidate"}

    def explain(s: ItemState) -> dict:
        pool = {uid for ids in s["by_position"].values() for uid in ids}
        main = {m["user_id"]: m["slot"] for m in s["main"]}
        payload = {"action_item": s["context"], "spec": s["spec"], "warnings": s["warnings"],
                   "candidates": [{**r, "is_main": r["user_id"] in main, "slot": main.get(r["user_id"])}
                                  for r in s["ranked"] if r["user_id"] in pool],
                   "excluded": s["excluded"]}
        try:
            out = call_json(llm, EXPLAIN_PROMPT, payload)
        except Exception as exc:
            return {"warnings": s["warnings"] + [f"LLM 설명 생성 실패 ({type(exc).__name__}). 점수 결과만 저장했습니다."]}
        reasons = out.get("reasons") if isinstance(out.get("reasons"), dict) else {}
        checks = out.get("manager_checks") if isinstance(out.get("manager_checks"), list) else []
        return {"explained": {
            "explanation": str(out.get("explanation") or DISCLAIMER),
            "manager_checks": [str(c) for c in checks],
            "reasons": {uid: str(text) for uid, text in reasons.items() if uid in pool},  # 후보 풀 밖은 버림
        }}

    def save(s: ItemState) -> dict:
        output = build_output(s)
        warnings = output["recommendation"]["warnings"]
        try:
            tools["save"](s["item_id"], s["run_id"], s["status"], warnings, recommend_rows(output))
        except Exception as exc:
            warnings.append(f"Neo4j 저장 실패 ({type(exc).__name__}). JSON 파일만 저장했습니다.")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{s['item_id']}.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except OSError as exc:  # 결과는 run_{run_id}.json에도 남는다
            print(f"[{s['item_id']}] 결과 JSON 저장 실패 ({type(exc).__name__})", file=sys.stderr)
        print(f"  [{s['status']}] {s['item_id']}  메인 {len(output['main_recommendations'])}명")
        return {"output": output}

    def after_validate(s: ItemState) -> str:
        if s.get("spec"):
            return "score"
        return "interpret" if s["attempts"] < MAX_INTERPRET else "give_up"

    item = StateGraph(ItemState)
    for name, node in [("fetch_context", fetch_context), ("interpret", interpret), ("validate", validate),
                       ("give_up", give_up), ("score", score), ("explain", explain), ("save", save)]:
        item.add_node(name, node)
    item.add_edge(START, "fetch_context")
    item.add_conditional_edges("fetch_context", lambda s: "save" if s.get("status") == "error" else "interpret",
                               ["save", "interpret"])
    item.add_edge("interpret", "validate")
    item.add_conditional_edges("validate", after_validate, ["score", "interpret", "give_up"])
    item.add_edge("give_up", "save")
    item.add_conditional_edges("score", lambda s: "explain" if s["main"] else "save", ["explain", "save"])
    item.add_edge("explain", "save")
    item.add_edge("save", END)
    item_graph = item.compile()

    def load_items(s: RunState) -> dict:
        return {"catalog": tools["catalog"](), "item_ids": tools["items"](s.get("force", False))}

    def fan_out(s: RunState):
        return [Send("process_item", {"item_id": i, "run_id": s["run_id"], "catalog": s["catalog"]})
                for i in s["item_ids"]] or END

    def process_item(payload: dict) -> dict:
        try:
            return {"results": [item_graph.invoke(payload)["output"]]}
        except Exception as exc:  # 한 건이 실패해도 다른 건에 영향 없게
            print(f"[{payload['item_id']}] 처리 실패 ({type(exc).__name__})", file=sys.stderr)
            return {"results": [{"action_item": {"action_item_id": payload["item_id"]},
                                 "recommendation": {"run_id": payload["run_id"], "status": "error",
                                                    "warnings": [f"처리 실패 ({type(exc).__name__})"]}}]}

    run = StateGraph(RunState)
    run.add_node("load_items", load_items)
    run.add_node("process_item", process_item)
    run.add_edge(START, "load_items")
    run.add_conditional_edges("load_items", fan_out, ["process_item", END])
    run.add_edge("process_item", END)
    return run.compile()


def run_once(graph, force: bool, out_dir: Path) -> list[dict]:
    run_id = uuid.uuid4().hex[:12]
    results = graph.invoke({"run_id": run_id, "force": force}).get("results") or []
    if results:  # 미결정 액션아이템이 없으면 아무것도 쓰지 않는다
        (out_dir / f"run_{run_id}.json").write_text(
            json.dumps({"run_id": run_id, "items": results}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    return results


# ─────────────────────────────── Neo4j ───────────────────────────────

def neo4j_tools(driver) -> dict:
    # Send로 노드가 병렬 실행되므로 세션을 공유하지 않고 driver.execute_query / 호출마다 새 세션을 쓴다.
    def q(query, **params):
        return driver.execute_query(query, params, routing_="r").records

    def save_tx(tx, item_id, run_id, status, warnings, rows):
        tx.run(CLEAR_PENDING, id=item_id).consume()
        tx.run(SAVE_RECOMMENDATIONS, id=item_id, run_id=run_id, status=status,
               warnings=warnings, rows=rows).consume()

    def save(*args):
        with driver.session() as session:
            session.execute_write(save_tx, *args)

    def context(item_id):
        records = q(CONTEXT_QUERY, id=item_id)
        return records[0].data() if records else None

    return {
        "catalog": lambda: {name: [r.data() for r in q(query)] for name, query in CATALOG_QUERIES.items()},
        "items": lambda force: [r["id"] for r in q(ITEMS_QUERY, force=force)],
        "context": context,
        "candidates": lambda: ([r.data() for r in q(CANDIDATE_QUERY)],
                               q(WORKLOAD_DATA_QUERY)[0]["present"]),
        "save": save,
    }


# ─────────────────────────────── self-check ───────────────────────────────

class FakeLLM:
    """interpret 답은 action_item_id별 큐에서 꺼낸다. Exception이면 호출 실패로 취급."""

    def __init__(self, interpret_answers: dict):
        self.answers = interpret_answers

    def bind(self, **_):
        return self

    def invoke(self, messages):
        payload = json.loads(messages[1][1])
        if "previous_attempt" in payload:
            answer = self.answers[payload["action_item"]["action_item_id"]].pop(0)
        else:
            answer = {"explanation": "설명", "manager_checks": ["확인"], "reasons": {"s1": "스킬 일치", "zz": "풀 밖"}}
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=json.dumps(answer))


def self_check() -> None:
    def person(uid, position, level, active=True):
        return {"user_id": uid, "name": uid, "is_active": active, "position": position,
                "teams": [{"id": "t1", "name": "팀1"}], "roles": [], "open_tasks": 0, "open_task_meetings": [],
                "skills": [{"id": "A", "name": "A", "level": level, "verification_status": None}]}
    people = [person("s1", "senior", 4), person("s2", "senior", 2), person("s3", "senior", 3),
              person("m1", "mid", 3), person("j1", "junior", 1), person("x1", None, 4), person("off", "mid", 4, False)]
    people[3]["open_tasks"], people[3]["open_task_meetings"] = 1, ["m-1"]
    good = {"required_skills": [{"skill_id": "A", "importance": "core"}]}
    bad = {"required_skills": [{"skill_id": "NOPE", "importance": "core"}]}
    llm = FakeLLM({"ok": [bad, good], "unclear": [bad, bad], "llmfail": [RuntimeError(), RuntimeError()],
                   "nocand": [{"required_skills": [{"skill_id": "B", "importance": "core"}]}]})
    saved = {}
    tools = {
        "catalog": lambda: {"skills": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}], "roles": [], "teams": [],
                            "users": [{"id": p["user_id"]} for p in people]},
        "items": lambda force: ["ok", "unclear", "llmfail", "nocand", "gone"],
        "context": lambda i: None if i == "gone" else {"action_item_id": i, "meeting_id": "m-1",
                                                      "attendees": [{"user_id": "s1", "name": "s1"}]},
        "candidates": lambda: (people, True),
        "save": lambda i, run_id, status, warnings, rows: saved.update({i: (status, rows)}),
    }
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        results = run_once(build_graph(tools, llm, out_dir), False, out_dir)
        files = sorted(p.name for p in out_dir.iterdir())
    by_id = {r["action_item"]["action_item_id"]: r for r in results}
    status = {k: r["recommendation"]["status"] for k, r in by_id.items()}
    assert status == {"ok": "pending_manager_review", "unclear": "skill_unclear", "llmfail": "error",
                      "nocand": "no_candidate", "gone": "error"}, status
    ok = by_id["ok"]
    assert ok["candidates_by_position"] == {"senior": ["s1", "s3"], "mid": ["m1"], "junior": ["j1"]}
    assert [(m["slot"], m["user_id"]) for m in ok["main_recommendations"]] == [
        ("senior", "s1"), ("mid", "m1"), ("junior", "j1")], "부족한 직급은 다른 직급으로 채우지 않는다"
    warns = " ".join(ok["recommendation"]["warnings"])
    assert "mid 직급 후보가 1명" in warns and "junior 직급 후보가 1명" in warns and "x1(x1)" in warns
    assert ok["people"]["off"]["eligible"] is False and ok["people"]["off"]["excluded_reason"] == "비활성 팀원"
    assert ok["people"]["s1"]["is_attendee"] and ok["people"]["m1"]["open_tasks"] == {"total": 1, "from_this_meeting": 1}
    assert ok["people"]["s1"]["reason"] == "스킬 일치" and "zz" not in ok["people"]
    assert ok["teams"][0]["member_ids"][:4] == ["s1", "s3", "m1", "j1"], ok["teams"][0]["member_ids"]
    rows = saved["ok"][1]
    assert len(rows) == 4 and sum(r["is_main"] for r in rows) == 3 and saved["nocand"] == ("no_candidate", [])
    assert files == sorted(["ok.json", "unclear.json", "llmfail.json", "nocand.json", "gone.json",
                            f"run_{ok['recommendation']['run_id']}.json"]), files

    empty = dict(tools, items=lambda force: [])
    with tempfile.TemporaryDirectory() as tmp:
        assert run_once(build_graph(empty, llm, Path(tmp)), False, Path(tmp)) == [] and not any(Path(tmp).iterdir())
    print("self-check 통과: 재시도 후 성공, skill_unclear / error / no_candidate, 직급별 선발, 빈 슬롯 유지, "
          "후보 풀 밖 reason 제거, 미결정 없으면 아무것도 안 씀.")


# ─────────────────────────────── main ───────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="담당자 추천 에이전트 (LangGraph)")
    parser.add_argument("--force", action="store_true", help="이미 RECOMMENDED_FOR가 있는 건도 다시 추천")
    parser.add_argument("--model", help="미지정 시 OPENAI_MODEL / LLM_MODEL, 없으면 gpt-5.5")
    parser.add_argument("--self-check", action="store_true", help="가짜 DB·LLM으로 그래프 전체 검사")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0

    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI
    from neo4j import GraphDatabase
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parents[2] / ".env")  # repo root
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("NEO4J_PASSWORD"):
        print("OPENAI_API_KEY와 NEO4J_PASSWORD 설정이 필요합니다.", file=sys.stderr)
        return 1
    llm = ChatOpenAI(model=args.model or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-5.5",
                     timeout=60, max_retries=1)
    with GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                              auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
                              notifications_disabled_classifications=["UNRECOGNIZED"]) as driver:
        driver.verify_connectivity()
        RESULTS_DIR.mkdir(exist_ok=True)
        results = run_once(build_graph(neo4j_tools(driver), llm, RESULTS_DIR), args.force, RESULTS_DIR)
    if not results:
        print("담당자 미결정 액션아이템이 없습니다.")
        return 0
    print(f"\n{len(results)}건 처리, 결과: {RESULTS_DIR}")
    print("※ " + DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
