"""summary.json · 단계별 LLM 응답 로컬 파일 저장 (GCS 등으로 바꿀 때는 같은 메서드를 가진 구현체로 교체)

    {root}/{회의 ID}/{회의 ID}_summary.json     최종 결과
    {root}/{회의 ID}/steps/{단계}_{해시}.json   단계별 LLM 응답 (중간 실패 후 재실행용, 완료되면 삭제)
"""

import shutil
import uuid
from pathlib import Path

from followup.analysis.summary.schemas import MeetingSummary


def _write_atomic(path: Path, content: str) -> None:
    # 쓰는 도중 실패해도 기존 파일이 손상되지 않고, 동시 실행 시 임시 파일이 겹치지 않음
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class LocalFileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _steps_dir(self, meeting_id: str) -> Path:
        return self.root / meeting_id / "steps"

    def save_summary(self, summary: MeetingSummary) -> str:
        path = self.root / summary.meeting_id / f"{summary.meeting_id}_summary.json"
        _write_atomic(path, summary.model_dump_json(indent=2))
        return str(path)

    def read_step(self, meeting_id: str, key: str) -> str | None:
        path = self._steps_dir(meeting_id) / f"{key}.json"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def write_step(self, meeting_id: str, key: str, content: str) -> None:
        _write_atomic(self._steps_dir(meeting_id) / f"{key}.json", content)

    def clear_steps(self, meeting_id: str) -> None:
        steps = self._steps_dir(meeting_id)
        shutil.rmtree(steps, ignore_errors=True)
        try:
            steps.parent.rmdir()        # 요약 파일이 없는 빈 폴더만 지워짐
        except OSError:
            pass
