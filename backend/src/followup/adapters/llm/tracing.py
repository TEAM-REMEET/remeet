"""LangSmith 연동 (선택)

- LANGSMITH_TRACING=true이고 LANGSMITH_API_KEY가 있을 때만 기록 (실명 · 직무 · 발화가 외부로 전송되므로 기본은 꺼짐)
- langsmith 패키지가 없어도 동작
- 기록되면 LangGraph 실행 1회 = trace 1개 (노드 · OpenAI 호출이 하위 run)
"""

import os

from followup import settings as cfg

try:
    from langsmith.wrappers import wrap_openai as _wrap_openai
except ImportError:
    _wrap_openai = None


def enabled() -> bool:
    return cfg.USE_LANGSMITH and _wrap_openai is not None and cfg.langsmith_requested()


def configure() -> None:
    # 환경변수를 읽은 뒤, LLM 호출 전에 1회 실행
    if enabled():
        os.environ.setdefault("LANGSMITH_PROJECT", cfg.LANGSMITH_PROJECT)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"   # 키 없이 기록을 시도하며 경고가 나는 것 방지


def wrap_client(client):
    return _wrap_openai(client) if enabled() else client
