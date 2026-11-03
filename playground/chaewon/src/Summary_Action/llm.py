"""OpenAI 호출 공통 모듈

- .env의 OPENAI_API_KEY · OPENAI_MODEL 사용 (키 값은 출력하지 않음), 호출 설정은 config.py
- Pydantic 스키마로 structured output 호출 → 파싱된 모델 반환
- 응답 캐시: 같은 모델 · 추론 강도 · 프롬프트 · 스키마면 저장된 결과 재사용
  캐시에는 파싱된 출력만 저장 (토큰 사용량 · 비용 정보 저장 안 함)
- LangSmith: tracing.py (기록이 켜져 있으면 OpenAI 호출의 프롬프트 · 응답이 기록됨)
"""

import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel

import config as cfg
from tracing import traceable, wrap_client

_client = None
_api_calls = 0      # 현재 회의에서 실제 API 호출 수 (run 시작 시 reset_call_count)


def reset_call_count() -> None:
    global _api_calls
    _api_calls = 0


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(f"OPENAI_API_KEY가 설정되지 않음 (.env 위치: {cfg.ENV_PATH})")
        _client = wrap_client(OpenAI(max_retries=cfg.LLM_MAX_RETRIES, timeout=cfg.LLM_TIMEOUT))
    return _client


def cache_path(system: str, user: str, schema: type[BaseModel], tag: str, effort: str | None) -> Path:
    payload = json.dumps([cfg.model_name(), effort, system, user, schema.__name__, schema.model_json_schema()],
                         ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return cfg.LLM_CACHE_DIR / f"{tag + '_' if tag else ''}{key}.json"


@traceable(name="call_structured")    # 캐시를 쓴 호출은 하위에 OpenAI 호출 기록이 없음
def call_structured(system: str, user: str, schema: type[BaseModel], tag: str = "",
                    effort: str | None = None) -> BaseModel:
    # tag: 캐시 파일 이름 앞에 붙는 식별자 (ex. TS3003c_summary_action)
    model = cfg.model_name()
    effort = effort or cfg.REASONING_EFFORT
    path = cache_path(system, user, schema, tag, effort)
    if cfg.USE_LLM_CACHE and path.is_file():
        print(f"[LLM] 캐시 사용: {path.name}")
        return schema.model_validate_json(path.read_text(encoding="utf-8"))

    global _api_calls
    if _api_calls >= cfg.MAX_LLM_CALLS_PER_MEETING:
        raise RuntimeError(f"LLM 호출 상한 초과 — 회의 하나당 최대 {cfg.MAX_LLM_CALLS_PER_MEETING}회 "
                           f"(config.MAX_LLM_CALLS_PER_MEETING), 호출하지 않고 중단")
    _api_calls += 1     # 실패해도 센 횟수로 둠 (같은 실행에서 다시 호출하지 않음)

    kwargs = {} if cfg.LLM_TEMPERATURE is None else {"temperature": cfg.LLM_TEMPERATURE}
    if effort is not None:
        kwargs["reasoning"] = {"effort": effort}
    print(f"[LLM] 호출: {model} (추론 강도 {effort})")
    response = get_client().responses.parse(model=model, instructions=system, input=user,
                                            text_format=schema, **kwargs)
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError(f"구조화 출력 파싱 실패 ({tag or schema.__name__}) — 응답 거부 또는 형식 오류")

    if cfg.USE_LLM_CACHE:
        cfg.LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(parsed.model_dump_json(indent=1), encoding="utf-8")
        tmp.replace(path)
    return parsed
