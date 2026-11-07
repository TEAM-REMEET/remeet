"""POST /api/ask — 회의에 물어보기 (온보딩 에이전트)

body = {"question": "PG사는 어디로 정했어요?"}
응답 = 근거가 달린 답 JSON (형식: playground/junbyong/onboarding_agent/온보딩 에이전트 화면·출력 보고서.md 5절)
    answer_status: grounded | partial | no_evidence | error (LLM 실패도 200 + error 로 옴, 화면이 안내 문구를 띄움)

오류 응답
    401  로그인하지 않음 (LLM을 호출하지 않음)
    422  질문이 비었거나 너무 김
    502  Neo4j 조회 등 실행 실패 — 잠시 뒤 다시 요청
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.application import ask_meetings
from followup.entrypoints.api.routes.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["ask"], dependencies=[Depends(current_user)])


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


# 동기 함수: 답하는 동안(LLM 2번, 수 초) 다른 요청을 막지 않음 · 로그인한 사람만 (LLM 비용 보호)
@router.post("/ask")
def ask(body: Question) -> dict[str, Any]:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="질문을 적어 주세요.")
    try:
        return ask_meetings.ask(question)
    except Exception as e:
        logger.exception("회의에 물어보기 실패")  # 원인은 서버 로그에만 (응답에 내부 정보를 넣지 않음)
        raise HTTPException(status_code=502, detail="답을 만들지 못했어요. 잠시 뒤 다시 물어봐 주세요.") from e
