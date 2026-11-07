"""LLM-as-a-Judge: answer relevance and faithfulness. Uses extra paid judge calls (pip install deepeval)."""
import os

from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from conftest import BY_ID, run


def test_answer_quality(tools, llm):
    question = BY_ID["D3"]["question"]
    a = run(tools, llm, question).answer
    # 근거 = 답변 문장에 인용된 논의 문장 + 인용된 발화 원문
    context = [c["point"] for st in a["sentences"] for c in st.get("citations", []) if c.get("point")]
    context += [u["text"] for utts in a.get("transcripts", {}).values() for u in utts if u.get("text")]
    assert context, "답변에 원문 근거가 붙어야 함"
    case = LLMTestCase(input=question, actual_output=a["summary"], retrieval_context=context)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    relevance = AnswerRelevancyMetric(threshold=0.7, model=model, include_reason=True)
    faithfulness = FaithfulnessMetric(threshold=0.7, model=model, include_reason=True)
    relevance.measure(case)
    faithfulness.measure(case)

    print(f"\nRelevance: {relevance.score} ({relevance.reason})")
    print(f"Faithfulness: {faithfulness.score} ({faithfulness.reason})")
    assert relevance.is_successful()
    assert faithfulness.is_successful()
