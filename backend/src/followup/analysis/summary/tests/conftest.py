"""요약 에이전트 평가 공통 준비: 실제 OpenAI로 summarize_meeting 그래프(생성 → 검증 ⇄ 재수정)를 돌린다.
결과는 저장하지 않고(save=False) 단계별 응답 캐시도 쓰지 않는다(use_cache=False) — 매번 새로 호출해 API 비용 발생.

실행 전: remeet/.env (OPENAI_API_KEY, OPENAI_MODEL), PYTHONPATH=backend/src.
평가할 회의는 SUMMARY_EVAL_MEETINGS="TS3003a,TS3003b" 처럼 바꾼다 (기본값 TS3003 a~d).
입력 원문: evals/datasets/{id}/{id}.txt / 정답셋: playground/chaewon/dataset/GoldenSet/hj/{id}.json (안건 구간 경계 · 주제 · 요약)
"""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from followup import settings
from followup.adapters.llm.structured_client import OpenAIStructuredClient
from followup.analysis.summary.preprocessing import load_transcript
from followup.application.summarize_meeting import summarize_meeting

ROOT = Path(__file__).resolve().parents[6]      # remeet/
DATASETS = ROOT / "evals" / "datasets"
GOLDEN = ROOT / "playground" / "chaewon" / "dataset" / "GoldenSet" / "hj"
settings.load_env()     # 가짜 LLM 시나리오도 summarize_meeting이 OPENAI_MODEL을 읽으므로 모듈 로드 때 읽음
import baseline

TARGET = os.getenv("SUMMARY_EVAL_TARGET", "agent")   # agent | baseline
MEETINGS = os.getenv("SUMMARY_EVAL_MEETINGS", "TS3003a,TS3003b,TS3003c,TS3003d").split(",")


def raw_text(meeting_id: str) -> str:
    """데이터셋 원문 그대로 (참여자가 '프로젝트 매니저, …' 형식이라 파이프라인 입력 형식이 아님)"""
    return (DATASETS / meeting_id / f"{meeting_id}.txt").read_text(encoding="utf-8")


def transcript_text(meeting_id: str) -> str:
    """원문 메타데이터 3줄을 파이프라인 형식으로 바꾼다. 본문은 그대로라 발화 번호 = 정답셋 position.

    '회의 대주제' → '회의 제목', 참여자 '역할' → '역할_역할_u1' (이름이 없는 데이터라 역할을 이름으로 씀).
    '발화자' 줄은 넣지 않는다 → none 모드 (한 줄 = 발화 하나, 화자 없음).
    """
    lines = raw_text(meeting_id).splitlines()
    meta = dict(line.split(":", 1) for line in lines[:3])
    roles, _, count = meta["참여자"].rpartition("(")
    people = ", ".join(f"{r.strip().replace(' ', '')}_{r.strip()}_u{n}"
                       for n, r in enumerate(roles.split(","), 1))
    header = [f"회의 제목:{meta['회의 대주제']}", f"참여자: {people} ({count}", f"일시:{meta['일시']}"]
    return "\n".join(header + lines[3:])


def golden(meeting_id: str) -> dict:
    return json.loads((GOLDEN / f"{meeting_id}.json").read_text(encoding="utf-8"))


class CountingLLM:
    """실제 LLM 호출 수를 센다. reply를 주면 실제 LLM 대신 그 값을 돌려주거나(예외면 던짐) — 고장 주입용."""

    def __init__(self, inner=None, reply=None):
        self.inner, self.reply, self.calls = inner, reply, 0

    def parse(self, system, user, schema, effort):
        self.calls += 1
        if self.inner is not None:
            return self.inner.parse(system, user, schema, effort)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def run(meeting_id: str, text: str, llm) -> SimpleNamespace:
    if TARGET == "baseline" and not isinstance(llm, CountingLLM):   # 고장 주입 시나리오는 에이전트로
        return baseline.run(meeting_id, text, llm)
    counting = llm if isinstance(llm, CountingLLM) else CountingLLM(llm)
    start = time.perf_counter()
    # store=None: save=False · use_cache=False 이면 store를 쓰지 않음
    summary = summarize_meeting(meeting_id, text, llm=counting, store=None, save=False, use_cache=False)
    return SimpleNamespace(summary=summary, latency_sec=time.perf_counter() - start, llm_calls=counting.calls)


def active_items(summary) -> list:
    return [it for a in summary.active_agendas() for it in a.iter_items()]


def summary_text(summary) -> str:
    """채점용 회의록 텍스트 (안건 제목 + 항목 문장)"""
    out = []
    for a in summary.active_agendas():
        out.append(f"안건 {a.agenda_number}. {a.agenda_title}")
        out += [f"- {it.text}" for it in a.iter_items()]
    return "\n".join(out)


@pytest.fixture(scope="session")
def llm():
    return OpenAIStructuredClient()


@pytest.fixture(scope="session")
def summaries(llm):
    """회의별 실행 결과를 한 번만 만든다 (같은 세션의 테스트끼리 공유)."""
    done = {}

    def get(meeting_id: str) -> SimpleNamespace:
        if meeting_id not in done:
            text = transcript_text(meeting_id)
            # LLM 호출 전에 확인: 발화 번호가 정답셋 position과 어긋나면 비교가 무의미함
            count, expected = len(load_transcript(meeting_id, text)["transcript"]), golden(meeting_id)["utterance_count"]
            assert count == expected, f"{meeting_id}: 발화 수 {count} ≠ 정답셋 {expected}"
            done[meeting_id] = run(meeting_id, text, llm)
        return done[meeting_id]

    return get
