"""Neo4j에 적재된 결과를 마크다운 파일로 뽑는다. Neo4j Browser를 열지 않고 결과를 볼 때 쓴다.

실행 (ontology 폴더에서):
    uv run python scripts/report.py embedding   →  data/reports/topics_report/embedding/<YYYYMMDD-HHMM>.md
    uv run python scripts/report.py llm         →  data/reports/topics_report/llm/<YYYYMMDD-HHMM>.md
Topic 분류 아래에 후속 업무(FOLLOWS_UP) 연결 결과도 같이 싣는다 (둘 다 load.py가 만든다).
담당자 추천 리포트는 recommend_agent.py가 실행 끝에 따로 만든다 (scripts/recommend_report.py).

load.py(LLM, 현재 구조), load_llm.py(LLM, 예전 구조), load_embed.py(임베딩) 중 어느 쪽으로 적재했든 지금 DB 상태를 그대로 보여 준다.
적재 방식을 바꾸기 전후로 이름만 다르게 뽑아 두면 두 파일을 나란히 놓고 비교할 수 있다.

LLM으로 적재한 데이터면 맨 위에 그 실행의 OpenAI 비용을, 맨 아래에 지금까지 실행한 설정별 비용 비교를 붙인다.
(비용은 data/reports/cost_log.jsonl에서 읽는다. load.py / load_llm.py가 실행할 때마다 쌓는다)
"""

import sys
from collections import defaultdict
from datetime import datetime

import cost
from load import ROOT, get_driver

label = sys.argv[1] if len(sys.argv) > 1 else "current"

rows = get_driver().execute_query("""
    // 라벨을 안 붙여서 예전 구조(AgendaItem)와 새 구조(Agenda) 둘 다 읽는다
    MATCH (t:Topic)<-[r:ABOUT]-(ai)<-[:HAS_AGENDA]-(mt:Meeting)
    RETURN t.topic_id AS topic_id, t.title AS topic, mt.meeting_id AS meeting,
           ai.title AS agenda, properties(r)['linked_by'] AS linked_by, r.confidence AS score,  // load.py는 linked_by 없음
           properties(r)['reason'] AS reason  // 임베딩 적재본엔 reason이 없어서, r.reason으로 쓰면 경고가 뜬다
    ORDER BY mt.held_at, mt.meeting_id, ai.order  // 날짜가 같은 회의는 회의 ID 순
""").records

meetings = list(dict.fromkeys(r["meeting"] for r in rows))  # 회의 순서 유지
topics: dict[str, list] = defaultdict(list)                 # Topic 처음 등장 순서 유지
for r in rows:
    topics[r["topic_id"]].append(r)


def cell(text: str) -> str:
    return text.replace("|", "\\|")  # 표 칸이 깨지지 않게


def how(r) -> str:
    """이 안건이 Topic에 어떻게 붙었는지 한 줄로."""
    if r["linked_by"] == "new" or (r["linked_by"] in ("auto", None) and r["score"] == 1.0):
        tag = "🆕 새 Topic"  # load.py는 linked_by를 쓰지 않고, 새 Topic만 confidence 1.0
    elif r["linked_by"] in ("auto", None):
        tag = "🤖 LLM 연결"
    elif r["linked_by"] == "hitl":
        tag = "👤 사람 확인"
    elif r["linked_by"] == "llm":
        tag = "🤖 LLM 연결"
    elif r["linked_by"] == "next_topics":
        tag = "➡️ next_topics"
    else:
        tag = f"📐 {r['linked_by']}"
    if r["score"] is not None and r["linked_by"] not in ("new", "auto", None):
        tag += f" ({r['score']:.3f})"
    return f"{tag} — {r['reason']}" if r["reason"] else tag


# LLM 적재본은 ABOUT마다 reason이 있고, 임베딩 적재본(load_embed.py)에는 없다
cost_rows = cost.load_rows()
llm_loaded = any(r["reason"] for r in rows)
if not llm_loaded:
    cost_header = "OpenAI 비용: 없음 (LLM으로 적재한 데이터가 아니다)"
elif not cost_rows:
    cost_header = "OpenAI 비용: 기록 없음 (비용 기록이 생기기 전에 적재한 데이터다)"
else:
    # shortcut: DB에는 어느 실행으로 적재했는지 남지 않아서, 가장 최근 LLM 실행을 이 데이터의 비용으로 본다.
    #           그 뒤에 load_embed.py로 다시 적재했다면 위 llm_loaded 검사에서 걸러진다.
    cost_header = "OpenAI 비용 (가장 최근 LLM 실행): " + cost.run_line(cost_rows[-1])

