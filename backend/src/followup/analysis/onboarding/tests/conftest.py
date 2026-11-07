"""온보딩 에이전트 평가 공통 준비: 실제 Neo4j · OpenAI 로 ask 그래프를 돌린다 (질문마다 API 비용 발생).

실행 전: remeet/.env (NEO4J_*, OPENAI_API_KEY), PYTHONPATH=backend/src,
DB에 TS3003 회의와 Decision 노드가 있어야 함 (Decision은 API로 한 번 물어보면 backfill로 생김).
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from followup.adapters.neo4j.driver import get_driver
from followup.analysis.onboarding import onboarding_agent as agent
from followup.application.ask_meetings import build_llm

QUESTIONS = json.loads((Path(__file__).parent / "eval_questions.json").read_text(encoding="utf-8"))
BY_ID = {q["id"]: q for q in QUESTIONS}


def run(tools: dict, llm, question: str) -> SimpleNamespace:
    """근거 조회(*_evidence) 호출 수와 응답 시간을 같이 잰다."""
    calls = []

    def counted(name, fn):
        def f(*args):
            calls.append(name)
            return fn(*args)
        return f

    t = {k: counted(k, v) if k.endswith("_evidence") else v for k, v in tools.items()}
    start = time.perf_counter()
    answer = agent.ask(agent.build_ask_graph(t, llm), t, question)
    return SimpleNamespace(answer=answer, tool_calls=len(calls), latency_sec=time.perf_counter() - start)


@pytest.fixture(scope="session")
def llm():
    return build_llm()


@pytest.fixture(scope="session")
def tools():
    driver = get_driver()
    yield agent.neo4j_tools(driver)
    driver.close()
