"""로그인한 사람의 user_id → PostgreSQL 어댑터의 actor_id.

어댑터 함수(adapters/postgres)는 actor_id 로 권한을 검사해요. 그래서 actor_id 는 꼭 로그인 토큰에서 꺼낸
이 값만 넘기고, 요청 본문 · 주소에 들어온 사람 ID 를 넘기면 안 돼요 (다른 사람 행세 방지).
"""

from fastapi import Depends

from followup.entrypoints.api.routes.auth import current_user


def actor(user: dict = Depends(current_user)) -> str:
    return user["user_id"]