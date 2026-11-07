"""시나리오 테스트: 정상 / 후보 없음 / LLM 실패 / 스킬 해석 실패(빌릴 것 없음·있음) / 맥락 조회 실패.
고장은 tools 나 LLM 을 바꿔 끼워 만든다. 실제 LLM은 s01 · s02 만 호출한다."""
import pytest

from conftest import MEETINGS, FakeLLM, make_tools, run

UNKNOWN_SKILL = {"required_skills": [{"skill_id": "__no_such_skill__", "importance": "core", "reason": "x"}],
                 "unmatched_skills": ["양자 컴퓨팅"]}


@pytest.fixture(scope="module")
def item_id(driver):
    ids = make_tools(driver, MEETINGS[0], [])["items"](True)
    if not ids:
        pytest.skip(f"{MEETINGS[0]}에 담당자가 없는 open 액션아이템이 없음")
    return ids[0]


def one(driver, item_id, saved, **overrides):
    """액션아이템 하나만 돌리게 한다."""
    return make_tools(driver, MEETINGS[0], saved, items=lambda force: [item_id], **overrides)


def test_s01_normal(driver, llm):
    saved = []
    results = run(make_tools(driver, MEETINGS[0], saved), llm).results
    assert results and all(r["status"] != "error" for r in results)
    assert len(saved) == len(results), "error 가 아니면 전부 저장 대상"
    for r in results:
        assert len(r["recommendations"]) <= 6
        assert all(x["rank"] in (1, 2) for x in r["recommendations"]), "직급별 Top-2"


def test_s02_no_candidate(driver, llm, item_id):
    saved = []
    [r] = run(one(driver, item_id, saved, candidates=lambda: ([], False)), llm).results
    assert r["status"] == "no_candidate" and r["recommendations"] == []
    assert saved, "후보 없음도 결과로 저장"


def test_s03_llm_failure(driver, item_id):
    saved = []
    [r] = run(one(driver, item_id, saved), FakeLLM(ConnectionError("LLM 연결 실패"))).results
    assert r["status"] == "error"
    assert saved == [], "LLM 실패는 저장하지 않아 다음 요청에 다시 처리"


def test_s04_skill_unclear(driver, item_id):
    saved = []
    [r] = run(one(driver, item_id, saved, similar=lambda _id: []), FakeLLM(UNKNOWN_SKILL)).results
    assert r["status"] == "skill_unclear" and r["recommendations"] == []
    assert r["missing_skills"] == ["양자 컴퓨팅"]


def test_s05_borrow_similar_skills(driver, item_id):
    saved = []
    tools = one(driver, item_id, saved)
    skill = tools["catalog"]()["skills"][0]["id"]
    similar = [{"id": "AI-SIMILAR", "description": "비슷한 업무",
                "required_skills": [{"skill_id": skill, "importance": "core", "reason": "빌려 온 스킬"}]}]
    [r] = run({**tools, "similar": lambda _id: similar}, FakeLLM(UNKNOWN_SKILL)).results
    assert r["skill_source"] == "similar_item" and r["borrowed_from"] == "AI-SIMILAR"
    assert [x["skill_id"] for x in r["required_skills"]] == [skill]


def test_s06_context_failure(driver, item_id):
    def broken(_id):
        raise ConnectionError("Neo4j 연결 실패")

    saved = []
    [r] = run(one(driver, item_id, saved, context=broken), FakeLLM(UNKNOWN_SKILL)).results
    assert r["status"] == "error" and saved == []
    assert any("조회 실패" in w for w in r["warnings"])
