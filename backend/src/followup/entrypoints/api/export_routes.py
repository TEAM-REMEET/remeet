"""외부 도구 전송: POST /api/exports/jira (액션 아이템 → Jira 이슈) · POST /api/exports/mail (요약본 → 참석자 메일)

POST /api/exports/mail
body = {"meeting_title": "주간 회의", "body": "회의록 글", "recipients": ["u1", "u2"]}
응답 = {"items": [{"key": "u1", "ok": true, "sent": true}, {"key": "u2", "ok": false, "error": "…"}]}
    sent=false: 메일 주소가 없어 보내지 않음 · 일부만 실패해도 200 (실패한 사람만 다시 보내면 됨)
    오류: 401 · 422 · 502 (메일 서버 접속 · 로그인 실패) · 503 (메일 연동 설정 없음)

POST /api/exports/jira

body = {"meeting_title": "주간 회의", "items": [{"key": "41", "title": "…", "owners": ["u1"], "due": "2026-11-10"}]}
    key = 할 일 id (meeting_todos.id) → 이슈에 라벨 remeet-todo-{key}
응답 = {"project_key": "KAN", "created": 2, "total": 3,
        "items": [{"key": "41", "ok": true, "issue_key": "KAN-12", "assigned": true},
                  {"key": "42", "ok": true, "issue_key": "KAN-9", "assigned": false, "existing": true},
                  {"key": "43", "ok": false, "error": "…"}]}
    일부만 실패해도 200 (실패한 key 만 다시 보내면 됨)
    existing: 같은 라벨의 이슈가 이미 있어 새로 만들지 않음 (같은 할 일로 이슈가 두 번 생기지 않게)

오류 응답 (아무것도 만들지 않음 · 사용자가 다시 보내도 해결 안 됨 → 관리자 문의)
    401  로그인하지 않음
    422  입력 형식 오류
    502  Jira 에 연결하지 못함 (토큰 만료 · 권한)
    503  Jira 연동 설정이 없음
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from followup.application import export_jira, export_mail
from followup.entrypoints.api.routes.auth import current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(current_user)])


class Item(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")  # 할 일 id · Jira 라벨 · JQL 에 들어감
    title: str = Field(min_length=1, max_length=255)  # Jira summary 최대 255자
    owners: list[str] = Field(default_factory=list, max_length=20)
    due: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    agenda: str | None = Field(default=None, max_length=200)  # 이 일이 나온 안건 (이슈 설명에 적음)


class JiraExport(BaseModel):
    meeting_title: str = Field(min_length=1, max_length=200)
    meeting_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # 이슈 설명에 적음
    items: list[Item] = Field(max_length=100)  # 비어도 받음: 만들 이슈가 없어도 Jira 연결(인증)은 확인해야 함


# shortcut: 회의 주관자인지 확인하지 않음 (회의가 아직 화면 목업에만 있음), 회의가 DB 로 옮겨지면 주관자 확인 추가
@router.post("/jira")
def jira(body: JiraExport) -> dict[str, Any]:
    try:
        return export_jira.export(body.meeting_title, [i.model_dump(exclude_none=True) for i in body.items],
                                  meeting_date=body.meeting_date)
    except export_jira.JiraNotConfigured as e:
        raise HTTPException(status_code=503, detail="Jira 연동 설정이 없어요. 관리자에게 문의해 주세요.") from e
    except export_jira.JiraUnavailable as e:
        logger.exception("Jira 연결 실패")  # 원인은 서버 로그에만
        raise HTTPException(status_code=502, detail="Jira 에 연결하지 못했어요 (인증 만료 · 권한). 관리자에게 문의해 주세요.") from e


class MailExport(BaseModel):
    meeting_title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100_000)
    recipients: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=100)


# shortcut: 회의 주관자인지 · 본문이 그 회의의 회의록인지 확인하지 않음 (회의가 아직 화면 목업에만 있음),
#   회의가 DB 로 옮겨지면 주관자 확인 + 본문을 서버에서 만들기 · 받는 사람은 구성원(이메일 있는 사람)으로만 제한됨
@router.post("/mail")
def mail(body: MailExport) -> dict[str, Any]:
    try:
        return export_mail.export(body.meeting_title, body.body, body.recipients)
    except export_mail.MailNotConfigured as e:
        raise HTTPException(status_code=503, detail="메일 연동 설정이 없어요. 관리자에게 문의해 주세요.") from e
    except export_mail.MailUnavailable as e:
        logger.exception("메일 서버 연결 실패")  # 원인은 서버 로그에만
        raise HTTPException(status_code=502, detail="메일 서버에 연결하지 못했어요 (주소 · 비밀번호). 관리자에게 문의해 주세요.") from e
