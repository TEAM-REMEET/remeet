"""내 화면 설정 (PostgreSQL · migrations/026) — 사람 고르기 · 조직 선택의 즐겨찾기 · 최근.

GET /api/me/preferences          → {"people_fav": ["u2"], "people_recent": [...], "team_fav": [...], "team_recent": [...]}
                                   (저장한 적 없는 키는 빠짐)
PUT /api/me/preferences/{key}    {"value": ["u2", "u5"]} → 저장한 목록 (중복 제거) · 통째로 바꿈
    key = people_fav | people_recent | team_fav | team_recent · 그 밖의 키 · 너무 많은 항목은 400
본인 것만 (로그인 토큰의 user_id).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from followup.adapters.postgres import preferences
from followup.entrypoints.api.routes.actor import actor

router = APIRouter(prefix="/api/me", tags=["preferences"])


class Preference(BaseModel):
    value: list[str]


@router.get("/preferences")
def mine(me: str = Depends(actor)) -> dict[str, list[str]]:
    return preferences.get_preferences(actor_id=me)


@router.put("/preferences/{key}")
def save(key: str, body: Preference, me: str = Depends(actor)) -> list[str]:
    return preferences.set_preference(actor_id=me, key=key, value=body.value)
