"""대시보드 · 내 Jira 할 일

GET  /api/dashboard/jira[?refresh=true]       내 미완료 이슈 + 최근 완료된 이슈 (읽기)
응답 = {"linked": true, "all_url": "https://…/issues/?jql=…", "synced_at": "2026-11-08T15:03:00+09:00",
        "issues": [{"key": "KAN-12", "title": "…", "status": "진행 중", "category": "진행 중", "due": "2026-11-10",
                    "url": "https://…/browse/KAN-12"}],
        "done": [같은 모양 · 최근 30일 안에 완료된 일],
        "statuses": [{"name": "진행 중", "category": "indeterminate"}, …]}   (이슈마다 category_key = new | indeterminate | done → 색)
    linked=false: 이메일로 Jira 계정을 찾지 못함 (그래도 RE:MEET 에서 내가 담당자인 일은 보여 줌)

POST /api/dashboard/jira/{key}/status  {"status": "진행 중"}    상태 드롭다운 (GET 의 statuses 중 하나)
응답 = {"key": "KAN-12", "status": "완료", "category": "done"}
    내 일인지(대시보드와 같은 범위) 서버가 먼저 확인 · Jira 이력엔 관리자 계정이 남으니 누른 사람을 댓글로 남김 (응답 뒤에)

오류: 401 로그인 안 함 · 403 내 일이 아님 · 409 그 상태로 바꿀 수 없음 · 502 Jira 연결 실패 · 503 Jira 연동 설정 없음
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.application import dashboard_jira
from followup.application.export_jira import JiraNotConfigured, JiraUnavailable
from followup.entrypoints.api.routes.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
_NOT_CONFIGURED = "Jira 연동 설정이 없어요. 관리자에게 문의해 주세요."


@router.get("/jira")
def my_jira(refresh: bool = False, user: dict = Depends(current_user)) -> dict[str, Any]:
    """refresh=true: 「동기화」 (캐시를 건너뛰고 Jira 에서 다시)."""
    try:
        return dashboard_jira.my_issues(user["user_id"], refresh=refresh)  # 범위 = 로그인한 사람 (요청으로 바꿀 수 없음)
    except JiraNotConfigured as e:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED) from e
    except JiraUnavailable as e:
        logger.exception("대시보드 Jira 조회 실패")  # 원인은 서버 로그에만
        raise HTTPException(status_code=502, detail="Jira 에서 할 일을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.") from e


class Status(BaseModel):
    status: str = Field(min_length=1, max_length=60)  # 상태 이름 (GET 의 statuses 중 하나)


@router.post("/jira/{key}/status")
def set_status(key: str, body: Status, tasks: BackgroundTasks, user: dict = Depends(current_user)) -> dict[str, str]:
    """상태 드롭다운 · 그 이름의 상태로 (내 일인지 확인). 누른 사람 댓글은 응답을 보낸 뒤에 (기다리지 않게)."""
    out = _change(lambda name: dashboard_jira.set_status(user["user_id"], name, key, body.status), user)
    tasks.add_task(dashboard_jira.comment_change, user.get("name") or user["user_id"], out["key"], out["status"])
    return out


def _change(run, user: dict) -> dict[str, str]:
    try:
        return run(user.get("name") or user["user_id"])
    except dashboard_jira.NotMine as e:
        raise HTTPException(status_code=403, detail="내 할 일만 바꿀 수 있어요.") from e
    except dashboard_jira.NoTransition as e:
        raise HTTPException(status_code=409, detail="Jira 에서 이 이슈를 그 상태로 바꿀 수 없어요. Jira 에서 직접 바꿔 주세요.") from e
    except JiraNotConfigured as e:
        raise HTTPException(status_code=503, detail=_NOT_CONFIGURED) from e
    except JiraUnavailable as e:
        logger.exception("대시보드 Jira 상태 변경 실패")
        raise HTTPException(status_code=502, detail="Jira 상태를 바꾸지 못했어요. 잠시 뒤 다시 시도해 주세요.") from e
