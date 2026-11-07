"""새 회의록 입력값 → POST /api/meetings 요청 본문."""

from __future__ import annotations

from data import repository as repo
from state import selectors as sel


def _attendees(user_ids: list[str]) -> list[dict]:
    rows = {r["user_id"]: r for r in repo.people_rows()}
    missing = [uid for uid in user_ids if not (rows.get(uid) or {}).get("job")]
    if missing:
        raise ValueError(f"구성원 정보(이름 · 직무)를 찾을 수 없는 사람이 있어요: {', '.join(missing)}")
    return [{"user_id": uid, "name": rows[uid]["name"], "job": rows[uid]["job"]} for uid in user_ids]


def build_request(s, transcript: str) -> dict:
    """화면 값 → 요청 본문. 새 폴더면 project_id를 빼고 보내고, 백엔드가 만든 ID를 응답으로 받아요. 보낼 수 없으면 ValueError."""
    body = {
        "directory_id": s.nn_type,
        "title": s.nn_title,
        "date": s.nn_date.isoformat(),
        "host_user_id": s.nn_host,
        "attendees": _attendees(list(dict.fromkeys([s.nn_host, *s.nn_people]))),
        "transcript_format": s.cp_example,
        "transcript": transcript,
    }
    if s.nn_group == "period":
        body["project_id"] = f"{sel.PERIOD_PREFIX}{s.nn_type}"
    elif s.nn_topic in sel.folder_ids_of_type(s, s.nn_type):
        body["project_id"] = s.nn_topic
    return body
