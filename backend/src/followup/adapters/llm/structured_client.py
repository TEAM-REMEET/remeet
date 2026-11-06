"""OpenAI Responses API structured output 호출 — 키는 환경변수 OPENAI_API_KEY (값은 출력하지 않음)

호출 상한 · 응답 재사용은 application/summarize_meeting.py가 담당하고, 이 클래스는 API 호출 1회만 담당.
"""

from openai import OpenAI
from pydantic import BaseModel

from followup import settings as cfg
from followup.adapters.llm.tracing import wrap_client


class OpenAIStructuredClient:
    def __init__(self, client=None):
        self._client = client       # 테스트에서 가짜 클라이언트 주입

    def _get_client(self):
        if self._client is None:
            if not cfg.openai_key_set():
                raise RuntimeError("OPENAI_API_KEY가 설정되지 않음")
            self._client = wrap_client(OpenAI(max_retries=cfg.LLM_MAX_RETRIES, timeout=cfg.LLM_TIMEOUT))
        return self._client

    def parse(self, system: str, user: str, schema: type[BaseModel], effort: str | None) -> BaseModel:
        kwargs = {} if cfg.LLM_TEMPERATURE is None else {"temperature": cfg.LLM_TEMPERATURE}
        if effort is not None:
            kwargs["reasoning"] = {"effort": effort}
        response = self._get_client().responses.parse(model=cfg.model_name(), instructions=system, input=user,
                                                      text_format=schema, **kwargs)
        if response.output_parsed is None:
            raise RuntimeError(f"구조화 출력 파싱 실패 ({schema.__name__}) — 응답 거부 또는 형식 오류")
        return response.output_parsed
