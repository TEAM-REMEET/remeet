"""담당자 추천 결과(Neo4j)를 마크다운 리포트로 뽑는다. recommend_agent.py가 실행 끝에 부른다.

다시 뽑기만 할 때 (ontology 폴더에서):
    uv run python scripts/recommend_report.py luna_high   →  data/reports/recommend_report/luna_high/<YYYYMMDD-HHMM>.md

Neo4j의 ASSIGNED_TO / RECOMMENDED_FOR / REQUIRES_SKILL / BORROWED_SKILLS_FROM / FOLLOWS_UP과
ActionItem의 recommend_status, missing_skills를 읽는다.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

import cost

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "recommend_agent.py"

ITEMS_QUERY = """
    MATCH (mt:Meeting)-[:HAS_AGENDA]->(ag:Agenda)-[:HAS_DISCUSSION]->(:Discussion)-[:PRODUCED]->(ai:ActionItem)
    RETURN ai.action_item_id AS id, ai.description AS description, mt.meeting_id AS meeting,
           ai.owner_raw AS owner_raw, properties(ai) AS props,
           [(ai)-[r:REQUIRES_SKILL]->(s:Skill) | {skill_id: s.skill_id, name: s.name, importance: r.importance,
                                                  reason: r.reason, source: r.source}] AS skills,
           [(p:Person)-[r:RECOMMENDED_FOR]->(ai) | {name: p.name, user_id: p.user_id, active: p.is_active,
                                                    position: p.position, r: properties(r)}] AS recs,
           [(p:Person)-[a:ASSIGNED_TO]->(ai) | {name: p.name, user_id: p.user_id, source: a.assign_source}] AS assigned,
           [(ai)-[:FOLLOWS_UP]->(:ActionItem)<-[:ASSIGNED_TO]-(p:Person) | {name: p.name, user_id: p.user_id,
                                                                          active: p.is_active}] AS previous,
           [(ai)-[:BORROWED_SKILLS_FROM]->(src:ActionItem) | src.action_item_id][0] AS borrowed_from
    ORDER BY mt.held_at, mt.meeting_id, ag.order, id"""

# ✅ 회의록 담당자 = 회의록에 담당자가 적혀 있어 load.py가 바로 ASSIGNED_TO로 이은 것 (추천은 돌지 않음)
STATUS = {"no_candidate": "🟡 후보 없음", "skill_unclear": "🟠 스킬 불명"}
IMP = {"core": "핵심", "major": "주요", "minor": "보조"}
SLOT_ORDER = {"senior": 0, "mid": 1, "junior": 2}


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")  # 표 칸이 깨지지 않게


def who(p: dict) -> str:
    return f"{p['name']}({p['user_id']})" + ("" if p.get("active", True) else " ⛔비활성")


def item_status(it) -> str:
    if it["assigned"]:
        sources = {a["source"] for a in it["assigned"]}
        return {frozenset({"meeting_explicit"}): "✅ 회의록 담당자",
                frozenset({"history"}): "✅ 선행 업무 담당자"}.get(frozenset(sources), "✅ 배정됨")
    if it["recs"]:
        return "🟢 추천됨"
    return STATUS.get(it["props"].get("recommend_status"), "⚪ 미실행")


def skill_text(it) -> str:
    text = ", ".join(f"{s['name']}({IMP.get(s['importance'], s['importance'])})" for s in it["skills"]) or "-"
    return text + (" ↩️유사 업무" if it["borrowed_from"] else "")


def sorted_recs(it) -> list:
    """직급 순 → 직급 안 rank 순."""
    return sorted(it["recs"], key=lambda r: (SLOT_ORDER.get(r["position"], 9), r["r"].get("rank") or 9))


def top_text(it) -> str:
    return "<br>".join(f"{r['position']} {who(r)} {r['r'].get('score')}" for r in sorted_recs(it)) or "-"


def notices(it) -> list[str]:
    """확인이 필요한 것: 스킬 목록에 없는 기술, 유사 업무에서 빌린 스킬."""
    out = []
    if it["props"].get("missing_skills"):
        out.append("스킬 목록에 없는 필요 기술: " + ", ".join(it["props"]["missing_skills"]))
    if it["borrowed_from"]:
        out.append(f"유사 업무 `{it['borrowed_from']}`의 요구 스킬을 빌려 추천")
    return out


def write(driver, label: str) -> Path:
    items = driver.execute_query(ITEMS_QUERY).records
    runs = [r for r in cost.load_rows() if r["script"] == SCRIPT]
    cost_header = ("OpenAI 비용 (가장 최근 추천 실행): " + cost.run_line(runs[-1])) if runs else "OpenAI 비용: 기록 없음"
    counts = defaultdict(int)
    for it in items:
        counts[item_status(it)] += 1
    issues = [it for it in items if notices(it)]

    lines = [
        f"# 담당자 추천 결과: {label}",
        "",
        f"생성: {datetime.now():%Y-%m-%d %H:%M} · 액션아이템 {len(items)}개 · "
        + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        cost_header,
        "",
        "## 1. 한눈에 보기",
        "",
        "상태: ✅ 회의록 담당자 = 회의록에 담당자가 명시돼 load.py가 바로 배정한 것 · ✅ 선행 업무 담당자 = 선행 업무"
        "(FOLLOWS_UP)의 담당자에게 load.py가 바로 배정한 것 (둘 다 추천 대상 아님, 표의 Top2 칸에 배정된 사람) · "
        "🟢 추천됨 = 추천을 돌려 관리자 검토 대기.",
        "",
        "| 회의 | 액션 | 상태 | 요구 스킬 | 이전 담당자 | 직급별 Top2 (직급 이름 총점) | 안내 |",
        "|---|---|---|---|---|---|---:|",
    ]
    for it in items:
        if it["assigned"]:
            prev, main = "-", "<br>".join(who(a) for a in it["assigned"])
        else:
            prev = "<br>".join(who(p) for p in it["previous"]) or "-"
            main = top_text(it)
        lines.append(f"| {it['meeting']} | {cell(it['description'] or it['id'])} | {item_status(it)} | "
                     f"{cell(skill_text(it))} | {prev} | {main} | {len(notices(it))} |")

    lines += ["", "## 2. 확인이 필요한 안내 (스킬 목록 부족 · 유사 업무 차용)", ""]
    for it in issues:
        lines.append(f"- `{it['meeting']}` **{it['description']}**")
        lines += [f"  - {cell(n)}" for n in notices(it)]
    if not issues:
        lines.append("없음")

    lines += ["", "## 3. 액션아이템별 상세", ""]
    for it in items:
        lines += [f"### `{it['meeting']}` {it['description']}  ·  {item_status(it)}", ""]
        if it["owner_raw"]:
            lines.append(f"- 회의록 담당자 원문: {it['owner_raw']}")
        if it["assigned"]:
            lines += ["- 배정: " + ", ".join(f"{who(a)} ({a['source']})" for a in it["assigned"]), ""]
            continue
        for sk in it["skills"]:
            lines.append(f"- 요구 스킬 {sk['skill_id']} {sk['name']} ({IMP.get(sk['importance'], sk['importance'])}, "
                         f"{sk['source']}): {sk['reason'] or ''}")
        lines += [f"- ⚠️ {cell(n)}" for n in notices(it)]
        if it["previous"]:
            lines.append("- 선행 업무 담당자: " + ", ".join(who(p) for p in it["previous"]))
        if it["recs"]:
            lines += ["", "| 직급 | 순위 | 이름 | 총점 | 스킬 | 부서·역할 | 여유 | 상태 |",
                      "|---|---:|---|---:|---:|---:|---:|---|"]
            for r in sorted_recs(it):
                x = defaultdict(lambda: "-", r["r"])  # Neo4j는 null 속성을 저장하지 않아서 없는 키 = 값 없음
                lines.append(f"| {r['position']} | {x['rank']} | {who(r)} | {x['score']} | {x['skill_score']} | "
                             f"{x['role_team_score']} | {x['workload_score']} | {x['status']} |")
        lines.append("")

    # 실행마다 새 파일: 덮어쓰지 않고 설정(label)별로 기록을 쌓는다
    out = ROOT / "data" / "reports" / "recommend_report" / label / f"{datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv(ROOT / ".env")
    with GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                              auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
                              notifications_disabled_classifications=["UNRECOGNIZED"]) as d:
        print(f"저장: {write(d, sys.argv[1] if len(sys.argv) > 1 else 'current')}")
