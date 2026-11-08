"""AI(LLM) 사용량 · 비용 기록 — 호출 1번 = PostgreSQL llm_usage 1줄 (관리자 「비용 관리」 화면)

    enable(sink)          API 서버가 시작할 때 저장 함수를 연결 (테스트 · CLI는 연결하지 않아 기록 안 함)
    scope(meeting_id, feature)
                          이 안에서 일어난 호출을 어느 회의 · 기능으로 기록할지 (요약 · 근거 연결의 _StepRunner)
    record_responses      OpenAI Responses API 응답 (structured_client)
    record_chat           Chat Completions 응답의 usage dict (openai_client.OpenAIClient.calls — 파일은 고치지 않고 밖에서 읽음)
    langchain_callback    LangChain ChatOpenAI에 붙이는 기록기 (담당자 추천 · 회의 질문)

기능(feature): summary(회의록 분석) · evidence(근거 연결 · 검증) · recommend(담당자 추천) · ask(회의 질문)
비용은 저장할 때의 단가(settings.LLM_PRICES_USD)로 계산. 단가가 없는 모델이면 비용을 비워 두고 경고.
기록이 실패해도 회의록 작업은 그대로 진행 (로그만 남기고 잠시 기록을 쉼). 결과 JSON에는 사용량 · 비용을 넣지 않음.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from followup import settings as cfg

logger = logging.getLogger(__name__)

FEATURES = ("summary", "evidence", "recommend", "ask")
PAUSE_AFTER_FAILURE = 60    # 저장이 실패하면 이 시간(초) 동안 기록을 쉼 (DB가 꺼졌을 때 호출마다 기다리지 않게)

Sink = Callable[..., None]  # sink(meeting_id=, feature=, input_tokens=, cached_tokens=, output_tokens=, cost_usd=)

_scope: contextvars.ContextVar[tuple[str | None, str | None]] = contextvars.ContextVar("llm_usage_scope",
                                                                                     default=(None, None))
_sink: Sink | None = None
_paused_until = 0.0
_warned_models: set[str] = set()
_lock = threading.Lock()


def enable(sink: Sink) -> None:
    global _sink, _paused_until
    _sink, _paused_until = sink, 0.0


def disable() -> None:
    global _sink
    _sink = None


@contextmanager
def scope(meeting_id: str | None, feature: str):
    token = _scope.set((meeting_id, feature))
    try:
        yield
    finally:
        _scope.reset(token)


def price(model: str | None) -> tuple[float, float, float] | None:
    # 모델 이름이 단가표 키로 시작하면 그 단가 (gpt-6-luna-2026-… → gpt-6-luna) · 여러 개면 가장 긴 키
    if not model:
        return None
    keys = [k for k in cfg.LLM_PRICES_USD if model.startswith(k)]
    return cfg.LLM_PRICES_USD[max(keys, key=len)] if keys else None


def cost_usd(model: str | None, input_tokens: int, cached_tokens: int, output_tokens: int) -> float | None:
    unit = price(model)
    if unit is None:
        return None
    p_in, p_cached, p_out = unit
    cached = min(cached_tokens, input_tokens)
    return ((input_tokens - cached) * p_in + cached * p_cached + output_tokens * p_out) / 1_000_000


def record(model: str | None, input_tokens: int, cached_tokens: int, output_tokens: int, *,
           meeting_id: str | None = None, feature: str | None = None) -> None:
    """호출 1번 기록. 실패해도 예외를 내지 않음."""
    global _paused_until
    scoped_meeting, scoped_feature = _scope.get()
    meeting_id, feature = meeting_id or scoped_meeting, feature or scoped_feature
    sink = _sink
    if sink is None or not cfg.RECORD_LLM_USAGE or feature not in FEATURES:
        return
    if time.monotonic() < _paused_until:
        return
    cost = cost_usd(model, input_tokens, cached_tokens, output_tokens)
    if cost is None:
        with _lock:
            first = model not in _warned_models
            _warned_models.add(str(model))
        if first:
            logger.warning("단가가 없는 모델이라 비용을 비워 둠: %s — settings.LLM_PRICES_USD에 추가해 주세요", model)
    try:
        sink(meeting_id=meeting_id, feature=feature, input_tokens=int(input_tokens), cached_tokens=int(cached_tokens),
             output_tokens=int(output_tokens), cost_usd=cost)
    except Exception as e:  # noqa: BLE001 — 기록 실패로 회의록 작업을 멈추지 않음
        _paused_until = time.monotonic() + PAUSE_AFTER_FAILURE
        logger.warning("사용량 기록 실패 (%s초 동안 기록을 쉼): %s", PAUSE_AFTER_FAILURE, type(e).__name__)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def record_responses(response: Any) -> None:
    # OpenAI Responses API: usage.input_tokens(캐시 포함) · input_tokens_details.cached_tokens · output_tokens(추론 포함)
    usage = getattr(response, "usage", None)
    if usage is None or not isinstance(getattr(usage, "input_tokens", None), int):
        return
    details = getattr(usage, "input_tokens_details", None)
    record(getattr(response, "model", None) or cfg.model_name(), usage.input_tokens,
           _int(getattr(details, "cached_tokens", 0)), _int(getattr(usage, "output_tokens", 0)))


def record_chat(model: str | None, usage: dict | None, *, meeting_id: str | None = None, feature: str | None = None) -> None:
    # Chat Completions: prompt_tokens(캐시 포함) · prompt_tokens_details.cached_tokens · completion_tokens(추론 포함)
    if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), int):
        return
    details = usage.get("prompt_tokens_details") or {}
    record(model, usage["prompt_tokens"], _int(details.get("cached_tokens")), _int(usage.get("completion_tokens")),
           meeting_id=meeting_id, feature=feature)


def langchain_callback(feature: str, meeting_id: str | None = None):
    """ChatOpenAI(callbacks=[...])에 붙이면 응답마다 기록 (usage_metadata: input_tokens · input_token_details.cache_read · output_tokens)."""
    from langchain_core.callbacks import BaseCallbackHandler   # 무거운 import는 실제로 쓸 때만

    class _UsageCallback(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs) -> None:
            for generations in response.generations:
                for generation in generations:
                    message = getattr(generation, "message", None)
                    meta = getattr(message, "usage_metadata", None)
                    if not meta:
                        continue
                    model = (getattr(message, "response_metadata", None) or {}).get("model_name") or cfg.model_name()
                    details = meta.get("input_token_details") or {}
                    record(model, _int(meta.get("input_tokens")), _int(details.get("cache_read")),
                           _int(meta.get("output_tokens")), meeting_id=meeting_id, feature=feature)

    return _UsageCallback()
