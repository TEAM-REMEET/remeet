"""PostgreSQL 어댑터의 예외 → HTTP 응답. main.py 에서 install(app) 한 번만 부름.

    NotFoundError        404  없는 회의 · 폴더 · 공유 …
    PermissionDenied     403  볼 수 없거나 고칠 수 없음 (주관자 · 작성자 · 검토자 · 참석자 · 공유받은 사람 규칙)
    ValidationError      400  입력값이 규칙에 안 맞음
    IntegrityError       409  DB 제약 위반 (중복 · 없는 사람 ID 등) — 메시지는 db_errors.user_message
    DatabaseUnavailable  503  DB 에 접속 못 함 (원인은 서버 로그에만)

각 라우트에서 try/except 를 반복하지 않으려고 한곳에 모았어요.
"""

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from followup.adapters.postgres.common import NotFoundError, PermissionDenied, ValidationError
from followup.adapters.postgres.db import DatabaseUnavailable
from followup.adapters.postgres.db_errors import user_message

logger = logging.getLogger(__name__)


def _json(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def install(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, e: NotFoundError):
        return _json(404, str(e))

    @app.exception_handler(PermissionDenied)
    async def denied(_: Request, e: PermissionDenied):
        return _json(403, str(e))

    @app.exception_handler(ValidationError)
    async def invalid(_: Request, e: ValidationError):
        return _json(400, str(e))

    @app.exception_handler(psycopg.errors.IntegrityError)
    async def integrity(_: Request, e: psycopg.errors.IntegrityError):
        logger.warning("PostgreSQL 제약 위반: %s", e)
        return _json(409, user_message(e))

    @app.exception_handler(DatabaseUnavailable)
    async def unavailable(_: Request, e: DatabaseUnavailable):
        logger.error("%s", e)
        return _json(503, "데이터베이스에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.")