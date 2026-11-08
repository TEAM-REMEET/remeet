"""POST /api/meetings — 회의 등록 폼 입력으로 요약 · 액션 아이템 추출 (응답: MeetingSummary)
POST /api/meeting-jobs — 같은 입력을 백그라운드로 실행하고 바로 응답 (202 · 사람마다 1건, 실행 중이면 409)
GET /api/meeting-jobs/current — 내 작업 상태와 입력값 (none · running · done(결과 포함) · failed) / DELETE — 끝난 작업 지우기
POST /api/meetings/{meeting_id}/review — 사용자 수정(HITL) 반영 · 근거 연결 · 검증 → 최종 JSON (응답: MeetingSummary)
PUT /api/meetings/{meeting_id}/final/assignees — 담당자 추천 단계의 담당자 · 마감일 변경 → 최종 JSON에 반영 (LLM 없음)

오류 응답
    401  로그인하지 않음 · 출입증 만료 (LLM을 호출하지 않음)
    404  저장된 회의가 없음 (LLM을 호출하지 않음)
    422  입력 형식 오류 · 입력 길이 상한 초과 (settings.MAX_TRANSCRIPT_CHARS · MAX_STEP_INPUT_CHARS, 상한을 넘는 LLM 호출은 하지 않음)
    504  확정 처리가 제한 시간을 넘김 — 같은 요청을 다시 보내면 이어서 진행됨
    502  요약 실행 실패 — 같은 요청을 다시 보내면 실패한 단계부터 진행됨
"""

import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from followup.analysis.summary.hitl import HitlEditError
from followup.analysis.summary.preprocessing import TranscriptFormatError
from followup.analysis.summary.schemas import AssignChanges, HitlEdits, MeetingSummary
from followup.analysis.summary.transcript_builder import MeetingSubmission
from followup.application.ports import StructuredLLM, SummaryStore
from followup.application import meeting_jobs
from followup.application.review_meeting import MeetingNotFoundError, review_meeting, save_assignments
from followup.application.summarize_meeting import LLMInputLimitError, summarize_submission
from followup.application.time_limit import TimeLimitExceeded
from followup.entrypoints.api.routes.auth import current_user
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
# nginx가 /api/를 외부에 공개하므로, 로그인한 사람만 호출하게 함 (LLM 비용 보호)
@router.post("/meetings", response_model=MeetingSummary, dependencies=[Depends(current_user)])
def create_meeting(submission: MeetingSubmission, llm: StructuredLLM = Depends(get_llm),
                   store: SummaryStore = Depends(get_store)) -> MeetingSummary:
    try:
        return summarize_submission(submission, llm=llm, store=store)
    except TranscriptFormatError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e
    except LLMInputLimitError as e:
        logger.warning("요약 입력 상한 초과: %s", e)
        raise HTTPException(status_code=422, detail=meeting_jobs.INPUT_LIMIT_MESSAGE) from e
    except Exception as e:
        logger.exception("요약 실행 실패")      # 원인은 서버 로그에만 남김 (응답에 내부 정보를 넣지 않음)
        raise HTTPException(status_code=502, detail="요약 실행에 실패했어요. 같은 내용으로 다시 요청해 주세요.") from e


@router.post("/meetings/{meeting_id}/review", response_model=MeetingSummary, dependencies=[Depends(current_user)])
def review(meeting_id: str, edits: HitlEdits, llm: StructuredLLM = Depends(get_llm),
           store: SummaryStore = Depends(get_store)) -> MeetingSummary:
    try:
        return review_meeting(meeting_id, edits, llm=llm, store=store)
    except MeetingNotFoundError as e:
        raise HTTPException(status_code=404, detail="저장된 회의를 찾을 수 없어요.") from e
    except HitlEditError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e
    except LLMInputLimitError as e:
        logger.warning("근거 연결 입력 상한 초과 %s: %s", meeting_id, e)
        raise HTTPException(status_code=422, detail="회의 내용이 길어 근거 연결에 쓸 수 있는 입력 길이를 넘었어요.") from e
    except TimeLimitExceeded as e:
        logger.warning("사용자 수정 반영 시간 초과 %s", meeting_id)
        raise HTTPException(status_code=504, detail="근거 연결이 제한 시간(10분) 안에 끝나지 않았어요. 다시 확정하면 이어서 진행해요.") from e
    except Exception as e:
        logger.exception("사용자 수정 반영 실패 %s", meeting_id)
        raise HTTPException(status_code=502, detail="회의록 확정에 실패했어요. 같은 내용으로 다시 요청해 주세요.") from e


@router.put("/meetings/{meeting_id}/final/assignees", response_model=MeetingSummary, dependencies=[Depends(current_user)])
def save_final_assignees(meeting_id: str, changes: AssignChanges,
                         store: SummaryStore = Depends(get_store)) -> MeetingSummary:
    try:
        return save_assignments(meeting_id, changes, store=store)
    except MeetingNotFoundError as e:
        raise HTTPException(status_code=404, detail="확정된 회의록을 찾을 수 없어요.") from e
    except HitlEditError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e


class MeetingJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: MeetingSubmission
    draft: dict = {}        # 화면 복원에만 쓰는 값 (새 프로젝트 이름 등)


@router.post("/meeting-jobs", status_code=202)
def start_meeting_job(body: MeetingJobRequest, user: dict = Depends(current_user), llm: StructuredLLM = Depends(get_llm),
                      store: SummaryStore = Depends(get_store)) -> dict:
    try:
        return meeting_jobs.start_job(user["user_id"], body.request, body.draft, llm=llm, store=store)
    except TranscriptFormatError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e
    except meeting_jobs.JobRunningError as e:
        raise HTTPException(status_code=409, detail="이미 분석 중인 회의가 있어요. 끝난 뒤에 등록해 주세요.") from e


@router.get("/meeting-jobs/current")
def current_meeting_job(user: dict = Depends(current_user), store: SummaryStore = Depends(get_store)) -> dict:
    return meeting_jobs.current_job(user["user_id"], store) or {"status": "none"}


@router.delete("/meeting-jobs/current", status_code=204)
def clear_meeting_job(user: dict = Depends(current_user), store: SummaryStore = Depends(get_store)) -> None:
    try:
        meeting_jobs.clear_job(user["user_id"], store)
    except meeting_jobs.JobRunningError as e:
        raise HTTPException(status_code=409, detail="분석 중인 회의는 지울 수 없어요.") from e
