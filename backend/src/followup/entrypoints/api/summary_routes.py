"""회의 등록 · 요약 · 액션 아이템 추출 API (채원 담당)"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["summary"])
