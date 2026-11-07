"""정량 평가 (정답표 없음): 추천 성공률, 오류율, LLM 스킬 직접 해석률, 같은 입력 2회 실행 일관성, 응답 시간."""
import pytest

from conftest import MEETINGS, make_tools, run

MIN_SUCCESS = 0.8      # 추천이 나온(pending_manager_review) 비율
MAX_ERROR = 0.05       # error 비율
MIN_LLM_SKILL = 0.8    # 유사 업무에서 빌리지 않고 LLM이 스킬을 직접 고른 비율 (추천 성공 건 중)
MIN_CONSISTENCY = 0.7  # 2회 실행 간 추천 인원·요구 스킬 겹침(Jaccard) 평균


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


@pytest.fixture(scope="module")
def runs(driver, llm):
    """회의마다 2번씩 실행 (두 번째는 일관성 측정용)."""
    return {mid: [run(make_tools(driver, mid, []), llm) for _ in range(2)] for mid in MEETINGS}


def test_success_and_consistency(runs):
    first = [r for a, _ in runs.values() for r in a.results]
    assert first, "평가할 액션아이템이 없음 (RECOMMEND_EVAL_MEETINGS 확인)"
    second = {r["action_item_id"]: r for _, b in runs.values() for r in b.results}

    users = lambda r: {x["user_id"] for x in r.get("recommendations") or []}
    skills = lambda r: {x["skill_id"] for x in r.get("required_skills") or []}
    user_sim, skill_sim = [], []
    for r in first:
        other = second.get(r["action_item_id"], {})
        user_sim.append(jaccard(users(r), users(other)))
        skill_sim.append(jaccard(skills(r), skills(other)))
        print(f"{r['action_item_id']}: {r['status']} skills={sorted(skills(r))} "
              f"source={r.get('skill_source')} recs={len(users(r))} "
              f"same_users={user_sim[-1]:.2f} same_skills={skill_sim[-1]:.2f}")

    ok = [r for r in first if r["status"] == "pending_manager_review"]
    success = len(ok) / len(first)
    error = sum(r["status"] == "error" for r in first) / len(first)
    llm_skill = sum(r.get("skill_source") == "llm" for r in ok) / len(ok) if ok else 0.0
    consistency = (sum(user_sim) + sum(skill_sim)) / (len(user_sim) + len(skill_sim))
    latency = sum(a.latency_sec for a, _ in runs.values()) / len(runs)

    print(f"\nItems: {len(first)}, success: {success:.1%}, error: {error:.1%}, llm skill: {llm_skill:.1%}, "
          f"same users: {sum(user_sim) / len(user_sim):.1%}, same skills: {sum(skill_sim) / len(skill_sim):.1%}, "
          f"mean latency per meeting: {latency:.2f}s")

    assert success >= MIN_SUCCESS
    assert error <= MAX_ERROR
    assert llm_skill >= MIN_LLM_SKILL
    assert consistency >= MIN_CONSISTENCY
    # Latency is reported, not hard-failed: cloud API response times vary.
