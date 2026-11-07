"""회의록 작성 흐름 (migrations/008 · 010 · web state/actions.py compose_* · start_finalize).

    ① save_draft()       status = draft. 작성 2단계(분석 및 검토)의 「미리보기」 회의록을 그대로 저장.
                          여러 번 저장해도 됨 (요약은 버전으로 쌓임). 작성자 · 주관자만 보임.
    ② submit_meeting()   draft → review. 참석자에게 「회의록이 생성됐어요」 알림.
    ③ confirm_meeting()  review → confirmed. 검토자(reviewer_id), 없으면 주관자만 (web: 주관자만 「확정하기」).

    흐름 (web compose_finish 와 같음)
      내가 주관자          미리보기 → 생성 → (바로) 확정                         검토 단계 없음
      주관자가 다른 사람   미리보기 → 생성 = 「검토 요청하기」 → 주관자 검토 · 수정 → 확정
                           reviewer_id = 주관자 (자동) · 주관자에게 review 알림 · 작성자는 이제 못 고침
      신입이 주관자        미리보기 → 생성 → 윗사람 검토 · 수정 → 확정            reviewer_id = 윗사람 (꼭 지정)

누가 신입인지는 구성원 표(팀원 담당)에 생길 예정 → seniority() 한 곳만 그 표를 보게 바꾸면 돼요.
    'junior' → (내가 주관자면) 윗사람 지정 필수 · 'senior' → 윗사람 지정하면 오류 · None(지금: 모름) → 넘긴 값을 따름
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from db import transaction
from .common import (
    NotFoundError,
    PermissionDenied,
    ValidationError,
    account,
    can_access_folder,
    invalidate,
    is_editor,
    require_user,
    required,
)
from .detail import insert_summary


def seniority(cur, user_id: str) -> str | None:
    """'junior'(신입) | 'senior' | None(모름).
    TODO(구성원 표 완성 후): 구성원 표의 직급 · 연차로 판단. 지금은 정보가 없어서 None."""
    return None


def _notify(cur, user_id: str, kind: str, text: str, meeting_id: str) -> None:
    cur.execute("INSERT INTO notifications (id, user_id, kind, text, meeting_id) VALUES (%s, %s, %s, %s, %s)",
                (f"n-{uuid4().hex[:16]}", user_id, kind, text, meeting_id))


def _check_user(cur, user_id: str, label: str) -> str:
    row = account(cur, required(user_id, label))
    if row["role"] != "USER":
        raise ValidationError(f"{label}은(는) 일반 사용자여야 합니다.")
    return user_id


# =====================================================================
# ① 미리보기 저장
# =====================================================================
def save_draft(*, actor_id: str, content: dict, meeting_id: str | None = None, title: str = "",
               directory_id: str = "normal", project_id: str | None = None, host_id: str | None = None,
               held_at: datetime | None = None, time_label: str = "", file_name: str = "",
               attendees: list[str] | None = None, transcript: list[dict] | None = None,
               todos: list[dict] | None = None, summary_json: dict | None = None) -> dict:
    """작성 단계의 미리보기 회의록 저장 (한 트랜잭션).

    meeting_id 없음 → 새 회의를 draft 로 만듦 (작성자 = actor, 주관자 = host_id 또는 actor)
    meeting_id 있음 → 그 draft 를 고쳐 저장 (작성자 · 주관자만, draft 일 때만)
    content    = 요약 (web 목업 meetings.json 모양: summary · discussion · decisions · open_issues · results · agendas)
    transcript = [{line_id, speaker, speaker_role?, text, source_line?}] (주면 원문을 통째로 바꿈)
    attendees  = 참석자 id (주관자는 늘 포함) · todos = AI 액션 아이템 (주면 바꿈 · item_id 포함)
    summary_json = 검토가 끝난 MeetingSummary JSON (web AppState.summaries) — 담당자 추천 API 에 보낼 원본
    """
    lines = transcript or []
    ids = [required(str(p.get("line_id") or ""), "발화 ID") for p in lines]
    if len(set(ids)) != len(ids):
        raise ValidationError("발화 ID가 중복됐습니다.")
    for p in lines:
        required(p.get("text"), "발화 내용")

    with transaction(actor_id) as cur:
        account(cur, actor_id)
        require_user(cur, actor_id)
        host = _check_user(cur, host_id, "주관자") if host_id else actor_id
        if project_id:
            can_access_folder(cur, project_id, actor_id)         # 프로젝트 멤버면 그 프로젝트에 쓸 수 있음
        cur.execute("SELECT 1 FROM meeting_types WHERE id = %s", (directory_id,))
        if cur.fetchone() is None:
            raise ValidationError(f"없는 회의 종류입니다: {directory_id}")

        if meeting_id is None:
            meeting_id = f"m-{uuid4().hex[:12]}"
            cur.execute(
                """INSERT INTO meetings (id, project_id, directory_id, host_user_id, title, held_at, time_label, file_name,
                                         status, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s) RETURNING *""",
                (meeting_id, project_id, directory_id, host, title.strip(), held_at, time_label, file_name, actor_id),
            )
        else:
            cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (meeting_id,))
            m = cur.fetchone()
            if m is None:
                raise NotFoundError("회의를 찾을 수 없습니다.")
            if m["status"] != "draft":
                raise ValidationError("이미 생성된 회의록은 미리보기로 저장할 수 없습니다.")
            if not is_editor(m, actor_id):
                raise PermissionDenied("이 미리보기를 고칠 수 없습니다.")
            cur.execute(
                """UPDATE meetings SET project_id = %s, directory_id = %s, host_user_id = %s, title = %s,
                       held_at = %s, time_label = %s, file_name = %s
                   WHERE id = %s RETURNING *""",
                (project_id, directory_id, host, title.strip(), held_at, time_label, file_name, meeting_id),
            )
        row = cur.fetchone()

        cur.execute("DELETE FROM meeting_attendees WHERE meeting_id = %s", (meeting_id,))
        for i, uid in enumerate(dict.fromkeys([host, *(attendees or [])])):
            cur.execute("INSERT INTO meeting_attendees (meeting_id, user_id, position) VALUES (%s, %s, %s)",
                        (meeting_id, required(uid, "참석자 ID"), i))
        if transcript is not None:
            cur.execute("DELETE FROM transcript_lines WHERE meeting_id = %s", (meeting_id,))
            for i, p in enumerate(lines):
                cur.execute(
                    """INSERT INTO transcript_lines (meeting_id, line_id, position, speaker, speaker_role, text,
                                                     source_line)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (meeting_id, ids[i], i, p.get("speaker", ""), p.get("speaker_role", ""), p["text"],
                     p.get("source_line")),
                )
        cur.execute("SELECT 1 FROM meeting_summaries WHERE meeting_id = %s LIMIT 1", (meeting_id,))
        first = cur.fetchone() is None
        summary = insert_summary(cur, meeting_id=meeting_id, content=content, todos=todos,
                                 origin="ai" if first else "edited", created_by=actor_id, summary_json=summary_json)
    invalidate("_base_meetings", "base_folders")
    return {**row, "summary_version": summary["version"]}


