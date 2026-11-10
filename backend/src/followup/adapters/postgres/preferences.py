"""사람마다 저장하는 화면 설정 (migrations/026) — 사람 고르기 · 조직 선택의 즐겨찾기 · 최근.

    get_preferences(actor_id)            → {"people_fav": ["u2", …], "team_recent": ["서비스기획팀", …], …} (저장된 키만)
    set_preference(actor_id, key, value) → 저장한 목록 (중복 제거 · 순서 유지)
actor_id 는 로그인 토큰에서 꺼낸 값만 (본인 것만 읽고 씀). 관리자도 씀 (구성원 관리의 팀 즐겨찾기).
"""

from __future__ import annotations

from psycopg.types.json import Jsonb

from followup.adapters.postgres.db import transaction

from .common import ValidationError, account

KEYS = {"people_fav": 200, "people_recent": 8, "team_fav": 200, "team_recent": 5}  # 키 → 최대 개수
MAX_ITEM = 100  # 한 항목(user_id · 팀 이름) 최대 글자 수


def get_preferences(*, actor_id: str) -> dict[str, list[str]]:
    with transaction(actor_id) as cur:
        account(cur, actor_id)
        cur.execute("SELECT key, value FROM user_preferences WHERE user_id = %s", (actor_id,))
        return {r["key"]: r["value"] for r in cur.fetchall() if r["key"] in KEYS}


def set_preference(*, actor_id: str, key: str, value: list[str]) -> list[str]:
    if key not in KEYS:
        raise ValidationError(f"저장할 수 없는 설정이에요: {key}")
    if not isinstance(value, list) or not all(isinstance(v, str) and 0 < len(v) <= MAX_ITEM for v in value):
        raise ValidationError(f"설정 값은 {MAX_ITEM}자 이하 글자 목록이어야 해요.")
    items = list(dict.fromkeys(value))
    if len(items) > KEYS[key]:
        raise ValidationError(f"{key} 는 최대 {KEYS[key]}개까지 저장할 수 있어요.")
    with transaction(actor_id) as cur:
        account(cur, actor_id)
        cur.execute("""INSERT INTO user_preferences (user_id, key, value) VALUES (%s, %s, %s)
                       ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                    (actor_id, key, Jsonb(items)))
    return items
