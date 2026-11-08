"""검토가 끝난 MeetingSummary → Neo4j 적재 → 이번 회의 액션아이템 담당자 추천 → 결과 조회.

같은 회의로 다시 요청해도 된다: 적재는 건너뛰고, 추천은 아직 결과가 없는(실패했던) 건만 다시 돌린다.
"""

from __future__ import annotations

from typing import Any

from followup import settings
from followup.adapters.llm import usage
from followup.adapters.llm.openai_client import OpenAIClient
from followup.adapters.neo4j import action_items, load
from followup.adapters.neo4j.driver import get_driver
from followup.analysis.recommend import recommend_agent as agent

LOAD_EFFORT = "high"  # Topic · 후속 업무 판단 (playground에서 비교해 고른 값)


def recommend_for_meeting(summary: dict[str, Any]) -> list[dict[str, Any]]:
    from langchain_openai import ChatOpenAI  # 무거운 import는 실제로 추천할 때만

    settings.load_env()
    model = settings.model_name()
    meeting_id = summary["meeting_id"]
    # 실행 1회의 API 호출 상한: 적재(Topic 판단 · 후속 업무 연결)와 추천(해석)이 함께 씀
    agendas = load.normalize_meeting(summary)["agendas"]
    n_items = sum(len(d["actions"]) for ag in agendas for d in ag["discussions"])
    budget = agent.CallBudget(settings.recommend_max_llm_calls(len(agendas), n_items))
    loader = OpenAIClient(model=model, reasoning_effort=LOAD_EFFORT)

    def counted_loader(system: str, user: str) -> str:
        budget.take("적재")
        return loader(system, user)

    try:
        load.load_meeting(summary, counted_loader)
    finally:    # 적재 중 AI 호출 사용량 기록 (중간에 실패해도 이미 쓴 호출은 기록)
        for call in loader.calls:
            usage.record_chat(call.get("model") or model, call.get("usage"), meeting_id=meeting_id, feature="recommend")
    llm = ChatOpenAI(model=model, reasoning_effort=agent.LLM_EFFORT, timeout=120, max_retries=agent.LLM_RETRIES,
                     callbacks=[usage.langchain_callback("recommend", meeting_id)])
    graph = agent.build_graph(agent.neo4j_tools(get_driver(), summary["meeting_id"]), llm, budget)
    agent.run_once(graph, force=False, metadata={"meeting_id": summary["meeting_id"]})
    return action_items.meeting_items(summary["meeting_id"])
