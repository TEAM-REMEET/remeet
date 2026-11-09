"""담당자 추천 · 확정 · 최종 회의록 API (은제 담당)

POST /api/meetings/{meeting_id}/recommendations   body = 검토가 끝난 MeetingSummary JSON 전체
    → Neo4j 적재 → 이번 회의 액션아이템 담당자 추천 → 액션아이템별 결과 (몇십 초 ~ 몇 분 걸림)
    같은 회의로 다시 보내도 된다 (적재는 건너뛰고, 추천은 실패했던 건만 다시).
PUT  /api/meetings/{meeting_id}/action-items      body = {"items": [{item_id, user_ids, due}]}  「다음: 최종 회의록」
    이 회의의 액션아이템 전부의 담당자 · 마감일을 한 번에 확정 (하나라도 빠지거나 틀리면 아무것도 저장 안 함)
GET  /api/meetings/{meeting_id}/minutes            최종 회의록 (회의 정보 · 안건 · 액션아이템과 확정 담당자)

오류 응답
    401 로그인하지 않음 / 404 없는 회의 / 400 빠진 액션아이템 · 없는 사람 / 422 입력 형식 오류 (담당자 없음 · 날짜 형식)
    502 적재 · 추천 실패 — 같은 요청을 다시 보내면 됨
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.adapters.neo4j import action_items
from followup.adapters.postgres import folders, meetings
from followup.analysis.summary import ui_test
from followup.analysis.summary.schemas import MeetingSummary
from followup.application.recommend_assignees import recommend_for_meeting
from followup.entrypoints.api.routes.actor import actor
from followup.entrypoints.api.routes.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recommend"], dependencies=[Depends(current_user)])


class Assignee(BaseModel):
    user_id: str
    name: str
    source: str | None      # meeting_explicit: 회의록에 적힘 / manual: 주관자가 지정


class Predecessor(BaseModel):
    """이 일과 이어지는 선행 업무를 맡았던 사람 (추천 후보 위에 따로 보여 줌)"""
    user_id: str
    name: str
    team: str | None
    role: str | None
    task: str               # 맡았던 선행 업무
    meeting: str | None     # 그 업무가 나온 회의 제목
    date: str | None        # YYYY-MM-DD


class Candidate(BaseModel):
    user_id: str
    name: str
    team: str | None
    role: str | None
    score: float            # 종합 점수 (0~100)
    skill_score: float
    role_team_score: float | None   # 목표 팀 · 역할이 없는 업무면 null
    workload_score: float
    attended: bool          # 이번 회의 참석자
    skills: list[str]       # 이 일에 필요한 스킬 중 가진 것
    open_tasks: int         # 진행 중 업무 수


class RecommendedItem(BaseModel):
    item_id: str
    text: str
    due: str | None                 # YYYY-MM-DD (확정 전에는 회의록 값 · 날짜가 아닐 수 있음)
    recommend_status: str | None    # null: 정상 / no_candidate: 후보 없음 / skill_unclear: 필요 스킬을 못 정함
    missing_skills: list[str] | None
    assignees: list[Assignee]       # 지금 담당자 (회의록에 적힘 · 주관자가 지정 · 확정)
    predecessors: list[Predecessor]
    candidates: list[Candidate]     # 점수 높은 순 (직급별 2명, 최대 6명) · 담당자를 고른 뒤에도 그대로


class Recommendations(BaseModel):
    meeting_id: str
    items: list[RecommendedItem]


class ConfirmItem(BaseModel):
    item_id: str
    user_ids: list[str] = Field(min_length=1)   # 최종 담당자 전부 (회의록 담당자를 그대로 두려면 그 사람도 넣음)
    due: date                                   # 마감일 (YYYY-MM-DD)


class Confirmation(BaseModel):
    items: list[ConfirmItem]                    # 이 회의의 액션아이템 전부


# 동기 함수: 오래 걸리는 동안 다른 요청을 막지 않게 (FastAPI가 스레드에서 실행)
@router.post("/meetings/{meeting_id}/recommendations", response_model=Recommendations)
def recommend(meeting_id: str, summary: MeetingSummary, me: str = Depends(actor)):
    if summary.meeting_id != meeting_id:
        raise HTTPException(status_code=422, detail=f"주소의 회의({meeting_id})와 본문의 meeting_id({summary.meeting_id})가 달라요.")
    if ui_test.is_test_summary(summary):    # 화면 확인용 시험 모드: LLM 호출 · Neo4j 적재 없이 시험용 후보
        return {"meeting_id": meeting_id, "items": ui_test.recommend_items(summary)}
    raw = summary.model_dump(mode="json")
    project = _project(me, summary.project_id)
    raw["project_name"] = project.get("name")
    raw["project_created_at"] = project["created_at"].isoformat() if project.get("created_at") else None
    row = _meeting(me, meeting_id)
    raw["meeting_created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
    raw["meeting_created_by"] = row.get("created_by")
    try:
        items = recommend_for_meeting(raw)
    except Exception as e:
        logger.exception("담당자 추천 실패 %s", meeting_id)  # 원인은 서버 로그에만 (응답에 내부 정보를 넣지 않음)
        raise HTTPException(status_code=502, detail="담당자 추천에 실패했어요. 잠시 뒤 다시 시도해 주세요.") from e
    return {"meeting_id": meeting_id, "items": items}


def _project(me: str, project_id: str | None) -> dict:
    """Neo4j Project 에 넣을 웹 프로젝트 정보 (이름 · 만든 시각). 못 찾으면 {} (적재는 project_id 로 계속)."""
    if not project_id:
        return {}
    try:
        return folders.get_folder(actor_id=me, folder_id=project_id)
    except Exception:
        logger.warning("프로젝트 조회 실패 %s", project_id, exc_info=True)
        return {}


def _meeting(me: str, meeting_id: str) -> dict:
    """Neo4j Meeting 에 넣을 작성 정보 (만든 시각 · 작성자). 못 찾으면 {} (두 속성 없이 적재 계속)."""
    try:
        return meetings.get_meeting(actor_id=me, meeting_id=meeting_id)
    except Exception:
        logger.warning("회의 조회 실패 %s", meeting_id, exc_info=True)
        return {}


@router.put("/meetings/{meeting_id}/action-items", response_model=list[RecommendedItem])
def confirm(meeting_id: str, body: Confirmation):
    items = [{"item_id": i.item_id, "user_ids": list(dict.fromkeys(i.user_ids)), "due": i.due.isoformat()}
             for i in body.items]
    try:
        result = action_items.confirm_items(meeting_id, items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if result is None:
        raise HTTPException(status_code=404, detail=f"회의 {meeting_id}를 찾을 수 없어요.")
    return result


@router.get("/meetings/{meeting_id}/minutes")
def get_minutes(meeting_id: str) -> dict:
    found = action_items.minutes(meeting_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"회의 {meeting_id}를 찾을 수 없어요.")
    return found
