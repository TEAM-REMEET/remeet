"""GET /api/dashboard/jira — 대시보드 · 내 Jira 할 일 (로그인한 사람이 담당자인 미완료 이슈, 읽기만)

응답 = {"linked": true, "all_url": "https://…/issues/?jql=…", "synced_at": "2026-11-08T15:03:00+09:00",
        "issues": [{"key": "KAN-12", "title": "…", "status": "진행 중", "category": "진행 중", "due": "2026-11-10",
                    "url": "https://…/browse/KAN-12"}]}
    linked=false: 이메일로 Jira 계정을 찾지 못함 (issues 는 빈 목록)
오류: 401 로그인 안 함 · 502 Jira 연결 실패 · 503 Jira 연동 설정 없음
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from followup.application import dashboard_jira
from followup.application.export_jira import JiraNotConfigured, JiraUnavailable
from followup.entrypoints.api.routes.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/jira")
def my_jira(refresh: bool = False, user: dict = Depends(current_user)) -> dict[str, Any]:
    """refresh=true: 「동기화」 (캐시를 건너뛰고 Jira 에서 다시)."""
    try:
        return dashboard_jira.my_issues(user["user_id"], refresh=refresh)  # 범위 = 로그인한 사람 (요청으로 바꿀 수 없음)
    except JiraNotConfigured as e:
        raise HTTPException(status_code=503, detail="Jira 연동 설정이 없어요. 관리자에게 문의해 주세요.") from e
    except JiraUnavailable as e:
        logger.exception("대시보드 Jira 조회 실패")  # 원인은 서버 로그에만
        raise HTTPException(status_code=502, detail="Jira 에서 할 일을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.") from e
