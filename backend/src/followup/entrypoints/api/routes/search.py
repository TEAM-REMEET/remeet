"""회의 검색 (PostgreSQL · pg_trgm). 범위 = 내 프로젝트의 회의 중 내가 볼 수 있는 것.

GET /api/search?q=수수료   탭별 결과 한 번에 → {counts, title, folder, people, content}
    title   회의명 · folder  프로젝트 이름 · people  이름이 맞는 구성원이 주관 · 참석한 회의와 그 사람 발화
    content 요약 · 결정 · 할 일 · 원문 발화 (원문은 볼 권한이 있을 때만)
"""

from fastapi import APIRouter, Depends, Query

from followup.adapters.postgres import search as pg_search
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=100), me: str = Depends(actor)):
    return pg_search.search_all(actor_id=me, q=q)