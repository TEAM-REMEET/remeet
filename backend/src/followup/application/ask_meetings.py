"""회의에 물어보기: 질문 → (새 결정 정리) → 온보딩 에이전트 → 근거가 달린 답 JSON.

결정 이력(Decision · SUPERSEDES)은 적재된 안건의 decisions[] 에서 만든다 (backfill).
처리한 안건은 건너뛰므로 새 회의가 없으면 조회 몇 번으로 끝난다.
shortcut: 질문마다 backfill 을 먼저 돌림, 회의 적재(load) 단계에서 결정까지 만들게 되면 여기서 빼기
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from followup import settings
from followup.adapters.neo4j.driver import get_driver
from followup.analysis.onboarding import onboarding_agent as agent

logger = logging.getLogger(__name__)


def build_llm():
    from langchain_openai import ChatOpenAI  # 무거운 import는 실제로 물어볼 때만

    settings.load_env()
    from followup.adapters.llm import usage

    return ChatOpenAI(model=settings.model_name(), reasoning_effort=agent.LLM_EFFORT, timeout=agent.LLM_TIMEOUT,
                      max_retries=agent.LLM_RETRIES, callbacks=[usage.langchain_callback("ask")])


def ask(question: str, *, llm=None, tools: dict | None = None) -> dict[str, Any]:
    """llm · tools 를 넘기면 그것으로 (테스트용), 아니면 OpenAI · Neo4j 로."""
    llm = llm or build_llm()
    tools = tools or agent.neo4j_tools(get_driver())
    try:
        result = agent.backfill(tools, llm, write=True)
        if result["hitl"]:  # 확신이 낮은 결정 변경은 자동으로 잇지 않음 (사람 확인 대상)
            logger.info("결정 변경 확인 필요 %d건: %s", len(result["hitl"]), [h["new_id"] for h in result["hitl"]])
    except Exception:  # 결정 정리가 실패해도 이미 있는 결정으로 답은 할 수 있음
        logger.exception("결정 정리(backfill) 실패 — 기존 결정으로 답변")
    graph = agent.build_ask_graph(tools, llm)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    return agent.ask(graph, tools, question, today)
