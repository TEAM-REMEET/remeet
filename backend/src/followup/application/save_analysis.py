"""분석(LLM)이 끝나면 서버가 바로 PostgreSQL 에 저장 · 주관자에게 넘김 (application/meeting_jobs._run 이 부름).

예전에는 작성자 브라우저가 「분석 끝남」을 확인한 뒤 저장 · 넘기기를 요청해서, 브라우저를 닫으면 주관자에게 안 넘어갔어요.
    내가 주관자      작성 중(draft) 회의록으로 저장 (새 프로젝트는 아직 안 만듦 → 「확정하기」 때 web 이 만듦)
    주관자가 다른 사람  새 프로젝트면 만들고 → draft 저장 → 주관자에게 넘김 (주관자는 넘겨받은 입력값 · 요약 JSON 으로 2단계를 다시 열어요)
요약 화면 내용(content)은 web 이 「확정하기」 때 채워요 (요약 JSON → 화면 모양 변환은 web state/summary_view.py 가 해요).
"""

from __future__ import annotations

from datetime import datetime

from followup.adapters.postgres import compose, folders
from followup.adapters.postgres.common import NotFoundError, PermissionDenied
from followup.analysis.summary.transcript_builder import MeetingSubmission

PROJECT_GONE = "선택한 프로젝트가 삭제됐어요. 프로젝트를 다시 골라 주세요."


class ProjectGone(Exception):
    """분석하는 사이에 고른 프로젝트가 지워짐 (작성자가 프로젝트를 다시 골라 분석해야 함)"""


def _exists(user_id: str, project_id: str) -> bool:
    try:
        folders.get_folder(actor_id=user_id, folder_id=project_id)
    except (NotFoundError, PermissionDenied):
        return False
    return True


def save_result(user_id: str, submission: MeetingSubmission, summary: dict, draft: dict) -> str:
    """'draft' (내가 주관자) | 'handoff' (넘김). 고른 프로젝트가 지워졌으면 ProjectGone."""
    project_id = submission.project_id
    if project_id and not _exists(user_id, project_id):
        raise ProjectGone(project_id)
    handoff = submission.host_user_id != user_id
    if handoff and not project_id:  # 새 프로젝트 → 요약이 정한 id 로 만듦 (주관자는 handoff_meeting 이 멤버로 넣음)
        project_id = summary["project_id"]
        if not _exists(user_id, project_id):
            folders.create_folder(actor_id=user_id, name=(draft.get("nn_name") or "").strip() or "새 프로젝트",
                                  folder_id=project_id,
                                  members=[a.user_id for a in submission.attendees if a.user_id != user_id])
    compose.save_draft(actor_id=user_id, content={}, meeting_id=summary["meeting_id"], title=submission.title,
                       directory_id=submission.directory_id, project_id=project_id, host_id=submission.host_user_id,
                       held_at=datetime.fromisoformat(f"{submission.date.isoformat()}T00:00:00+09:00"),
                       attendees=[a.user_id for a in submission.attendees], summary_json=summary)
    if not handoff:
        return "draft"
    compose.handoff_meeting(actor_id=user_id, meeting_id=summary["meeting_id"],
                            request=submission.model_dump(mode="json", exclude_none=True), draft=draft)
    return "handoff"
