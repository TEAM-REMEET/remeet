"""LangSmith 연동 (선택)

- .env에 LANGSMITH_API_KEY가 있고 config.USE_LANGSMITH가 True이면 trace 기록, 아니면 기록 없이 동일하게 동작
- .env의 LANGSMITH_TRACING=false로 두면 키가 있어도 기록 끔
- langsmith 패키지가 없어도 동작 (traceable · wrap_client가 원래 함수 · 클라이언트를 그대로 반환)

기록되는 것 (LangSmith 프로젝트: .env LANGSMITH_PROJECT, 없으면 config.LANGSMITH_PROJECT)
    summary_action_{회의 ID}    LangGraph 실행 1회 (graph.py) — 노드별 입력 · 출력 상태
      ├ load_input              전사문 txt → 메타데이터 · 발화
      ├ generate                └ call_structured → OpenAI 호출 (프롬프트 · 응답 · 토큰 — wrap_openai)
      ├ postprocess             └ build_summary (근거 검사 · ID 부여)
      ├ verify (1~3회)          └ call_structured → OpenAI 호출
      ├ revise (0~2회)          └ call_structured → OpenAI 호출
      ├ mark_review             (검증 미통과 시)
      ├ finalize
      └ save
"""

import os

from dotenv import load_dotenv

import config as cfg

load_dotenv(cfg.ENV_PATH)

try:
    from langsmith import traceable as _traceable
    from langsmith.wrappers import wrap_openai as _wrap_openai
except ImportError:
    _traceable = _wrap_openai = None

TRACING_ENABLED = (cfg.USE_LANGSMITH and _traceable is not None
                   and bool(os.getenv("LANGSMITH_API_KEY"))
                   and os.getenv("LANGSMITH_TRACING", "true").strip().lower() != "false")

if TRACING_ENABLED:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGSMITH_PROJECT", cfg.LANGSMITH_PROJECT)
else:
    os.environ["LANGSMITH_TRACING"] = "false"   # 키 없이 기록을 시도하며 경고가 나는 것 방지


def traceable(**kwargs):
    # @traceable(name="...") — 기록이 꺼져 있으면 원래 함수 그대로
    if not TRACING_ENABLED:
        return lambda fn: fn
    kwargs.setdefault("tags", list(cfg.LANGSMITH_TAGS))
    return _traceable(**kwargs)


def run_extra(run_name: str, metadata: dict | None = None) -> dict:
    # traced 함수 호출 시 넘길 kwargs (trace 이름 · 메타데이터) — 기록이 꺼져 있으면 빈 dict
    if not TRACING_ENABLED:
        return {}
    return {"langsmith_extra": {"name": run_name, "metadata": metadata or {}}}


def wrap_client(client):
    return _wrap_openai(client) if TRACING_ENABLED else client


def status() -> str:
    if TRACING_ENABLED:
        return f"LangSmith 기록: 켜짐 (프로젝트 {os.getenv('LANGSMITH_PROJECT')})"
    if not cfg.USE_LANGSMITH:
        return "LangSmith 기록: 꺼짐 (config.USE_LANGSMITH=False)"
    if _traceable is None:
        return "LangSmith 기록: 꺼짐 (langsmith 패키지 없음)"
    if not os.getenv("LANGSMITH_API_KEY"):
        return "LangSmith 기록: 꺼짐 (.env에 LANGSMITH_API_KEY 없음)"
    return "LangSmith 기록: 꺼짐 (LANGSMITH_TRACING=false)"