def list_my_drafts(*, actor_id: str) -> list[dict]:
    """내가 쓰다 만 미리보기 회의록 (최근 저장 순)."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            """SELECT m.id, m.title, m.directory_id, m.project_id, m.held_at, m.host_user_id,
                      (SELECT max(created_at) FROM meeting_summaries s WHERE s.meeting_id = m.id) AS saved_at
               FROM meetings m
               WHERE m.status = 'draft' AND m.deleted_at IS NULL AND %s IN (m.created_by, m.host_user_id)
               ORDER BY saved_at DESC NULLS LAST, m.created_at DESC""",
            (actor_id,),
        )
        return cur.fetchall()


# =====================================================================
# ② 회의록 생성 (제출)
# =====================================================================
def submit_meeting(*, actor_id: str, meeting_id: str, reviewer_id: str | None = None) -> dict:
    """미리보기 → 회의록 생성 (draft → review).

    reviewer_id: 신입이 쓴 회의록을 검토할 윗사람. 안 주면 — 작성자가 주관자면 검토 없음(주관자가 바로 확정),
                 작성자가 주관자가 아니면 주관자가 검토자 (web 「검토 요청하기」).
    알림: 참석자 · 주관자(나 · 검토자 빼고) → created 「회의록이 생성됐어요」 / 검토자 → review 「검토 요청」
    """
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (meeting_id,))
        m = cur.fetchone()
        if m is None:
            raise NotFoundError("회의를 찾을 수 없습니다.")
        if m["status"] != "draft":
            raise ValidationError("이미 생성된 회의록입니다.")
        if not is_editor(m, actor_id):
            raise PermissionDenied("이 회의록을 생성할 권한이 없습니다.")
        cur.execute("SELECT 1 FROM meeting_summaries WHERE meeting_id = %s LIMIT 1", (meeting_id,))
        if cur.fetchone() is None:
            raise ValidationError("미리보기를 먼저 저장해 주세요.")
        level = seniority(cur, m["created_by"])
        if reviewer_id:
            if level == "senior" and reviewer_id != m["host_user_id"]:
                raise ValidationError("신입이 쓴 회의록만 윗사람 검토 단계를 거칩니다.")
            _check_user(cur, reviewer_id, "검토자")
            if reviewer_id == m["created_by"]:
                raise ValidationError("작성자 본인을 검토자로 지정할 수 없습니다.")
        elif m["created_by"] != m["host_user_id"]:
            reviewer_id = m["host_user_id"]          # 주관자가 아닌 사람이 씀 → 주관자에게 검토 요청 (web)
        elif level == "junior":
            raise ValidationError("신입이 쓴 회의록은 검토할 윗사람을 지정해야 합니다.")

        cur.execute(
            """UPDATE meetings SET status = 'review', reviewer_id = %s, submitted_at = now()
               WHERE id = %s RETURNING *""",
            (reviewer_id or None, meeting_id),
        )
        row = cur.fetchone()
        title = row["title"] or "회의"
        cur.execute("SELECT user_id FROM meeting_attendees WHERE meeting_id = %s ORDER BY position", (meeting_id,))
        for uid in dict.fromkeys([row["host_user_id"], *(r["user_id"] for r in cur.fetchall())]):
            if uid not in (actor_id, reviewer_id):
                _notify(cur, uid, "created", f"「{title}」 회의록이 생성됐어요.", meeting_id)
        if reviewer_id:
            _notify(cur, reviewer_id, "review", f"「{title}」 회의록 검토 요청이 왔어요. 검토한 뒤 확정해 주세요.", meeting_id)
    invalidate("_base_meetings", "base_folders", "notifications")
    return row


def list_review_queue(*, actor_id: str) -> list[dict]:
    """내가 확정해야 할 회의록 (검토 대기): 내가 검토자인 것 + 검토자 없이 내가 주관자인 것."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            """SELECT id, title, created_by, host_user_id, reviewer_id, submitted_at
               FROM meetings
               WHERE status = 'review' AND deleted_at IS NULL
                 AND COALESCE(reviewer_id, host_user_id) = %(a)s
               ORDER BY submitted_at DESC NULLS LAST, created_at DESC""",
            {"a": actor_id},
        )
        return cur.fetchall()


# =====================================================================
# ④ 확정
# =====================================================================
def confirm_meeting(*, actor_id: str, meeting_id: str) -> dict:
    """회의록 확정 (review → confirmed). 검토자(없으면 주관자)만."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (meeting_id,))
        m = cur.fetchone()
        if m is None:
            raise NotFoundError("회의를 찾을 수 없습니다.")
        if m["status"] != "review":
            raise ValidationError("검토 대기 중인 회의록만 확정할 수 있습니다.")
        if not is_editor(m, actor_id):
            raise PermissionDenied("확정 권한이 없습니다 (검토자, 없으면 주관자만 확정할 수 있어요).")
        cur.execute(
            """UPDATE meetings SET status = 'confirmed', confirmed_at = now(), confirmed_by = %s
               WHERE id = %s RETURNING *""",
            (actor_id, meeting_id),
        )
        row = cur.fetchone()
    invalidate("_base_meetings", "base_folders")
    return row
