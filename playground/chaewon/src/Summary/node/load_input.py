"""[0] 입력: 주제 경계 탐지 결과 JSON → 초기 State"""

from pathlib import Path

from schemas import AgendaRegistry, BoundaryResult, SummaryState


def load_input(boundary_json_path: str | Path) -> SummaryState:
    result = BoundaryResult.model_validate_json(Path(boundary_json_path).read_text(encoding="utf-8"))
    return {
        "meeting_id": result.meeting_id,
        "metadata": result.metadata,
        "segments": result.segments,
        "utterance_texts": {u.utterance_index: u.text for s in result.segments for u in s.all_utterances},
        "registry": AgendaRegistry(meeting_id=result.meeting_id),
        "segment_summaries": [],
    }