lines = [
    f"# Topic 분류 결과: {label}",
    "",
    f"생성: {datetime.now():%Y-%m-%d %H:%M} · 안건 {len(rows)}개 → Topic {len(topics)}개",
    "",
    cost_header,
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

# ─────────────────────────────── FOLLOWS_UP 리포트 ───────────────────────────────

actions = get_driver().execute_query("""
    MATCH (mt:Meeting)-[:HAS_AGENDA]->(ag:Agenda)-[:HAS_DISCUSSION]->(:Discussion)-[:PRODUCED]->(ai:ActionItem)
    OPTIONAL MATCH (ag)-[:ABOUT]->(t:Topic)
    OPTIONAL MATCH (ai)-[f:FOLLOWS_UP]->(prev:ActionItem)
    RETURN ai.action_item_id AS id, ai.description AS description, mt.meeting_id AS meeting,
           ag.title AS agenda, t.title AS topic, prev.action_item_id AS prev, f.reason AS reason
    ORDER BY mt.held_at, mt.meeting_id, ag.order, id
""").records
by_id = {a["id"]: a for a in actions}
action_meetings = list(dict.fromkeys(a["meeting"] for a in actions))


def chain_root(action_id: str) -> str:
    seen = set()
    while by_id[action_id]["prev"] in by_id and action_id not in seen:  # 순환이 생겨도 멈추게
        seen.add(action_id)
        action_id = by_id[action_id]["prev"]
    return action_id


chains: dict[str, list] = defaultdict(list)
for a in actions:
    chains[chain_root(a["id"])].append(a)
linked = [a for a in actions if a["prev"]]
multi = [c for c in chains.values() if len(c) > 1]

lines += [
    "## 3. 후속 업무(FOLLOWS_UP) 한눈에 보기 (행 = 이어지는 업무 흐름, 열 = 회의)",
    "",
    f"액션아이템 {len(actions)}개 중 {len(linked)}개가 선행 업무와 연결 · 이어지는 흐름 {len(multi)}개. "
    "한 행이 선행 → 후속으로 이어진 액션들이고, 연결이 없는 액션은 4번 아래에 목록으로 둔다.",
    "",
    "| 흐름 (첫 액션) | 액션 수 | " + " | ".join(action_meetings) + " |",
    "|---|---:|" + "---|" * len(action_meetings),
]
for chain in sorted(multi, key=len, reverse=True):
    by_meeting = defaultdict(list)
    for a in chain:
        by_meeting[a["meeting"]].append(cell(a["description"] or a["id"]))
    lines.append(f"| **{cell(chain[0]['description'] or chain[0]['id'])}** | {len(chain)} | "
                 + " | ".join("<br>".join(by_meeting[m]) for m in action_meetings) + " |")

lines += ["", "## 4. 후속 업무 연결 상세 (후속 ← 선행, 연결 이유)", ""]
for a in linked:
    prev = by_id.get(a["prev"], {})
    topic_note = "" if prev.get("topic") == a["topic"] else f" · ⚠️ 다른 Topic ({prev.get('topic')} → {a['topic']})"
    lines.append(f"- `{a['meeting']}` **{a['description']}**  \n"
                 f"  ← `{prev.get('meeting')}` {prev.get('description')}{topic_note}  \n"
                 f"  🤖 {a['reason'] or '(이유 없음)'}")
if not linked:
    lines.append("연결된 액션이 없다. load.py로 다시 적재했는지 확인한다.")

lines += ["", "**선행 업무가 없는 액션**", ""]
lines += [f"- `{a['meeting']}` {a['description']}" for a in actions if not a["prev"]]
lines.append("")

if cost_rows:
    lines += [
        "## 5. 비용 비교 (지금까지 실행한 설정별 평균)",
        "",
        *cost.average_table(cost_rows),
        "",
        "실행 하나하나의 기록은 `uv run python scripts/cost.py` → `data/reports/cost.md`",
        "",
    ]

get_driver().close()

# 실행마다 새 파일: 덮어쓰지 않고 설정(label)별로 기록을 쌓는다
out = ROOT / "data" / "reports" / "topics_report" / label / f"{datetime.now():%Y%m%d-%H%M}.md"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(lines), encoding="utf-8")
print(f"저장: {out}")
