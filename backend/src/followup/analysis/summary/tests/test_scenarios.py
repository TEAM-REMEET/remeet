"""시나리오 테스트: 정상 / 입력 형식 오류 / 빈 생성 결과 / LLM 실패 / 발화 없음.
고장은 CountingLLM(reply=…)으로 LLM을 바꿔 끼워 만든다. 실제 LLM은 s01만 호출한다."""
import pytest

from conftest import MEETINGS, CountingLLM, active_items, raw_text, run, transcript_text
from followup import settings as cfg
from followup.analysis.summary.preprocessing import TranscriptFormatError
from followup.analysis.summary.schemas import LLMMeetingSummary
from followup.analysis.summary.steps import EmptySummaryError

MID = MEETINGS[0]


def test_s01_normal(summaries):
    summary = summaries(MID).summary
    n = len(summary.transcript)
    assert summary.verification.status in ("PASSED", "REVIEW_REQUIRED")
    assert summary.active_agendas(), "안건이 하나도 없음"
    for it in active_items(summary):
        assert it.text.strip()
        assert all(1 <= i <= n for i in it.evidence_utterance_indices), f"{it.item_id}: 원문 범위 밖 근거"
    results = summary.meeting_results
    for item_id in results.decision_item_ids + results.action_item_ids + results.unresolved_item_ids:
        assert summary.find_item(item_id), f"회의 결과가 없는 항목을 가리킴: {item_id}"


def test_s02_bad_format_stops_before_llm():
    # 데이터셋 원문은 참여자가 '이름_직무_userid' 형식이 아님 → LLM 호출 전에 중단
    llm = CountingLLM(reply=AssertionError("호출되면 안 됨"))
    with pytest.raises(TranscriptFormatError):
        run(MID, raw_text(MID), llm)
    assert llm.calls == 0


def test_s03_empty_generate():
    # 생성 결과가 비면 GENERATE_EMPTY_RETRIES번 다시 호출하고, 그래도 비면 오류 (빈 요약을 저장하지 않음)
    llm = CountingLLM(reply=LLMMeetingSummary(agendas=[]))
    with pytest.raises(EmptySummaryError):
        run(MID, transcript_text(MID), llm)
    assert llm.calls == 1 + cfg.GENERATE_EMPTY_RETRIES


def test_s04_llm_failure():
    # LLM 오류는 삼키지 않고 호출한 쪽(API)까지 올라가야 함 — 반쪽짜리 요약을 돌려주지 않음
    llm = CountingLLM(reply=ConnectionError("LLM 연결 실패"))
    with pytest.raises(ConnectionError):
        run(MID, transcript_text(MID), llm)
    assert llm.calls == 1


def test_s05_no_utterances():
    header = "\n".join(transcript_text(MID).splitlines()[:3])     # 메타데이터만
    llm = CountingLLM(reply=AssertionError("호출되면 안 됨"))
    with pytest.raises(ValueError):
        run(MID, header, llm)
    assert llm.calls == 0
