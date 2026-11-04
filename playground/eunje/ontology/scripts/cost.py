"""OpenAI 호출 비용을 실행 단위로 기록하고 모델별로 비교한다.

load.py / load_llm.py가 끝날 때 record_run()을 불러 data/reports/cost_log.jsonl에 한 줄씩 쌓는다.
호출 하나하나의 프롬프트·응답은 traced_call()이 LangSmith에 남긴다 (.env에 LANGSMITH_TRACING=true일 때).
실패해서 중간에 멈춘 실행도 그때까지 나간 호출은 기록한다 (이미 돈이 나갔으니까).

비교표 보기 (ontology 폴더에서):
    uv run python scripts/cost.py            →  data/reports/cost.md
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from langsmith import get_current_run_tree, traceable

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "reports" / "cost_log.jsonl"

# 1M 토큰당 달러. 가격이 바뀌면 여기만 고친다. 표에 없는 모델은 토큰만 기록하고 비용은 비워 둔다.
# shortcut: 272K 토큰이 넘는 긴 프롬프트의 할증 요금과 cache write 요금은 계산하지 않는다.
#           지금 프롬프트는 수천 토큰이라 해당이 없다. 긴 입력을 넣게 되면 추가한다.
PRICES_PER_1M = {
    # developers.openai.com/api/docs/models/gpt-6-luna (2026-11-04 확인)
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
    # 공식 페이지를 못 찾아 제3자 가격표 기준 (morphllm.com 등, 2026-11-04). openai.com에서 확인할 것
    "gpt-6.1-terra": {"input": 1.00, "cached_input": 0.05, "output": 5.00},
}


def price_for(model: str) -> dict | None:
    """'gpt-6-luna-2026-10-01' 같은 스냅샷 이름도 앞부분이 같은 가격을 쓴다."""
    for name in sorted(PRICES_PER_1M, key=len, reverse=True):
        if model == name or model.startswith(name + "-"):
            return PRICES_PER_1M[name]
    return None


def summarize(calls: list[dict], model: str) -> dict:
    """OpenAIClient.calls([{model, usage}, ...])를 토큰 합계와 비용으로 바꾼다."""
    total = {"calls": len(calls), "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    for call in calls:
        usage = call.get("usage") or {}
        total["input_tokens"] += usage.get("prompt_tokens", 0)
        total["cached_tokens"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        total["output_tokens"] += usage.get("completion_tokens", 0)  # 추론 토큰도 여기 포함되어 출력 요금으로 나간다
        total["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)

    price = price_for(model)
    if price is None:
        total["cost_usd"] = None
    else:
        uncached = total["input_tokens"] - total["cached_tokens"]
        total["cost_usd"] = round(
            (uncached * price["input"] + total["cached_tokens"] * price["cached_input"]
             + total["output_tokens"] * price["output"]) / 1_000_000,
            6,
        )
    return total


@traceable(
    run_type="llm",
    name="OpenAI",
    process_inputs=lambda i: {"messages": [{"role": "system", "content": i["system"]},
                                           {"role": "user", "content": i["user"]}]},
)
def traced_call(llm, system: str, user: str) -> str:
    """llm(system, user)를 그대로 부르고, LANGSMITH_TRACING=true면 프롬프트·응답·토큰·비용을 LangSmith에 남긴다."""
    content = llm(system, user)
    run = get_current_run_tree()
    if run is not None:
        call = llm.calls[-1]
        t = summarize([call], llm.model)
        usage = {
            "input_tokens": t["input_tokens"],
            "output_tokens": t["output_tokens"],
            "total_tokens": t["input_tokens"] + t["output_tokens"],
            "input_token_details": {"cache_read": t["cached_tokens"]},
            "output_token_details": {"reasoning": t["reasoning_tokens"]},
        }
        if t["cost_usd"] is not None:  # LangSmith 가격표엔 이 모델이 없어서 우리 PRICES_PER_1M으로 계산해 넣는다
            usage["total_cost"] = t["cost_usd"]
        run.set(usage_metadata=usage, metadata={
            "ls_provider": "openai", "ls_model_name": llm.model,
            "effort": llm.reasoning_effort, "response_model": call.get("model"),
        })
    return content


def record_run(script: str, model: str, effort: str, calls: list[dict], status: str) -> dict:
    """실행 한 번의 비용을 로그에 한 줄 추가하고 화면에 요약을 찍는다."""
    row = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "script": script,
        "model": model,
        "effort": effort,
        "status": status,
        "response_models": sorted({c.get("model") for c in calls if c.get("model")}),  # 실제 응답한 스냅샷 이름
        **summarize(calls, model),
    }
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    cost = "가격표에 없음" if row["cost_usd"] is None else f"${row['cost_usd']:.4f}"
    print(
        f"\n[비용] {model} ({effort}) · 호출 {row['calls']}번 · 입력 {row['input_tokens']:,} "
        f"(캐시 {row['cached_tokens']:,}) · 출력 {row['output_tokens']:,} (추론 {row['reasoning_tokens']:,}) · {cost}"
        f"\n       기록: {LOG}"
    )
    return row


def load_rows() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def usd(v) -> str:
    return "-" if v is None else f"${v:.4f}"


def run_line(r: dict) -> str:
    """실행 한 번을 한 줄로. report.py도 쓴다."""
    return (
        f"{r['model']} ({r['effort']}) · {r['at']} · {r['status']} · 호출 {r['calls']}번 · "
        f"입력 {r['input_tokens']:,} (캐시 {r['cached_tokens']:,}) · "
        f"출력 {r['output_tokens']:,} (추론 {r['reasoning_tokens']:,}) · **{usd(r['cost_usd'])}**"
    )


def average_table(rows: list[dict]) -> list[str]:
    """모델 × effort별 평균 비교표 (성공한 실행만). report.py도 쓴다."""
    lines = [
        "| 모델 | effort | 실행 수 | 평균 호출 | 평균 입력 | 평균 출력 | 평균 추론 | 실행당 평균 비용 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    groups = defaultdict(list)
    for r in rows:
        if r["status"] == "ok":
            groups[(r["model"], r["effort"])].append(r)
    for (model, effort), rs in sorted(groups.items()):
        n = len(rs)
        avg = lambda k: sum(r[k] for r in rs) / n  # noqa: E731
        costs = [r["cost_usd"] for r in rs]
        cost = None if None in costs else sum(costs) / n
        lines.append(
            f"| {model} | {effort} | {n} | {avg('calls'):.0f} | {avg('input_tokens'):,.0f} | "
            f"{avg('output_tokens'):,.0f} | {avg('reasoning_tokens'):,.0f} | {usd(cost)} |"
        )
    return lines


def write_report() -> Path:
    rows = load_rows()
    lines = [
        "# OpenAI 비용 비교",
        "",
        f"생성: {datetime.now():%Y-%m-%d %H:%M} · 실행 {len(rows)}번 · 기록: `data/reports/cost_log.jsonl`",
        "",
        "## 1. 설정별 평균 (성공한 실행만)",
        "",
        *average_table(rows),
        "",
        "## 2. 전체 실행 기록",
        "",
        "| 시각 | 스크립트 | 모델 | effort | 상태 | 호출 | 입력(캐시) | 출력(추론) | 비용 |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['at']} | {r['script']} | {r['model']} | {r['effort']} | {r['status']} | {r['calls']} | "
            f"{r['input_tokens']:,} ({r['cached_tokens']:,}) | {r['output_tokens']:,} ({r['reasoning_tokens']:,}) | "
            f"{usd(r['cost_usd'])} |"
        )
    lines += ["", "가격(1M 토큰당 USD): " + ", ".join(
        f"{m} 입력 ${p['input']} / 캐시 ${p['cached_input']} / 출력 ${p['output']}" for m, p in PRICES_PER_1M.items()
    )]

    out = ROOT / "data" / "reports" / "cost.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    if not LOG.exists():
        sys.exit(f"아직 기록이 없습니다: {LOG}")
    print(f"저장: {write_report()}")
