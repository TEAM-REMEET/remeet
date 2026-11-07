"""추천 에이전트 평가 공통 준비: 실제 Neo4j · OpenAI 로 돌리되 결과는 DB에 저장하지 않는다 (save 를 바꿔 끼움).

실행 전: remeet/.env (NEO4J_*, OPENAI_API_KEY, OPENAI_MODEL), PYTHONPATH=backend/src.
평가할 회의는 RECOMMEND_EVAL_MEETINGS="m-1023,m-1030" 처럼 바꾼다 (기본값은 playground 데이터의 회의 ID).
"""
import json
import os
import time
from types import SimpleNamespace

import pytest

from followup import settings
from followup.adapters.neo4j.driver import get_driver
from followup.analysis.recommend import recommend_agent as agent

MEETINGS = os.getenv("RECOMMEND_EVAL_MEETINGS", "m-1023,m-1030,m-1103,m-1106,m-1110").split(",")


def make_tools(driver, meeting_id: str, saved: list, **overrides) -> dict:
    """save 는 DB 대신 saved 목록에 담는다. overrides 로 조회 함수를 고장 난 것으로 바꿔 끼울 수 있다."""
    return {**agent.neo4j_tools(driver, meeting_id), "save": saved.append, **overrides}


def run(tools: dict, llm) -> SimpleNamespace:
    """force=True: 이미 추천받은 액션아이템도 다시 돌린다 (담당자가 정해진 건은 제외)."""
    start = time.perf_counter()
    results = agent.run_once(agent.build_graph(tools, llm), force=True)
    return SimpleNamespace(results=results, latency_sec=time.perf_counter() - start)


class FakeLLM:
    """정해 둔 JSON을 돌려주거나, 예외를 넣으면 그 예외를 던진다."""

    def __init__(self, reply):
        self.reply = reply

    def bind(self, **_):
        return self

    def invoke(self, _messages):
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=json.dumps(self.reply, ensure_ascii=False))


@pytest.fixture(scope="session")
def llm():
    from langchain_openai import ChatOpenAI

    settings.load_env()
    # 백엔드(recommend_assignees.py)와 같은 설정
    return ChatOpenAI(model=settings.model_name(), reasoning_effort=agent.LLM_EFFORT, timeout=120,
                      max_retries=agent.LLM_RETRIES)


@pytest.fixture(scope="session")
def driver():
    d = get_driver()
    yield d
    d.close()
