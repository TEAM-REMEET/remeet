"""Neo4j에 적재된 Topic 분류 결과를 마크다운 파일로 뽑는다. Neo4j Browser를 열지 않고 결과를 볼 때 쓴다.

실행 (ontology 폴더에서):
    uv run python scripts/report.py embedding   →  reports/topics_embedding.md
    uv run python scripts/report.py llm         →  reports/topics_llm.md

load.py(임베딩)와 load_llm.py(LLM) 중 어느 쪽으로 적재했든 지금 DB 상태를 그대로 보여 준다.
적재 방식을 바꾸기 전후로 이름만 다르게 뽑아 두면 두 파일을 나란히 놓고 비교할 수 있다.
"""

import sys
from collections import defaultdict
from datetime import datetime

from load import ROOT, get_driver

label = sys.argv[1] if len(sys.argv) > 1 else "current"

rows = get_driver().execute_query("""
    MATCH (t:Topic)<-[r:ABOUT]-(ai:AgendaItem)<-[:HAS_AGENDA]-(mt:Meeting)
    RETURN t.topic_id AS topic_id, t.title AS topic, mt.meeting_id AS meeting,
           ai.title AS agenda, r.linked_by AS linked_by, r.confidence AS score,
           properties(r)['reason'] AS reason  // 임베딩 적재본엔 reason이 없어서, r.reason으로 쓰면 경고가 뜬다
    ORDER BY mt.meeting_order, ai.order
""").records
get_driver().close()

meetings = list(dict.fromkeys(r["meeting"] for r in rows))  # 회의 순서 유지
topics: dict[str, list] = defaultdict(list)                 # Topic 처음 등장 순서 유지
for r in rows:
    topics[r["topic_id"]].append(r)


def cell(text: str) -> str:
    return text.replace("|", "\\|")  # 표 칸이 깨지지 않게


def how(r) -> str:
    """이 안건이 Topic에 어떻게 붙었는지 한 줄로."""
    if r["linked_by"] == "new":
        tag = "🆕 새 Topic"
    elif r["linked_by"] == "llm":
        tag = "🤖 LLM 연결"
    elif r["linked_by"] == "next_topics":
        tag = "➡️ next_topics"
    else:
        tag = f"📐 {r['linked_by']}"
    if r["score"] is not None and r["linked_by"] != "new":
        tag += f" ({r['score']:.3f})"
    return f"{tag} — {r['reason']}" if r["reason"] else tag


lines = [
    f"# Topic 분류 결과: {label}",
    "",
    f"생성: {datetime.now():%Y-%m-%d %H:%M} · 안건 {len(rows)}개 → Topic {len(topics)}개",
    "",
    "## 1. 한눈에 보기 (행 = Topic, 열 = 회의)",
    "",
    "안건이 여러 회의에 걸쳐 한 행에 모이면 이어지는 Topic이고, 한 칸에 안건이 많이 몰리면 너무 넓게 묶인 것이다.",
    "",
    "| Topic | 안건 수 | " + " | ".join(meetings) + " |",
    "|---|---:|" + "---|" * len(meetings),
]
for agendas in sorted(topics.values(), key=len, reverse=True):
    by_meeting = defaultdict(list)
    for r in agendas:
        by_meeting[r["meeting"]].append(cell(r["agenda"]))
    lines.append(
        f"| **{cell(agendas[0]['topic'])}** | {len(agendas)} | "
        + " | ".join("<br>".join(by_meeting[m]) for m in meetings) + " |"
    )

lines += ["", "## 2. Topic별 상세 (연결 이유)", ""]
for agendas in topics.values():
    lines.append(f"### {agendas[0]['topic']}  ·  안건 {len(agendas)}개")
    for r in agendas:
        lines.append(f"- `{r['meeting']}` **{r['agenda']}**  \n  {how(r)}")
    lines.append("")

out = ROOT / "reports" / f"topics_{label}.md"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(lines), encoding="utf-8")
print(f"저장: {out}")
