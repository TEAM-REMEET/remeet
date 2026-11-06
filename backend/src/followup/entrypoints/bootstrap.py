"""외부 구현체 연결 (CLI · API · Worker가 함께 사용)"""

from followup import settings as cfg
from followup.adapters.llm import tracing
from followup.adapters.llm.structured_client import OpenAIStructuredClient
from followup.adapters.storage.local_files import LocalFileStore


def build_llm() -> OpenAIStructuredClient:
    cfg.load_env()
    tracing.configure()
    return OpenAIStructuredClient()


def build_store() -> LocalFileStore:
    cfg.load_env()
    return LocalFileStore(cfg.output_dir())
