"""가독성 블라인드 A/B: 에이전트 회의록 TXT vs 베이스라인 B(스키마 없음) 마크다운.

  python readability_ab.py gen   [회의]   → out/{회의}_agent.txt · out/{회의}_baseline.txt (실제 OpenAI 호출)
  python readability_ab.py judge [회의]   → LLM 쌍 비교 2번(순서 바꿈) + 사람 투표용 md
에이전트는 사용자가 내려받는 TXT(apps/web/state/minutes_text.render_minutes) 그대로,
베이스라인은 그 TXT에 없는 근거 발화 번호만 지우고 그대로 쓴다.
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\jwnsq\Desktop\HWTA Final Project\remeet")
TESTS = ROOT / "backend/src/followup/analysis/summary/tests"
sys.path[:0] = [str(ROOT / "backend/src"), str(ROOT / "apps/web"), str(TESTS)]
OUT = Path(__file__).with_name("readability_out")

CRITERIA = """너는 회의록의 가독성을 평가하는 심사자다. 같은 회의를 정리한 회의록 A와 B를 비교한다.
내용의 정확성은 따지지 않고, 회의에 참석하지 않은 사람이 읽기에 어느 쪽이 더 좋은지만 본다.
기준:
1. 한눈에 파악: 회의의 핵심(무엇을 논의하고 무엇이 정해졌는지)을 빨리 알 수 있는가
2. 결정 · 할 일 찾기: 결정 사항과 할 일(담당 · 기한)을 쉽게 찾을 수 있는가
3. 문장: 간결하고 명확하며 중복 · 군더더기가 없는가
4. 구조: 안건 · 항목이 논리적으로 정리되어 있는가
회의록이 쓰인 형식(태그 · 기호 · 들여쓰기)은 그 자체로 감점하거나 가점하지 말고, 읽는 데 도움이 되는지만 본다.
반드시 아래 JSON 하나만 출력한다.
{"scores": {"A": {"1": 1-5, "2": 1-5, "3": 1-5, "4": 1-5}, "B": {"1": 1-5, "2": 1-5, "3": 1-5, "4": 1-5}},
 "winner": "A|B|tie", "reason": "한국어 3문장 이내"}"""


def gen(mid: str) -> None:
    from conftest import transcript_text
    import baseline
    from followup.adapters.llm.structured_client import OpenAIStructuredClient
    from followup.application.summarize_meeting import summarize_meeting
    from state.minutes_text import render_minutes

    OUT.mkdir(exist_ok=True)
    llm, text = OpenAIStructuredClient(), transcript_text(mid)
    b = baseline.run(mid, text, llm)
    (OUT / f"{mid}_baseline.txt").write_text(strip_evidence(b.summary.raw), encoding="utf-8")
    print(f"baseline done {b.latency_sec:.0f}s", flush=True)
    s = summarize_meeting(mid, text, llm=llm, store=None, save=False, use_cache=False)
    (OUT / f"{mid}_agent.txt").write_text(render_minutes(s.model_dump(mode="json")), encoding="utf-8")
    (OUT / f"{mid}_agent.json").write_text(s.model_dump_json(indent=1), encoding="utf-8")
    print(f"agent done status={s.verification.status}", flush=True)


PROMPT_C = "회의 원문을 읽고 회의록을 작성한다. 원문에 없는 내용은 쓰지 않는다."   # 형식 · 근거 번호 지시 없음


def gen_c(mid: str) -> None:
    """베이스라인 C: 같은 입력 · 같은 모델 · 추론 강도, 지시는 위 두 문장뿐. 결과는 손대지 않고 저장."""
    from conftest import transcript_text
    from followup import settings as cfg
    from followup.adapters.llm.structured_client import OpenAIStructuredClient
    from followup.analysis.summary import steps
    from followup.analysis.summary.preprocessing import load_transcript
    from followup.analysis.summary.prompts import extract_summary_action_item as generate_prompt

    loaded = load_transcript(mid, transcript_text(mid))
    user = generate_prompt.build_user_prompt(steps._utterances(loaded), loaded["llm_metadata"])
    r = OpenAIStructuredClient()._get_client().responses.create(
        model=cfg.model_name(), instructions=PROMPT_C, input=user, reasoning={"effort": cfg.REASONING_EFFORT})
    path = Path(__file__).with_name(f"{mid}_baseline_c.txt")
    path.write_text(r.output_text, encoding="utf-8")
    print(f"saved {path.name} ({len(r.output_text)}자)")


def strip_evidence(md: str) -> str:
    return re.sub(r"\s*\[[\d,\s~\-]+\]\s*$", "", md, flags=re.M)


def ask(client, model, first: str, second: str) -> dict:
    r = client.responses.create(model=model, instructions=CRITERIA, reasoning={"effort": "high"},
                                input=f"# 회의록 A\n{first}\n\n# 회의록 B\n{second}")
    return json.loads(re.search(r"\{.*\}", r.output_text, re.S).group(0))


def judge(mid: str) -> None:
    from followup import settings
    from openai import OpenAI

    settings.load_env()
    agent, base = (OUT / f"{mid}_agent.txt").read_text(encoding="utf-8"), (OUT / f"{mid}_baseline.txt").read_text(encoding="utf-8")
    client, model = OpenAI(), settings.model_name()
    rounds = [("agent", "baseline", ask(client, model, agent, base)), ("baseline", "agent", ask(client, model, base, agent))]
    picks = []
    for a, b, r in rounds:
        who = {"A": a, "B": b}.get(r["winner"], "tie")
        picks.append(who)
        print(f"\n[A={a}, B={b}] winner={who}\n  scores A={r['scores']['A']} B={r['scores']['B']}\n  {r['reason']}")
    verdict = picks[0] if picks[0] == picks[1] else "판정 불일치(순서 편향) → 무승부"
    total = {w: 0 for w in ("agent", "baseline")}
    for a, b, r in rounds:
        total[a] += sum(int(v) for v in r["scores"]["A"].values())
        total[b] += sum(int(v) for v in r["scores"]["B"].values())
    print(f"\n최종: {verdict} / 총점(2회 합, 만점 40) agent={total['agent']} baseline={total['baseline']}")
    (OUT / f"{mid}_judge.json").write_text(json.dumps([r for *_, r in rounds], ensure_ascii=False, indent=1), encoding="utf-8")
    vote_md(mid, agent, base)


def vote_md(mid: str, agent: str, base: str) -> None:
    """사람 투표용: A/B를 무작위로 섞고 정답표는 따로 저장"""
    pair = [("agent", agent), ("baseline", base)]
    random.Random().shuffle(pair)
    body = "\n\n".join(f"## 회의록 {label}\n\n```text\n{txt.strip()}\n```" for label, (_, txt) in zip("AB", pair))
    (OUT / f"{mid}_vote.md").write_text(
        f"# 회의록 가독성 투표 ({mid})\n\n같은 회의를 정리한 두 회의록입니다. 내용 정확성은 보지 말고 "
        f"**회의에 없던 사람이 읽기에 어느 쪽이 더 좋은지** 골라 주세요.\n\n"
        f"- 한눈에 핵심 파악 / 결정 · 할 일 찾기 / 문장 간결성 / 구조\n\n선택: A / B\n\n{body}\n", encoding="utf-8")
    (OUT / f"{mid}_vote_key.txt").write_text(f"A={pair[0][0]}, B={pair[1][0]}\n", encoding="utf-8")


# ── 가독성 지수 (LLM 없이 코드로 계산) ──────────────────────────────
# 항목 = 글머리표 한 줄. 꾸밈 = 태그 [..] · 빈 담당/기한 칸 · 안건 번호처럼 내용이 아닌 글자.
_BULLET = re.compile(r"^\s*[-*·]\s+")   # 번호 줄(안건 목차 · 안건 제목)은 항목이 아님
_NOISE = re.compile(r"\[[^\]]*\]|\(담당:[^)]*\)|^(논의|결정|액션아이템|액션|미해결|다음 회의)\s*:\s*")
LONG = 80        # 이 글자 수를 넘는 문장은 긴 문장
WEIGHTS = {"sentence": 0.2, "long": 0.2, "clauses": 0.15, "duplicate": 0.2, "noise": 0.1, "key_first": 0.15}


def measure(txt: str) -> dict:
    lines = [l for l in txt.splitlines() if _BULLET.match(l)]
    raw = [_BULLET.sub("", l).strip() for l in lines]
    content = [_NOISE.sub("", r).strip() for r in raw]
    sentences = [s for c in content for s in re.split(r"(?<=[.;])\s+", c) if s.strip()]
    key = [i for i, r in enumerate(raw) if re.search(r"\[결정\]|\[액션\]|^(결정|액션아이템|액션)\s*:", r)]
    m = {
        "items": len(raw),
        "sentence": sum(map(len, sentences)) / len(sentences),                       # 문장 평균 글자 수
        "long": sum(len(s) > LONG for s in sentences) / len(sentences),               # 긴 문장 비율
        "clauses": sum(s.count(",") + s.count(";") for s in sentences) / len(sentences),  # 문장당 쉼표·세미콜론
        "duplicate": 1 - len(set(content)) / len(content),                            # 반복된 항목 비율
        "noise": 1 - sum(map(len, content)) / sum(map(len, raw)),                     # 꾸밈 글자 비율
        "key_first": sum(i < len(raw) / 3 for i in key) / len(key) if key else 0.0,   # 결정·액션이 앞 1/3에 있는 비율
    }
    sub = {   # 0~100 (높을수록 읽기 좋음) — 기준값은 이 실험에서 정한 것
        "sentence": clamp(100 * (120 - m["sentence"]) / 80),    # 40자 이하 100점, 120자 이상 0점
        "long": clamp(100 * (1 - m["long"])),
        "clauses": clamp(100 * (3 - m["clauses"]) / 3),          # 문장당 0개 100점, 3개 이상 0점
        "duplicate": clamp(100 * (1 - 2 * m["duplicate"])),      # 반복 50% 이상 0점
        "noise": clamp(100 * (1 - 2 * m["noise"])),              # 꾸밈 50% 이상 0점
        "key_first": 100 * m["key_first"],
    }
    return {"metrics": m, "sub": sub, "index": sum(WEIGHTS[k] * v for k, v in sub.items())}


def clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


def metrics(mid: str) -> None:
    for name in ("agent", "baseline"):
        r = measure((Path(__file__).with_name(f"{mid}_{name}.txt")).read_text(encoding="utf-8"))
        m, s = r["metrics"], r["sub"]
        print(f"\n[{name}] 가독성 지수 {r['index']:.1f} / 100 (항목 {m['items']}줄)")
        for k in WEIGHTS:
            print(f"  {k:10} 값={m[k]:.2f}  점수={s[k]:.0f}  가중치={WEIGHTS[k]}")


def _self_check() -> None:
    r = measure("- [결정] 짧은 결정함.\n- 긴 문장, 쉼표, 많음; 이어짐.\n- [결정] 짧은 결정함.\n")
    assert r["metrics"]["items"] == 3 and abs(r["metrics"]["duplicate"] - 1 / 3) < 1e-9
    assert r["metrics"]["key_first"] == 0.5 and r["metrics"]["noise"] > 0


_self_check()

if __name__ == "__main__":
    {"gen": gen, "gen_c": gen_c, "judge": judge, "metrics": metrics}[sys.argv[1]](sys.argv[2] if len(sys.argv) > 2 else "TS3003c")
