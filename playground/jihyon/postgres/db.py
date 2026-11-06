"""PostgreSQL 연결.

접속 주소는 환경 변수 DATABASE_URL 로 받습니다. (없으면 로컬 docker-compose 기본값)
    postgresql://사용자:비밀번호@호스트:포트/DB이름

연결을 매번 새로 만들면 느리니까 커넥션 풀(미리 열어 둔 연결 몇 개를 돌려 쓰기)을 씁니다.

사용 예
    from db import query, execute

    rows = query("SELECT id, name FROM users WHERE team_id = %s", ("fin",))
    for r in rows:
        print(r["id"], r["name"])          # 행은 dict 로 나옴

    execute("UPDATE todos SET is_done = %s WHERE id = %s", (True, 3))

⚠️ SQL 에 값을 넣을 때는 f-string 으로 붙이지 말고 꼭 %s 자리표시자 + 튜플로 넘기세요.
   (f-string 으로 붙이면 SQL 인젝션 공격에 뚫립니다)
"""

from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from functools import lru_cache

from psycopg.rows import dict_row
import logging

from psycopg_pool import ConnectionPool, PoolTimeout

logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)  # 접속 재시도 로그가 화면을 덮지 않게

DEFAULT_URL = "postgresql://remeet:remeet@localhost:5432/remeet"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@lru_cache(maxsize=1)
def pool() -> ConnectionPool:
    """앱 전체가 같이 쓰는 커넥션 풀 (처음 부를 때 한 번만 만듦)."""
    p = ConnectionPool(
        database_url(),
        min_size=1,
        max_size=5,
        kwargs={"row_factory": dict_row},  # 결과 행을 {"컬럼": 값} dict 로
        open=True,
    )
    atexit.register(p.close)  # 프로그램이 끝날 때 연결을 깔끔하게 닫음
    try:
        p.wait(timeout=5)  # 5초 안에 접속 못 하면 바로 알려 줌 (안 그러면 30초 기다리다 긴 에러)
    except PoolTimeout:
        p.close()
        raise SystemExit(
            f"❌ PostgreSQL 에 접속하지 못했어요: {database_url()}\n"
            "   → DB 가 켜져 있나요?  docker compose up -d  /  docker compose ps 로 healthy 확인\n"
            "   → .env 의 DATABASE_URL 주소 · 비밀번호가 맞나요?"
        ) from None
    return p


@contextmanager
def transaction():
    """여러 SQL 을 '전부 성공 아니면 전부 취소' 로 묶을 때.

        with transaction() as cur:
            cur.execute("INSERT ...")
            cur.execute("UPDATE ...")
        # with 블록이 에러 없이 끝나면 COMMIT, 에러가 나면 자동 ROLLBACK
    """
    with pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


def query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """SELECT 결과를 dict 목록으로."""
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple | dict | None = None) -> int:
    """INSERT / UPDATE / DELETE. 바뀐 행 수를 돌려줌."""
    with transaction() as cur:
        cur.execute(sql, params)
        return cur.rowcount
