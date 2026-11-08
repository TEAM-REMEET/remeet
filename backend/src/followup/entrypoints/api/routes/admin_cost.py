"""관리자 「비용 관리」 (PostgreSQL llm_usage · app_settings). 관리자 계정만.

GET /api/admin/cost     최근 settings.USAGE_MONTHS개월의 달마다 비용(달러) · 회의록 수 · 기능별 비용 + 예산 + 환율
PUT /api/admin/budget   월 예산(원) · 알림 기준(%) 저장

비용은 달러로 저장 · 전달하고, 화면이 환율로 원을 계산해 「₩12,345 ($8.82)」처럼 보여 줌.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from followup import settings
from followup.adapters import exchange_rate
from followup.adapters.postgres import usage
from followup.entrypoints.api.routes.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


class Budget(BaseModel):
    amount_krw: int = Field(gt=0)
    alert_pct: int = Field(ge=1, le=100)


@router.get("/cost")
def cost(_: dict = Depends(require_admin)) -> dict:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    return {"today": today.isoformat(),
            "months": usage.monthly_usage(today, settings.USAGE_MONTHS),
            "budget": usage.budget(),
            "rate": exchange_rate.usd_krw(usage.fallback_rate)}


@router.put("/budget")
def save_budget(body: Budget, user: dict = Depends(require_admin)) -> dict:
    return usage.save_budget(actor_id=user["user_id"], amount_krw=body.amount_krw, alert_pct=body.alert_pct)
