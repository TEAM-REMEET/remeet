"""FastAPI 진입점.

실행 (backend 폴더에서):
    PYTHONPATH=src uvicorn followup.entrypoints.api.main:app --reload

nginx가 /api/ 경로를 그대로 넘기므로 모든 라우트는 /api 아래에 둔다.
API 문서: /api/docs
"""

from fastapi import FastAPI

app = FastAPI(
    title="RE:MEET API",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.get("/api/health")
def health() -> dict[str, str]:
    """배포·헬스체크용. 서버가 떠 있으면 ok."""
    return {"status": "ok"}
