"""요약 · 액션 아이템 추출에 필요한 외부 경계 (구현체는 entrypoints/bootstrap.py에서 연결)"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from followup.analysis.summary.schemas import MeetingSummary

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredLLM(Protocol):
    def parse(self, system: str, user: str, schema: type[SchemaT], effort: str | None) -> SchemaT:
        """API를 1회 호출해 스키마로 파싱한 결과를 반환"""
        ...


class SummaryStore(Protocol):
    def save_summary(self, summary: MeetingSummary) -> str:
        """summary.json을 저장하고 위치를 반환"""
        ...

    def read_step(self, meeting_id: str, key: str) -> str | None:
        """저장된 단계별 LLM 응답 (없으면 None)"""
        ...

    def write_step(self, meeting_id: str, key: str, content: str) -> None:
        ...

    def clear_steps(self, meeting_id: str) -> None:
        """회의의 단계별 LLM 응답을 모두 삭제"""
        ...
