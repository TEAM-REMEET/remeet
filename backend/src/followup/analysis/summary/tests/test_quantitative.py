"""정량 평가: 정답셋(GoldenSet)의 안건 구간과 비교한 커버리지 · 경계 일치, 근거 연결률, 검증 통과율, 호출 수 · 시간.
LLM 채점 없이 숫자로 계산한다 (요약 생성 비용만 듦)."""
from conftest import MEETINGS, active_items, golden

BOUNDARY_TOLERANCE = 10     # 안건 시작 발화가 정답 경계에서 이만큼(발화 수) 안에 있으면 일치로 봄
MIN_COVERAGE = 0.7          # 정답 구간 중 요약이 근거로 다룬 비율 — 잡담 · 진행 안내 구간은 빠질 수 있어 100%를 요구하지 않음
MIN_EVIDENCE_RATE = 0.95    # 근거 발화가 하나 이상 붙은 항목 비율 (PRD 목표 100%, 후처리가 잘못된 번호를 지우면 빈 근거가 생김)


def segment_coverage(summary, segments) -> list[bool]:
    """정답 구간마다: 그 구간 안의 발화를 근거로 쓴 항목이 하나라도 있는가"""
    used = {i for it in active_items(summary) for i in it.evidence_utterance_indices}
    return [any(s["start_utterance"] <= i <= s["end_utterance"] for i in used) for s in segments]


def boundary_hits(summary, boundaries) -> list[bool]:
    """정답 경계마다: 근처에서 시작하는 안건이 있는가 (안건은 처음 논의된 순서라 시작 발화로 비교)"""
    starts = [a.evidence_range[0] for a in summary.active_agendas() if a.evidence_range]
    return [any(abs(b - s) <= BOUNDARY_TOLERANCE for s in starts) for b in boundaries]


def test_against_golden_set(summaries):
    covered, hits, with_evidence, items, passed, calls, latency = [], [], 0, 0, 0, 0, 0.0
    for mid in MEETINGS:
        r, gold = summaries(mid), golden(mid)
        cov = segment_coverage(r.summary, gold["segments"])
        hit = boundary_hits(r.summary, gold["boundary_positions"])
        its = active_items(r.summary)
        ev = sum(bool(it.evidence_utterance_indices) for it in its)
        status = r.summary.verification.status
        missed = [s["subject"] for s, c in zip(gold["segments"], cov) if not c]
        print(f"\n{mid}: agendas={len(r.summary.active_agendas())} (gold segments={len(gold['segments'])}) "
              f"coverage={sum(cov)}/{len(cov)} boundary={sum(hit)}/{len(hit)} evidence={ev}/{len(its)} "
              f"status={status} calls={r.llm_calls} latency={r.latency_sec:.1f}s"
              + (f"\n  missed: {missed}" if missed else ""))
        covered += cov
        hits += hit
        with_evidence += ev
        items += len(its)
        passed += status == "PASSED"
        calls += r.llm_calls
        latency += r.latency_sec

    n = len(MEETINGS)
    coverage = sum(covered) / len(covered)
    boundary = sum(hits) / len(hits) if hits else 1.0
    evidence_rate = with_evidence / items if items else 0.0
    print(f"\nCoverage: {coverage:.1%}, boundary match: {boundary:.1%}, evidence: {evidence_rate:.1%}, "
          f"verify passed: {passed}/{n}, mean LLM calls: {calls / n:.1f}, mean latency: {latency / n:.1f}s")

    assert items, "요약 항목이 하나도 없음"
    assert coverage >= MIN_COVERAGE
    assert evidence_rate >= MIN_EVIDENCE_RATE
    # 경계 일치 · 검증 통과율 · 시간은 기록만 한다: 안건 묶는 단위가 정답셋 구간과 꼭 같을 필요는 없고, API 응답 시간은 변동이 큼
