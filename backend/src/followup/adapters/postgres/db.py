"""PostgreSQL 연결.

접속 주소는 settings.DATABASE_URL (환경 변수 DATABASE_URL, 없으면 로컬 docker-compose 기본값).
    postgresql://사용자:비밀번호@호스트:포트/DB이름
Neo4j 의 adapters/neo4j/driver.py 와 같은 방식: 풀은 프로세스당 하나, 서버가 끝날 때 close_pool().

연결을 매번 새로 만들면 느리니까 커넥션 풀(미리 열어 둔 연결 몇 개를 돌려 쓰기)을 씁니다.

사용 예
    from followup.adapters.postgres.db import query, execute

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
import re
from contextlib import contextmanager
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row
import logging

from psycopg_pool import ConnectionPool, PoolTimeout

from followup import settings

logging.getLogger("psycopg.pool").setLevel(logging.CRITICAL)  # 접속 재시도 로그가 화면을 덮지 않게

def database_url() -> str:
    # 환경 변수를 먼저 봄 (테스트가 실행 중에 TEST_DATABASE_URL 로 바꿔 끼움)
    return os.environ.get("DATABASE_URL") or settings.DATABASE_URL


class DatabaseUnavailable(RuntimeError):
    """DB 에 접속하지 못함. API 는 503 으로 바꿔 돌려줌 (entrypoints/api/errors.py)."""


def _masked(url: str) -> str:
    return re.sub(r"://([^:@/]+):[^@]*@", r"://\1:***@", url)  # 비밀번호는 가림 (로그 · 화면)


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
        try:  # 진짜 이유(비밀번호 틀림 · 연결 수 초과 · DB 재시작 중 …)를 같이 보여 줌
            psycopg.connect(database_url(), connect_timeout=5).close()
            reason = "5초 안에 연결이 안 됐어요 (DB 가 느리거나 막 재시작한 중일 수 있어요)"
        except psycopg.Error as e:
            reason = str(e).strip()
        raise DatabaseUnavailable(
            f"❌ PostgreSQL 에 접속하지 못했어요: {_masked(database_url())}\n"
            f"   이유: {reason}\n"
            "   → DB 가 켜져 있나요?  backend 폴더에서 docker compose -f docker-compose.postgres.yml up -d\n"
            "   → .env 의 DATABASE_URL 주소 · 비밀번호가 맞나요?"
        ) from None
    return p


def close_pool() -> None:
    """서버가 끝날 때 (main.py lifespan). 테스트에서 DB 주소를 바꿀 때도 씀."""
    if pool.cache_info().currsize:
        pool().close()
        pool.cache_clear()


@contextmanager
def transaction(actor_id: str | None = None):
    """여러 SQL 을 '전부 성공 아니면 전부 취소' 로 묶을 때.

        with transaction() as cur:
            cur.execute("INSERT ...")
            cur.execute("UPDATE ...")
        # with 블록이 에러 없이 끝나면 COMMIT, 에러가 나면 자동 ROLLBACK

    actor_id 를 주면 이 트랜잭션 동안만 remeet.user_id 를 설정합니다 (migrations/002_rls.sql 의 RLS 가 읽는 값).
    remeet_app 처럼 RLS 가 걸린 역할로 접속할 때 필요하고, 테이블 소유자로 접속하면 무시돼요.
    ⚠️ actor_id 는 서버가 로그인 세션에서 확인한 값만 넘기세요.
    """
    with pool().connection() as conn:
        with conn.cursor() as cur:
            if actor_id:
                cur.execute("SELECT set_config('remeet.user_id', %s, true)", (actor_id,))
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