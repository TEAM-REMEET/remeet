"""시나리오 테스트: 정상 질문 / 인사말 / 회의에 없는 내용 / 근거 조회 실패(고장 주입)."""
from conftest import BY_ID, run
from followup.analysis.onboarding import onboarding_agent as agent


def test_s01_normal_search(tools, llm):
    q = BY_ID["D1"]
    r = run(tools, llm, q["question"])
    assert r.answer["answer_status"] in ("grounded", "partial")
    assert r.tool_calls >= 1
    assert set(q["expected_agenda_ids"]) & {s["agenda_id"] for s in r.answer["sources"]}


def test_s02_greeting_without_search(tools, llm):
    r = run(tools, llm, "안녕하세요!")
    assert r.answer["intent"] == "other"
    assert r.tool_calls == 0
    assert r.answer["summary"].strip()


def test_s03_unknown_fact(tools, llm):
    r = run(tools, llm, BY_ID["N1"]["question"])
    assert r.answer["answer_status"] == "no_evidence"
    assert r.answer["summary"] == agent.NO_EVIDENCE
    assert r.answer["sources"] == []


def test_s04_search_failure(tools, llm):
    def broken(*_):
        raise ConnectionError("검색 시스템 연결 실패")

    broken_tools = {k: broken if k.endswith("_evidence") else v for k, v in tools.items()}
    r = run(broken_tools, llm, BY_ID["D1"]["question"])
    assert r.tool_calls >= 1
    assert r.answer["answer_status"] == "no_evidence" and r.answer["sources"] == []
    assert any("근거 조회 실패" in w for w in r.answer["warnings"])
