"""LLM-as-a-Judge (deepeval GEval): 액션아이템 맥락에 비해 LLM이 고른 요구 스킬과 이유가 타당한가.
Uses extra paid judge calls (pip install deepeval)."""
import json
import os

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from conftest import MEETINGS, make_tools, run
from followup.analysis.recommend import recommend_agent as agent

SAMPLE = 3  # 채점할 액션아이템 수 (채점 호출 비용)
THRESHOLD = 0.7

CRITERIA = """입력은 회의에서 나온 액션아이템과 그 논의 맥락, 그리고 고를 수 있는 스킬 목록이다.
출력은 에이전트가 그 목록에서 고른 요구 스킬(중요도 core/major/minor, 이유)과 목록에 없는 필요 기술이다.
1) 고른 스킬이 이 업무를 하는 데 실제로 필요한가
2) 중요도가 업무에서의 비중과 맞는가 (핵심 기술이 core)
3) 이유가 맥락에 근거하고 구체적인가
4) 목록에 있는데 빠뜨린 핵심 스킬은 없는가"""


def test_required_skill_quality(driver, llm):
    tools = make_tools(driver, MEETINGS[0], [])
    results = [r for r in run(tools, llm).results if r.get("required_skills")][:SAMPLE]
    assert results, "요구 스킬이 나온 액션아이템이 없음"
    skills = {s["id"]: s["name"] for s in tools["catalog"]()["skills"]}
    metric = GEval(name="요구 스킬 타당성", criteria=CRITERIA, threshold=THRESHOLD,
                   evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
                   model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    scores = []
    for r in results:
        given = {**agent.llm_context(tools["context"](r["action_item_id"])), "skill_catalog": list(skills.values())}
        output = "\n".join(f"- {skills.get(s['skill_id'], s['skill_id'])} ({s['importance']}): {s['reason']}"
                           for s in r["required_skills"])
        if r.get("missing_skills"):
            output += "\n목록에 없는 필요 기술: " + ", ".join(r["missing_skills"])
        metric.measure(LLMTestCase(input=json.dumps(given, ensure_ascii=False, default=str), actual_output=output))
        scores.append(metric.score)
        print(f"\n{r['action_item_id']}: {metric.score} ({metric.reason})")

    mean = sum(scores) / len(scores)
    print(f"\nSkill relevance mean: {mean:.2f}")
    assert mean >= THRESHOLD
