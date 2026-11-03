"""LangSmith 연동 (선택)

- .env에 LANGSMITH_API_KEY가 있으면 trace 기록, 없으면 기록 없이 기존과 동일하게 동작
- LANGSMITH_TRACING=false로 두면 키가 있어도 기록 끔
- langsmith 패키지가 없어도 동작 (traceable · wrap_client가 원래 함수 · 클라이언트를 그대로 반환)

사용
    from tracing import traceable, wrap_client
    @traceable(name="segment_summary")      # 함수 입력 · 출력 기록
    client = wrap_client(OpenAI())           # OpenAI 호출(프롬프트 · 응답 · 토큰) 기록
"""

import os

from dotenv import load_dotenv

import config as cfg

# ── 환경 변수 (.env) — 프로젝트 이름 기본값은 config.LANGSMITH_PROJECT ──
ENV_PATH = cfg.ENV_PATH
load_dotenv(ENV_PATH)
# ────────────────────────────────────────────────────

try:
    from langsmith import traceable as _traceable
    from langsmith.wrappers import wrap_openai as _wrap_openai
except ImportError:
    _traceable = _wrap_openai = None

TRACING_ENABLED = (_traceable is not None
                   and bool(os.getenv("LANGSMITH_API_KEY"))
                   and os.getenv("LANGSMITH_TRACING", "true").strip().lower() != "false")

if TRACING_ENABLED:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGSMITH_PROJECT", cfg.LANGSMITH_PROJECT)
else:
    os.environ["LANGSMITH_TRACING"] = "false"   # 키 없이 기록을 시도하며 경고가 나는 것 방지 (LangGraph 포함)


def traceable(name: str | None = None, **kwargs):
    # 기록이 켜져 있으면 langsmith.traceable 적용, 꺼져 있으면 함수를 그대로 반환
    def decorator(fn):
        return _traceable(name=name or fn.__name__, **kwargs)(fn) if TRACING_ENABLED else fn
    return decorator


def wrap_client(client):
    return _wrap_openai(client) if TRACING_ENABLED else client


def tracing_status() -> str:
    if TRACING_ENABLED:
        return f"LangSmith 기록 켜짐 (프로젝트: {os.environ['LANGSMITH_PROJECT']})"
    if _traceable is None:
        return "LangSmith 기록 꺼짐 (langsmith 패키지 없음)"
    if not os.getenv("LANGSMITH_API_KEY"):
        return "LangSmith 기록 꺼짐 (.env에 LANGSMITH_API_KEY 없음)"
    return "LangSmith 기록 꺼짐 (LANGSMITH_TRACING=false)"
