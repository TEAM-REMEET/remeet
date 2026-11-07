"""LLM-as-a-Judge (deepeval): 요약이 원문에 충실한가(Faithfulness), 정답셋의 주요 주제를 빠짐없이 다루는가(GEval).
요약 생성 외에 채점 호출 비용이 든다 (pip install deepeval). 비용을 줄이려면 SUMMARY_EVAL_MEETINGS를 1개로."""
import os

from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from conftest import MEETINGS, golden, summary_text

THRESHOLD = 0.7

COVERAGE_CRITERIA = """expected_output은 사람이 회의를 구간으로 나누고 구간마다 적은 주제와 요약(정답)이다.
actual_output은 요약 에이전트가 만든 회의록(안건 제목과 항목)이다.
1) 정답의 주요 주제와 결론이 회의록에 빠짐없이 들어 있는가
2) 정답에 있는 결정 내용과 다르게(반대로) 적힌 것은 없는가
표현 · 순서 · 안건 묶는 단위의 차이는 감점하지 않는다. 회의 시작 인사 · 진행 안내 같은 구간은 빠져도 감점하지 않는다."""


def test_summary_quality(summaries):
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    faithfulness = FaithfulnessMetric(threshold=THRESHOLD, model=model, include_reason=True)
    coverage = GEval(name="정답셋 주제 커버리지", criteria=COVERAGE_CRITERIA, threshold=THRESHOLD, model=model,
                     evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT])

    faith_scores, cover_scores = [], []
    for mid in MEETINGS:
        summary = summaries(mid).summary
        case = LLMTestCase(
            input=summary.meeting_info.title or mid,
            actual_output=summary_text(summary),
            expected_output="\n".join(f"- {s['subject']}: {s['summary']}" for s in golden(mid)["segments"]),
            retrieval_context=["\n".join(f"[{u.utterance_index}] {u.text}" for u in summary.transcript)],
        )
        faithfulness.measure(case)
        coverage.measure(case)
        faith_scores.append(faithfulness.score)
        cover_scores.append(coverage.score)
        print(f"\n{mid} Faithfulness: {faithfulness.score} ({faithfulness.reason})")
        print(f"{mid} Coverage: {coverage.score} ({coverage.reason})")

    faith, cover = sum(faith_scores) / len(faith_scores), sum(cover_scores) / len(cover_scores)
    print(f"\nFaithfulness mean: {faith:.2f}, coverage mean: {cover:.2f}")
    assert faith >= THRESHOLD
    assert cover >= THRESHOLD
