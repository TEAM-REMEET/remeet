"""OpenAI 호출 공통 모듈

- .env의 OPENAI_API_KEY / OPENAI_MODEL 사용 (키 값은 출력하지 않음, 모델은 config.ALLOWED_MODELS로 제한)
- 호출 설정(추론 강도 · 재시도 · 시간 제한 · 캐시)은 config.py
- Pydantic 스키마로 structured output 호출 → 파싱된 모델 반환
- 응답 캐시: 같은 모델 · 프롬프트 · 스키마면 저장된 결과를 재사용 (뒤 단계 수정 시 앞 단계 재호출 방지)
  캐시에는 파싱된 출력만 저장 (토큰 사용량 · 비용 정보 저장 안 함)
"""

import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

import config as cfg
from tracing import traceable, wrap_client

# ── 환경 변수 (.env) — 호출 설정은 config.py ──
ENV_PATH = cfg.ENV_PATH
load_dotenv(ENV_PATH)
OPENAI_MODEL = os.getenv("OPENAI_MODEL")

_client = None
# ────────────────────────────────────────────────────


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(f"OPENAI_API_KEY가 설정되지 않음 (.env 위치: {ENV_PATH})")
        cfg.check_model(OPENAI_MODEL)      # 허용 모델(config.ALLOWED_MODELS)이 아니면 호출 전에 중단
        # 키는 환경 변수에서 읽음 / LangSmith 기록이 켜져 있으면 호출 내용 기록
        _client = wrap_client(OpenAI(max_retries=cfg.LLM_MAX_RETRIES, timeout=cfg.LLM_TIMEOUT))
    return _client


def cache_path(system: str, user: str, schema: type[BaseModel], tag: str, effort: str | None) -> Path:
    # 모델 · 추론 강도 · 프롬프트 · 스키마가 모두 같을 때만 같은 캐시
    payload = json.dumps([OPENAI_MODEL, effort, system, user, schema.__name__, schema.model_json_schema()],
                         ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return cfg.LLM_CACHE_DIR / f"{tag + '_' if tag else ''}{key}.json"


@traceable(name="call_structured")     # 캐시를 쓴 호출은 하위에 OpenAI 호출 기록이 없음
def call_structured(system: str, user: str, schema: type[BaseModel], tag: str = "",
                    effort: str | None = None) -> BaseModel:
    # tag: 캐시 파일 이름 앞에 붙는 식별자 (ex. TS3003c_segment_summary_s3)
    # effort: 이 호출의 추론 강도 (None이면 config.LLM_REASONING_EFFORT 사용)
    effort = effort or cfg.LLM_REASONING_EFFORT
    path = cache_path(system, user, schema, tag, effort)
    if cfg.USE_LLM_CACHE and path.is_file():
        return schema.model_validate_json(path.read_text(encoding="utf-8"))

    kwargs = {} if cfg.LLM_TEMPERATURE is None else {"temperature": cfg.LLM_TEMPERATURE}
    if effort is not None:
        kwargs["reasoning"] = {"effort": effort}
    # OpenAI Responses API를 사용하여 structured output 호출 (Pydantic 객체 바로 받음)
    response = get_client().responses.parse(model=OPENAI_MODEL, instructions=system, input=user,
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
