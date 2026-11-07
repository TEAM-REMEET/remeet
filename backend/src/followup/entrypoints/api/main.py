"""FastAPI 진입점.

실행 (backend 폴더에서):
    PYTHONPATH=src uvicorn followup.entrypoints.api.main:app --reload

nginx가 /api/ 경로를 그대로 넘기므로 모든 라우트는 /api 아래에 둔다.
API 문서: /api/docs
라우터는 routes/ 아래에 파일 하나씩 두고, 여기에는 include_router 한 줄만 추가한다.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from followup import settings
from followup.adapters.neo4j.driver import close_driver
from followup.entrypoints.api.ask_routes import router as ask_router
from followup.entrypoints.api.export_routes import router as export_router
from followup.entrypoints.api.recommend_routes import router as recommend_router
from followup.entrypoints.api.routes import auth, people
from followup.entrypoints.api.summary_routes import router as summary_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 설정이 빠졌으면 시작하지 않는다 → 컨테이너가 healthy가 안 되고 로그에 이유가 남음 (로그인 때 500보다 찾기 쉬움)
    if settings.missing():
        raise RuntimeError(f"필수 환경변수가 비어 있습니다: {', '.join(settings.missing())} (deploy/.env 확인)")
    yield
    close_driver()

app = FastAPI(
    title="RE:MEET API",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(people.router)
app.include_router(summary_router)
app.include_router(recommend_router)
app.include_router(ask_router)
app.include_router(export_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    """배포·헬스체크용. 서버가 떠 있으면 ok."""
    return {"status": "ok"}
