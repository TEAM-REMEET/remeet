"""POST /api/meetings — 회의 등록 폼 입력으로 요약 · 액션 아이템 추출 (응답: MeetingSummary)

오류 응답
    422  입력 형식 오류 (LLM을 호출하지 않음)
    502  요약 실행 실패 — 같은 요청을 다시 보내면 실패한 단계부터 진행됨
"""

import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from followup.analysis.summary.preprocessing import TranscriptFormatError
from followup.analysis.summary.schemas import MeetingSummary
from followup.analysis.summary.transcript_builder import MeetingSubmission
from followup.application.ports import StructuredLLM, SummaryStore
from followup.application.summarize_meeting import summarize_submission
from followup.entrypoints.bootstrap import build_llm, build_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["summary"])


@lru_cache(maxsize=1)
def get_llm() -> StructuredLLM:
    return build_llm()


@lru_cache(maxsize=1)
def get_store() -> SummaryStore:
    return build_store()


# 동기 함수로 두어 요약이 실행되는 동안 다른 요청(헬스체크 등)을 막지 않음
@router.post("/meetings", response_model=MeetingSummary)
def create_meeting(submission: MeetingSubmission, llm: StructuredLLM = Depends(get_llm),
                   store: SummaryStore = Depends(get_store)) -> MeetingSummary:
    try:
        return summarize_submission(submission, llm=llm, store=store)
    except TranscriptFormatError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e
    except Exception as e:
        logger.exception("요약 실행 실패")      # 원인은 서버 로그에만 남김 (응답에 내부 정보를 넣지 않음)
        raise HTTPException(status_code=502, detail="요약 실행에 실패했어요. 같은 내용으로 다시 요청해 주세요.") from e