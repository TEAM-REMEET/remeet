"""정량 평가: eval_questions.json 전체로 의도 분류 정확도, 근거 적중률(recall), no_evidence 판정 정확도, 근거 조회율, 응답 시간."""
import pytest

from conftest import QUESTIONS, run

MIN_INTENT_ACC = 0.8
MIN_RECALL = 0.7
MIN_NO_EVIDENCE_ACC = 0.9
MIN_TOOL_USAGE = 0.9


@pytest.fixture(scope="module")
def results(tools, llm):
    return [(q, run(tools, llm, q["question"])) for q in QUESTIONS]


def test_task_success_and_tool_usage(results):
    rows = []
    for q, r in results:
        a, expected = r.answer, set(q["expected_agenda_ids"])
        cited = {s["agenda_id"] for s in a["sources"]}
        rows.append({"id": q["id"], "intent_ok": a["intent"] == q["intent"],
                     "recall": len(expected & cited) / len(expected) if expected else None,
                     "no_evidence_ok": (a["answer_status"] == "no_evidence") == q["expect_no_evidence"],
                     "tool_used": r.tool_calls >= 1, "latency": r.latency_sec})
        print(f"{q['id']}: intent={a['intent']} status={a['answer_status']} cited={sorted(cited)} {r.latency_sec:.1f}s")

    recalls = [x["recall"] for x in rows if x["recall"] is not None]
    intent_acc = sum(x["intent_ok"] for x in rows) / len(rows)
    recall = sum(recalls) / len(recalls)
    no_evidence_acc = sum(x["no_evidence_ok"] for x in rows) / len(rows)
    tool_usage = sum(x["tool_used"] for x in rows) / len(rows)
    mean_latency = sum(x["latency"] for x in rows) / len(rows)

    print(f"\nIntent: {intent_acc:.1%}, recall: {recall:.1%}, no_evidence: {no_evidence_acc:.1%}, "
          f"tool use: {tool_usage:.1%}, mean latency: {mean_latency:.2f}s")

    assert intent_acc >= MIN_INTENT_ACC
    assert recall >= MIN_RECALL
    assert no_evidence_acc >= MIN_NO_EVIDENCE_ACC
    assert tool_usage >= MIN_TOOL_USAGE
    # Latency is reported, not hard-failed: cloud API response times vary.
