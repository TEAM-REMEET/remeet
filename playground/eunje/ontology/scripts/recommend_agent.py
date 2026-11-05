"""담당자 추천 에이전트 (LangGraph). 설계: 담당자 추천 에이전트 설계 md.

실행 (ontology 폴더에서, load.py 적재 후):
    uv run python scripts/recommend_agent.py            # 담당자 미결정 + 추천 없는 액션아이템
    uv run python scripts/recommend_agent.py --force    # 이미 추천된 건도 다시 (pending만 교체)
    uv run python scripts/recommend_agent.py --reset --effort low   # 추천 결과를 전부 지우고 처음부터
    uv run python scripts/recommend_agent.py --self-check   # 가짜 DB·LLM으로 그래프 전체 검사
실행이 끝나면 비용을 data/reports/cost_log.jsonl에 쌓고 data/reports/recommend_report/<label>/<YYYYMMDD-HHMM>.md 리포트를 만든다.
.env에 LANGSMITH_TRACING=true면 LangGraph 실행 전체(노드·LLM 호출)가 LangSmith에 'recommend_agent'로 남는다.

[run 그래프]  load_items ──Send(액션아이템별 병렬)──▶ process_item ──▶ END
[item 그래프] fetch_context → interpret(LLM) → validate → score → save
              (validate 실패 시 interpret 1회 재시도. 그래도 스킬을 못 고르면 borrow: 이름이 가장 비슷한
               다른 액션아이템의 REQUIRES_SKILL을 빌려 score로, 빌릴 것도 없으면 give_up → save)
회의록에 담당자가 명시됐거나(meeting_explicit) 선행 업무 담당자가 있는(history) 액션아이템은
load.py가 ASSIGNED_TO로 바로 이어서 여기 대상에서 빠진다.

점수·순위는 Python 함수로 계산한다 (점수 계산 섹션). LLM은 요구 스킬 해석만 한다.
Neo4j에 남기는 것 (파일은 만들지 않는다):
  (Person)-[:RECOMMENDED_FOR {status:'pending', score, skill_score, role_team_score, workload_score, rank}]->(ActionItem)
      직급별 Top-2에게만. rank = 같은 직급 안 순서(1|2)
  (ActionItem)-[:REQUIRES_SKILL {importance, reason, source:'llm'|'similar_item'}]->(Skill)
  (ActionItem)-[:BORROWED_SKILLS_FROM]->(ActionItem)   유사 업무에서 스킬을 빌렸을 때만
  ActionItem.recommend_status = 'no_candidate'|'skill_unclear' (추천 엣지가 없는 경우만), ActionItem.missing_skills
조회·LLM 실패는 아무것도 쓰지 않는다 → 추천 엣지·recommend_status가 없으니 다음 실행 때 자동으로 다시 처리된다.
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import operator
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Annotated, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent  # = ontology/

INTERPRET_PROMPT = (SCRIPT_DIR / "prompts/interpret.md").read_text(encoding="utf-8")

POSITIONS = ("senior", "mid", "junior")
PER_POSITION = 2
MAX_INTERPRET = 2  # 최초 1회 + 재시도 1회
DISCLAIMER = "추천은 후보 제안이며, 최종 배정은 관리자가 검토 후 결정합니다."
LLM_MODEL, LLM_EFFORT = "gpt-6-luna", "high"
LLM_RETRIES = 3  # 일시적 오류(네트워크·rate limit)는 여기서 해결. 그래도 실패하면 저장 안 하고 다음 실행에 다시

# ─────────────────────────────── 고정 쿼리 (LLM은 ID만 넘김) ───────────────────────────────

CATALOG_QUERIES = {
    "skills": "MATCH (s:Skill) RETURN s.skill_id AS id, s.name AS name, s.description AS description ORDER BY id",
    "roles": "MATCH (r:Role) RETURN r.role_id AS id, r.name AS name ORDER BY id",
    "teams": "MATCH (t:Team) RETURN t.team_id AS id, t.name AS name ORDER BY id",
    "users": "MATCH (p:Person) RETURN p.user_id AS id, p.name AS name ORDER BY id",
}
ITEMS_QUERY = """
    MATCH (ai:ActionItem {status: 'open'})
    WHERE NOT ()-[:ASSIGNED_TO]->(ai)
      AND ($force OR (NOT ()-[:RECOMMENDED_FOR]->(ai) AND ai.recommend_status IS NULL))
    RETURN ai.action_item_id AS id ORDER BY id"""
# 액션아이템은 소주제(Discussion)에서 나온다: Meeting → Agenda → Discussion → ActionItem
# discussion은 그 소주제의 논의 문장들이라, 이 할 일이 어떤 논의 끝에 나왔는지 LLM이 볼 수 있다.
CONTEXT_QUERY = """
    MATCH (pr:Project)<-[:IN]-(mt:Meeting)-[:HAS_AGENDA]->(ag:Agenda)
          -[:HAS_DISCUSSION]->(dn:Discussion)-[:PRODUCED]->(ai:ActionItem {action_item_id: $id})
    OPTIONAL MATCH (ag)-[:ABOUT]->(tp:Topic)
    OPTIONAL MATCH (p:Person)-[:ATTENDED]->(mt)
    RETURN ai.action_item_id AS action_item_id, ai.description AS title, ai.due AS due,
        ai.owner_raw AS owner_raw, ai.status AS status, mt.meeting_id AS meeting_id, mt.title AS meeting_title,
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
    WITH p, teams, roles, [x IN collect({id: s.skill_id, name: s.name, level: h.level})
                           WHERE x.id IS NOT NULL] AS skills
    OPTIONAL MATCH (p)-[a:ASSIGNED_TO]->(ai:ActionItem)
    WHERE coalesce(a.work_status, 'in_work') <> 'work_completed' AND coalesce(ai.status, 'open') <> 'completed'
    RETURN p.user_id AS user_id, p.name AS name, p.is_active AS is_active, p.position AS position,
        teams, roles, skills, count(DISTINCT ai) AS open_tasks
    ORDER BY user_id"""
# 다시 추천하면 pending만 지운다. 관리자가 처리한(selected/not_selected) 추천은 남는다.
CLEAR_PENDING = """MATCH ()-[r:RECOMMENDED_FOR {status: 'pending'}]->(:ActionItem {action_item_id: $id})
    DELETE r"""
# recommend_status / missing_skills에 null을 넣으면 속성이 지워진다 (추천 성공 = 엣지만 남음)
SAVE_RECOMMENDATIONS = """
    MATCH (a:ActionItem {action_item_id: $id})
    SET a.recommend_status = $status, a.missing_skills = $missing
    WITH a
    UNWIND $rows AS row
    MATCH (p:Person {user_id: row.user_id})
    CREATE (p)-[r:RECOMMENDED_FOR]->(a)
    SET r.status = 'pending', r.score = row.score, r.skill_score = row.skill_score,
        r.role_team_score = row.role_team_score, r.workload_score = row.workload_score, r.rank = row.rank"""
# LLM이 만든 것만 교체한다. 나중에 사람이 고친 REQUIRES_SKILL(source가 다른 것)은 건드리지 않는다.
SAVE_REQUIRED_SKILLS = """
    MATCH (a:ActionItem {action_item_id: $id})
    OPTIONAL MATCH (a)-[old:REQUIRES_SKILL]->(:Skill) WHERE old.source IN ['llm', 'similar_item']
    DELETE old
    WITH DISTINCT a
    OPTIONAL MATCH (a)-[b:BORROWED_SKILLS_FROM]->(:ActionItem)
    DELETE b
    WITH DISTINCT a
    OPTIONAL MATCH (src:ActionItem {action_item_id: $borrowed_from})
    FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END | CREATE (a)-[:BORROWED_SKILLS_FROM]->(src))
    WITH DISTINCT a
    UNWIND $skills AS sk
    MATCH (s:Skill {skill_id: sk.skill_id})
    CREATE (a)-[r:REQUIRES_SKILL]->(s)
    SET r.importance = sk.importance, r.reason = sk.reason, r.source = $source"""
# --reset: 이 스크립트가 만든 것(추천 엣지 전부, LLM·빌려 온 REQUIRES_SKILL, 추천 속성)을 지운다. ASSIGNED_TO는 남는다.
# REMOVE의 recommend_run_id 이후는 예전 구조에서 쓰던 속성이라 남아 있으면 같이 지운다.
RESET_QUERIES = [
    "MATCH ()-[r:RECOMMENDED_FOR]->(:ActionItem) DELETE r",
    "MATCH (:ActionItem)-[r:REQUIRES_SKILL]->(:Skill) WHERE r.source IN ['llm', 'similar_item'] DELETE r",
    "MATCH (:ActionItem)-[r:BORROWED_SKILLS_FROM]->(:ActionItem) DELETE r",
    """MATCH (a:ActionItem)
    REMOVE a.recommend_status, a.missing_skills, a.recommend_run_id, a.recommended_at, a.recommend_warnings,
           a.skill_source, a.skill_source_item_id, a.recommend_notices""",
]
# 빌려 온 스킬(similar_item)은 다시 빌려주지 않는다. 추측이 꼬리를 물고 퍼지지 않게.
SIMILAR_SKILLS_QUERY = """
    MATCH (other:ActionItem)-[r:REQUIRES_SKILL]->(s:Skill)
    WHERE other.action_item_id <> $id AND r.source <> 'similar_item'
    RETURN other.action_item_id AS id, other.description AS description,
        collect({skill_id: s.skill_id, importance: r.importance, reason: r.reason}) AS required_skills"""


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
                 "weight": round(weights[skill_id], 3), "level": levels.get(skill_id, 0)}
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
    return warnings


def response_text(response) -> str:
    content = response.content
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM이 빈 답변을 반환했습니다.")
    return content.strip()


# ─────────────────────────────── 순수 함수 ───────────────────────────────

def first_tier(attendee_ids: set[str], candidates: list[dict]) -> set[str]:
    """1차 풀: 회의 참석자 + 참석자와 같은 팀 사람."""
    teams = {t["id"] for c in candidates if c["user_id"] in attendee_ids for t in c["teams"]}
    return attendee_ids | {c["user_id"] for c in candidates if teams & {t["id"] for t in c["teams"]}}


def select_by_position(ranked: list[dict]) -> tuple[dict, list[str]]:
    """직급별 2명. 1차 풀(tier 1)에서 순위대로 먼저 채우고, 모자란 자리만 팀 외(tier 2)에서 채운다.
    그래도 부족한 직급은 비워 둔다. 리스트 순서가 직급 안 순위(rank)다."""
    by_position = {p: [] for p in POSITIONS}
    warnings, outside = [], set()
    for r in sorted(ranked, key=lambda r: r["tier"]):  # 안정 정렬이라 tier 안에서는 순위가 유지된다
        if r.get("position") not in by_position:
            warnings.append(f"{r['name']}({r['user_id']})의 직급 '{r.get('position')}'을(를) 알 수 없어 선발에서 뺐습니다.")
        elif len(by_position[r["position"]]) < PER_POSITION:
            by_position[r["position"]].append(r["user_id"])
            if r["tier"] == 2:
                outside.add(r["position"])
    for p, ids in by_position.items():
        if p in outside:
            warnings.append(f"{p} 직급은 회의 참석자·같은 팀 후보가 부족해 팀 외 후보로 채웠습니다.")
        if len(ids) < PER_POSITION:
            warnings.append(f"{p} 직급 후보가 {len(ids)}명입니다 (목표 {PER_POSITION}명). 다른 직급으로 채우지 않았습니다.")
    return by_position, warnings


def most_similar(title: str, rows: list[dict]) -> tuple[dict, float] | None:
    """이름(description)이 가장 비슷한 액션아이템. 같으면 ID 순."""
    # shortcut: 글자 단위 유사도(difflib)라 뜻이 같아도 표현이 다르면 못 잡는다. 정확도가 부족하면 임베딩/LLM 비교로 바꾼다.
    scored = [(SequenceMatcher(None, title, r["description"] or "").ratio(), r)
              for r in sorted(rows, key=lambda r: r["id"])]
    if not scored:
        return None
    ratio, best = max(scored, key=lambda x: x[0])  # max는 같은 값이면 앞(ID가 작은 쪽)을 고른다
    return best, ratio


def llm_context(ctx: dict) -> dict:
    """LLM에는 ActionItem과 그 위 Discussion, Agenda의 정해진 속성만 준다."""
    return {
        "action_item": {"action_item_id": ctx["action_item_id"], "description": ctx.get("title"),
                        "due": ctx.get("due"), "owner_raw": ctx.get("owner_raw"), "status": ctx.get("status")},
        "discussion": {"title": ctx.get("discussion_title"), "discussions": ctx.get("discussion") or []},
        "agenda": {"title": ctx.get("agenda_title"), "background": ctx.get("background") or []},
    }


LLM_CALLS: list[dict] = []  # cost.summarize가 읽는 모양 {model, usage}. Send 병렬에서도 list.append는 안전하다


def call_json(llm, prompt: str, payload: dict) -> dict:
    response = llm.bind(response_format={"type": "json_object"}).invoke(
        [("system", prompt), ("human", json.dumps(payload, ensure_ascii=False, default=str))])
    meta = getattr(response, "response_metadata", None) or {}
    if meta.get("token_usage"):  # OpenAI 원본 usage (cached·reasoning 토큰 포함)
        LLM_CALLS.append({"model": meta.get("model_name"), "usage": meta["token_usage"]})
    out = json.loads(response_text(response))
    if not isinstance(out, dict):
        raise ValueError("LLM 결과가 JSON 객체가 아닙니다.")
    return out


def build_result(s: dict) -> dict:
    """save가 Neo4j에 쓰는 값. RECOMMENDED_FOR는 직급별 Top-2에게만, rank는 직급 안 순서."""
    spec = s.get("spec")
    ranked = {r["user_id"]: r for r in s.get("ranked") or []}
    rows = [{"user_id": uid, "position": position, "rank": i, "score": ranked[uid]["total"],
             "skill_score": ranked[uid]["skill_score"], "role_team_score": ranked[uid]["role_team_score"],
             "workload_score": ranked[uid]["workload_score"]}
            for position, ids in (s.get("by_position") or {}).items() for i, uid in enumerate(ids, 1)]
    return {
        "action_item_id": s["item_id"], "status": s["status"],
        "required_skills": [{k: x[k] for k in ("skill_id", "importance", "reason")}
                            for x in spec["required_skills"]] if spec else [],
        "skill_source": s.get("skill_source", "llm"), "borrowed_from": s.get("borrowed_from"),
        "missing_skills": s.get("missing_skills") or None,
        "recommendations": rows, "warnings": s.get("warnings", []),
    }


# ─────────────────────────────── 그래프 ───────────────────────────────

class ItemState(TypedDict, total=False):
    item_id: str
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
    by_position: dict
    warnings: list[str]  # 터미널 출력용 (저장하지 않음)
    missing_skills: list[str]  # LLM이 필요하다고 봤지만 스킬 목록에 없는 기술
    skill_source: str  # 'llm' | 'similar_item'
    borrowed_from: str | None
    status: str  # 'pending_manager_review' | 'no_candidate' | 'skill_unclear' | 'error'(저장 안 함)
    output: dict


class RunState(TypedDict, total=False):
    force: bool
    catalog: dict
    item_ids: list[str]
    results: Annotated[list[dict], operator.add]


def build_graph(tools: dict, llm):
    """tools: catalog(), items(force), context(id), candidates() -> (rows, workload_present),
    similar(id) -> REQUIRES_SKILL이 있는 다른 액션아이템들, save(result). 주입받아서 같은 그래프가 Neo4j와 가짜(--self-check)에서 돈다."""

    def fetch_context(s: ItemState) -> dict:
        try:
            context = tools["context"](s["item_id"])
            candidates, workload_present = tools["candidates"]()
        except Exception as exc:
            return {"status": "error", "warnings": [f"맥락·후보 조회 실패 ({type(exc).__name__})"]}
        if context is None:
            return {"status": "error", "warnings": ["액션아이템의 회의 맥락을 찾지 못했습니다."]}
        # 명시된 담당자는 load.py가 이미 ASSIGNED_TO로 이었다. 여기 남은 owner_raw는 참석자와 못 맞춘 것.
        warnings = ([f"회의록 담당자 '{context['owner_raw']}'를 참석자와 맞추지 못해 추천을 실행했습니다."]
                    if context.get("owner_raw") else [])
        return {"context": context, "candidates": candidates, "workload_present": workload_present,
                "attempts": 0, "warnings": warnings}

    def interpret(s: ItemState) -> dict:
        previous = ({"spec": s.get("raw_spec"), "failure": s["failure"]}
                    if s.get("failure_kind") == "skill" else None)
        payload = {**llm_context(s["context"]), "catalog": s["catalog"], "previous_attempt": previous}
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
        return {"spec": spec, "missing_skills": spec["unmatched_skills"], "warnings": s["warnings"] + warnings}

    def give_up(s: ItemState) -> dict:
        status = "error" if s.get("failure_kind") == "llm" else "skill_unclear"
        return {"status": status, "warnings": s["warnings"] + [f"{s['failure']} (해석 {s['attempts']}회 시도)"]}

    def borrow(s: ItemState) -> dict:
        """스킬 목록에서 맞는 스킬을 못 골랐을 때: 이름이 가장 비슷한 액션아이템의 요구 스킬을 빌린다."""
        missing = [m for m in (s.get("raw_spec") or {}).get("unmatched_skills") or [] if isinstance(m, str)]
        try:
            found = most_similar(s["context"].get("title") or "", tools["similar"](s["item_id"]))
        except Exception as exc:
            return {"missing_skills": missing,
                    "warnings": s["warnings"] + [f"유사 업무 조회 실패 ({type(exc).__name__})"]}
        if found is None:
            return {"missing_skills": missing}
        best, ratio = found
        try:
            spec, warnings = normalize_spec({**(s.get("raw_spec") or {}), "required_skills": best["required_skills"]},
                                            s["catalog"])
        except ValueError:
            return {"missing_skills": missing}
        return {"spec": spec, "missing_skills": missing, "skill_source": "similar_item", "borrowed_from": best["id"],
                "warnings": s["warnings"] + warnings
                            + [f"유사 업무 '{best['description']}'의 요구 스킬을 빌림 (이름 유사도 {ratio:.2f})"]}

    def score(s: ItemState) -> dict:
        ranked, _ = rank_candidates(s["spec"], s["candidates"])
        position = {c["user_id"]: c.get("position") for c in s["candidates"]}
        tier1 = first_tier({a["user_id"] for a in s["context"].get("attendees", [])}, s["candidates"])
        ranked = [{**r, "position": position[r["user_id"]], "tier": 1 if r["user_id"] in tier1 else 2}
                  for r in ranked]
        by_position, warnings = select_by_position(ranked)
        warnings = s["warnings"] + data_warnings(s["spec"], s["candidates"], s["workload_present"]) + warnings
        picked = any(by_position.values())
        return {"ranked": ranked, "by_position": by_position, "warnings": warnings,
                "status": "pending_manager_review" if picked else "no_candidate"}

    def save(s: ItemState) -> dict:
        result = build_result(s)
        if s["status"] == "error":  # 판단을 못 한 것이라 아무것도 쓰지 않는다 → 다음 실행에 다시 처리
            print(f"  [error] {s['item_id']}  저장 안 함, 다음 실행에 다시 시도: {'; '.join(result['warnings'])}")
            return {"output": result}
        try:
            tools["save"](result)
        except Exception as exc:
            result["status"] = "error"
            print(f"  [error] {s['item_id']}  Neo4j 저장 실패 ({type(exc).__name__})", file=sys.stderr)
            return {"output": result}
        print(f"  [{s['status']}] {s['item_id']}  추천 {len(result['recommendations'])}명")
        for k in result["required_skills"]:
            print(f"      요구 스킬 {k['skill_id']} ({IMPORTANCE_KO[k['importance']]}): {k['reason']}")
        for w in result["warnings"]:
            print(f"      ⚠️ {w}")
        return {"output": result}

    def after_validate(s: ItemState) -> str:
        if s.get("spec"):
            return "score"
        if s["attempts"] < MAX_INTERPRET:
            return "interpret"
        return "borrow" if s.get("failure_kind") == "skill" else "give_up"

    item = StateGraph(ItemState)
    for name, node in [("fetch_context", fetch_context), ("interpret", interpret), ("validate", validate),
                       ("borrow", borrow), ("give_up", give_up), ("score", score), ("save", save)]:
        item.add_node(name, node)
    item.add_edge(START, "fetch_context")
    item.add_conditional_edges("fetch_context", lambda s: "save" if s.get("status") == "error" else "interpret",
                               ["save", "interpret"])
    item.add_edge("interpret", "validate")
    item.add_conditional_edges("validate", after_validate, ["score", "interpret", "borrow", "give_up"])
    item.add_conditional_edges("borrow", lambda s: "score" if s.get("spec") else "give_up", ["score", "give_up"])
    item.add_edge("give_up", "save")
    item.add_edge("score", "save")
    item.add_edge("save", END)
    item_graph = item.compile()

    def load_items(s: RunState) -> dict:
        return {"catalog": tools["catalog"](), "item_ids": tools["items"](s.get("force", False))}

    def fan_out(s: RunState):
        return [Send("process_item", {"item_id": i, "catalog": s["catalog"]}) for i in s["item_ids"]] or END

    def process_item(payload: dict) -> dict:
        try:
            return {"results": [item_graph.invoke(payload)["output"]]}
        except Exception as exc:  # 한 건이 실패해도 다른 건에 영향 없게. 저장 안 했으니 다음 실행에 다시
            print(f"[{payload['item_id']}] 처리 실패 ({type(exc).__name__})", file=sys.stderr)
            return {"results": [{"action_item_id": payload["item_id"], "status": "error"}]}

    run = StateGraph(RunState)
    run.add_node("load_items", load_items)
    run.add_node("process_item", process_item)
    run.add_edge(START, "load_items")
    run.add_conditional_edges("load_items", fan_out, ["process_item", END])
    run.add_edge("process_item", END)
    return run.compile()


def run_once(graph, force: bool, metadata: dict | None = None) -> list[dict]:
    config = {"run_name": "recommend_agent",
              "metadata": {"recommend_run_id": uuid.uuid4().hex[:12], **(metadata or {})}}
    return graph.invoke({"force": force}, config).get("results") or []


# ─────────────────────────────── Neo4j ───────────────────────────────

def neo4j_tools(driver) -> dict:
    # Send로 노드가 병렬 실행되므로 세션을 공유하지 않고 driver.execute_query / 호출마다 새 세션을 쓴다.
    def q(query, **params):
        return driver.execute_query(query, params, routing_="r").records

    def save_tx(tx, result):
        item_id = result["action_item_id"]
        tx.run(CLEAR_PENDING, id=item_id).consume()
        status = None if result["status"] == "pending_manager_review" else result["status"]  # 성공은 엣지로 알 수 있다
        tx.run(SAVE_RECOMMENDATIONS, id=item_id, status=status, missing=result["missing_skills"],
               rows=result["recommendations"]).consume()
        tx.run(SAVE_REQUIRED_SKILLS, id=item_id, source=result["skill_source"],
               borrowed_from=result["borrowed_from"], skills=result["required_skills"]).consume()

    def save(result):
        with driver.session() as session:
            session.execute_write(save_tx, result)

    def context(item_id):
        records = q(CONTEXT_QUERY, id=item_id)
        return records[0].data() if records else None

    return {
        "catalog": lambda: {name: [r.data() for r in q(query)] for name, query in CATALOG_QUERIES.items()},
        "items": lambda force: [r["id"] for r in q(ITEMS_QUERY, force=force)],
        "context": context,
        "similar": lambda item_id: [r.data() for r in q(SIMILAR_SKILLS_QUERY, id=item_id)],
        "candidates": lambda: ([r.data() for r in q(CANDIDATE_QUERY)],
                               q(WORKLOAD_DATA_QUERY)[0]["present"]),
        "save": save,
    }


# ─────────────────────────────── self-check ───────────────────────────────

class FakeLLM:
    """interpret 답은 action_item_id별 큐에서 꺼낸다. Exception이면 호출 실패로 취급."""

    def __init__(self, interpret_answers: dict):
        self.answers = interpret_answers
        self.payloads = []

    def bind(self, **_):
        return self

    def invoke(self, messages):
        payload = json.loads(messages[1][1])
        self.payloads.append(payload)
        answer = self.answers[payload["action_item"]["action_item_id"]].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=json.dumps(answer))


def self_check() -> None:
    def person(uid, position, level, active=True, team="t1"):
        return {"user_id": uid, "name": uid, "is_active": active, "position": position,
                "teams": [{"id": team, "name": team}], "roles": [], "open_tasks": 0,
                "skills": [{"id": "A", "name": "A", "level": level}]}
    people = [person("s1", "senior", 4), person("s2", "senior", 2), person("s3", "senior", 3),
              person("m1", "mid", 3), person("j1", "junior", 1), person("x1", None, 4), person("off", "mid", 4, False),
              person("o1", "senior", 4, team="t2"), person("o2", "junior", 4, team="t2"),  # 참석자(s1) 팀 밖
              person("z1", "mid", 0)]
    people[3]["open_tasks"] = 1
    good = {"required_skills": [{"skill_id": "A", "importance": "core", "reason": "핵심"}],
            "unmatched_skills": ["엑셀"]}
    bad = {"required_skills": [{"skill_id": "NOPE", "importance": "core"}], "unmatched_skills": ["3D 프린팅"]}
    llm = FakeLLM({"ok": [bad, good], "unclear": [bad, bad], "borrow": [bad, bad],
                   "llmfail": [RuntimeError(), RuntimeError()],
                   "nocand": [{"required_skills": [{"skill_id": "B", "importance": "core"}]}]})
    saved = {}
    tools = {
        "catalog": lambda: {"skills": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}], "roles": [], "teams": [],
                            "users": [{"id": p["user_id"]} for p in people]},
        "items": lambda force: ["ok", "unclear", "borrow", "llmfail", "nocand", "gone"],
        "context": lambda i: None if i == "gone" else {"action_item_id": i, "meeting_id": "m-1",
                                                      "title": "할 일", "discussion_title": "소주제",
                                                      "discussion": ["논의"], "agenda_title": "안건",
                                                      "background": ["배경"], "decisions": ["결정"],
                                                      "attendees": [{"user_id": "s1", "name": "s1"}]},
        "candidates": lambda: (people, True),
        "similar": lambda i: [] if i == "unclear" else [
            {"id": "far", "description": "예산 정리", "required_skills": [{"skill_id": "B", "importance": "core"}]},
            {"id": "near", "description": "할 일 정리", "required_skills": [{"skill_id": "A", "importance": "core",
                                                                          "reason": "빌림"}]}],
        "save": lambda out: saved.update({out["action_item_id"]: out}),
    }
    results = run_once(build_graph(tools, llm), False)
    status = {r["action_item_id"]: r["status"] for r in results}
    assert status == {"ok": "pending_manager_review", "unclear": "skill_unclear", "borrow": "pending_manager_review",
                      "llmfail": "error", "nocand": "no_candidate", "gone": "error"}, status
    # 실패한 건은 아무것도 저장하지 않는다 → 다음 실행에 다시 처리
    assert set(saved) == {"ok", "unclear", "borrow", "nocand"}, saved.keys()

    ok = saved["ok"]
    # o1은 s3보다 점수가 높지만 팀 밖이라 senior 자리를 못 얻는다. o2는 j1보다 점수가 높지만 모자란 자리만 채우므로 2등
    rows = [(r["position"], r["user_id"], r["rank"]) for r in ok["recommendations"]]
    assert rows == [("senior", "s1", 1), ("senior", "s3", 2), ("mid", "m1", 1), ("junior", "j1", 1),
                    ("junior", "o2", 2)], rows
    by_user = {r["user_id"]: r for r in ok["recommendations"]}
    assert by_user["o2"]["score"] > by_user["j1"]["score"], "rank는 점수 순이 아니라 tier 우선"
    assert set(by_user["s1"]) == {"user_id", "position", "rank", "score", "skill_score", "role_team_score",
                                  "workload_score"}
    assert ok["required_skills"] == [{"skill_id": "A", "importance": "core", "reason": "핵심"}]
    assert ok["skill_source"] == "llm" and ok["borrowed_from"] is None and ok["missing_skills"] == ["엑셀"]
    warns = " ".join(ok["warnings"])
    assert "mid 직급 후보가 1명" in warns and "junior 직급은 회의 참석자·같은 팀 후보가 부족해" in warns
    assert "x1(x1)" in warns and "off" not in by_user and "z1" not in by_user

    borrowed = saved["borrow"]
    assert borrowed["skill_source"] == "similar_item" and borrowed["borrowed_from"] == "near"
    assert borrowed["missing_skills"] == ["3D 프린팅"] and borrowed["required_skills"][0]["reason"] == "빌림"
    unclear = saved["unclear"]
    assert unclear["required_skills"] == [] and unclear["recommendations"] == [] and unclear["borrowed_from"] is None
    assert unclear["missing_skills"] == ["3D 프린팅"]
    assert saved["nocand"]["recommendations"] == [] and saved["nocand"]["missing_skills"] is None
    assert most_similar("x", []) is None

    sent = llm.payloads[0]
    assert set(sent) == {"action_item", "discussion", "agenda", "catalog", "previous_attempt"}, sent.keys()
    assert sent["agenda"] == {"title": "안건", "background": ["배경"]} and "decisions" not in json.dumps(sent)
    assert run_once(build_graph(dict(tools, items=lambda force: []), llm), False) == []
    print("self-check 통과: 재시도 후 성공, 유사 업무 스킬 차용, skill_unclear / no_candidate, 실패 건은 저장 안 함, "
          "직급별 Top-2와 rank(tier 우선), 빈 슬롯 유지, 비활성·스킬 없음 제외.")


# ─────────────────────────────── main ───────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="담당자 추천 에이전트 (LangGraph)")
    parser.add_argument("--force", action="store_true", help="이미 RECOMMENDED_FOR가 있는 건도 다시 추천")
    parser.add_argument("--reset", action="store_true",
                        help="실행 전에 추천 결과(RECOMMENDED_FOR 전부 - 관리자가 처리한 것 포함, LLM이 만든 REQUIRES_SKILL, "
                             "ActionItem의 추천 속성)를 지우고 처음부터 추천한다. ASSIGNED_TO는 남는다. 되돌릴 수 없다.")
    parser.add_argument("--model", default=LLM_MODEL)
    parser.add_argument("--effort", default=LLM_EFFORT, choices=["none", "low", "medium", "high", "xhigh"])
    parser.add_argument("--label", help="리포트 이름 (기본: 모델 끝 이름_effort, 예: luna_high)")
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
    from langchain_core.tracers.langchain import wait_for_all_tracers
    import cost
    import recommend_report
    llm = ChatOpenAI(model=args.model, reasoning_effort=args.effort, timeout=120, max_retries=LLM_RETRIES)
    print(f"LLM: {args.model} (reasoning_effort={args.effort})")
    label = args.label or f"{args.model.split('-')[-1]}_{args.effort}"  # gpt-6-luna + high → luna_high
    status, results = "failed", []
    with GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                              auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
                              notifications_disabled_classifications=["UNRECOGNIZED"]) as driver:
        driver.verify_connectivity()
        if args.reset:
            for query in RESET_QUERIES:
                driver.execute_query(query)
            args.force = True
            print("[초기화] 추천 결과 삭제")
        try:
            results = run_once(build_graph(neo4j_tools(driver), llm), args.force,
                               {"model": args.model, "effort": args.effort})
            status = "ok"
        finally:
            # 중간에 실패해도 그때까지 나간 호출 비용은 기록한다 (load.py와 같은 규칙)
            if LLM_CALLS:
                cost.record_run(Path(__file__).name, args.model, args.effort, LLM_CALLS, status)
            wait_for_all_tracers()  # LangSmith로 보낼 trace가 남은 채로 끝나지 않게
        print(f"리포트: {recommend_report.write(driver, label)}")
    if not results:
        print("담당자 미결정 액션아이템이 없습니다.")
        return 0
    failed = sum(r["status"] == "error" for r in results)
    print(f"\n{len(results)}건 처리" + (f" (실패 {failed}건은 저장 안 함, 다음 실행에 다시 시도)" if failed else ""))
    print("※ " + DISCLAIMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
