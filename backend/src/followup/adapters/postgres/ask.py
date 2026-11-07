"""회의에 물어보기 기록 (web views/ask.py · AppState.ask_history · migrations/010).

답 자체는 온보딩 에이전트(백엔드)가 만들어요. 여기는 「누가 · 언제 · 무엇을 물었고 · 어떤 답을 받았나」만 저장해서
새로고침 · 다시 로그인해도 기록이 남게 합니다. 본인 기록만 보고 지울 수 있어요.

    save_ask(question, answer)   answer = 에이전트 ask 결과 JSON 그대로 (answer_status · summary · sentences …)
    list_ask_history()           최근 것이 먼저 (web ask_history 순서)
    delete_ask(ask_id) · clear_ask_history()
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from followup.adapters.postgres.db import transaction
from .common import NotFoundError, ValidationError, require_user, required

MAX_HISTORY = 100


def save_ask(*, actor_id: str, question: str, answer: dict) -> dict:
    if not isinstance(answer, dict):
        raise ValidationError("답(answer)은 JSON 객체여야 합니다.")
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("""INSERT INTO ask_history (user_id, question, answer) VALUES (%s, %s, %s)
                       RETURNING id, question, answer, asked_at""",
                    (actor_id, required(question, "질문"), Jsonb(answer)))
        return cur.fetchone()


def list_ask_history(*, actor_id: str, limit: int = 30) -> list[dict]:
    if not 1 <= limit <= MAX_HISTORY:
        raise ValidationError(f"limit 은 1~{MAX_HISTORY} 입니다.")
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("""SELECT id, question, answer, asked_at FROM ask_history WHERE user_id = %s
                       ORDER BY asked_at DESC, id DESC LIMIT %s""", (actor_id, limit))
        return cur.fetchall()


def delete_ask(*, actor_id: str, ask_id: int) -> None:
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("DELETE FROM ask_history WHERE id = %s AND user_id = %s RETURNING id", (ask_id, actor_id))
        if cur.fetchone() is None:
            raise NotFoundError("기록을 찾을 수 없습니다.")


def clear_ask_history(*, actor_id: str) -> int:
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("DELETE FROM ask_history WHERE user_id = %s", (actor_id,))
        return cur.rowcount
