"""회의록 작성 흐름 (migrations/008 · 010 · web state/actions.py compose_* · start_finalize).

    ① save_draft()       status = draft. 작성 2단계(분석 및 검토)의 「미리보기」 회의록을 그대로 저장.
                          여러 번 저장해도 됨 (요약은 버전으로 쌓임). 작성자 · 주관자만 보임.
    ①' handoff_meeting()  (작성자 ≠ 주관자) 분석이 끝난 미리보기를 주관자에게 넘김 · 주관자에게 review 알림
                          이후 작성자는 못 고치고, 주관자가 홈 「회의록 생성 검토 대기」→「검토하기」로 2단계부터 이어서 함
    ② submit_meeting()   draft → review. 참석자 · 주관자에게 「회의록이 생성됐어요」 알림.
    ③ confirm_meeting()  review → confirmed. 주관자 (+ 예전 방식의 검토자).

    흐름 (web compose_* 와 같음 · migrations/022)
      내가 주관자          미리보기 → 생성 → (바로) 확정
      주관자가 다른 사람   (작성자) 분석 → 넘김 → 새 회의록 화면으로
                           (주관자) 검토하기 → 2단계 미리보기 수정 → 생성 → 확정 → 결과 전달 → 외부 도구 전송
      주관자는 시니어 · 미드만 (신입이 쓰면 윗사람을 주관자로). 검토자(reviewer_id) 단계는 없어요.

누가 신입인지는 구성원 표 people.position (seniority()). None(모름) → 막지 않음.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from followup.adapters.postgres.db import transaction
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
    """'junior'(신입) | 'senior' | None(모름) — 구성원 표 people.position (migrations/011 · seed_people.py).
    junior → 'junior' · mid · senior → 'senior' · 구성원 표에 없으면 None (넘긴 reviewer_id 를 따름)."""
    cur.execute("SELECT position FROM people WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return "junior" if row["position"] == "junior" else "senior"


def _notify(cur, user_id: str, kind: str, text: str, meeting_id: str) -> None:
    cur.execute("INSERT INTO notifications (id, user_id, kind, text, meeting_id) VALUES (%s, %s, %s, %s, %s)",
                (f"n-{uuid4().hex[:16]}", user_id, kind, text, meeting_id))


def _check_host_level(cur, host_id: str) -> None:
    if seniority(cur, host_id) == "junior":
        raise ValidationError("주관자는 시니어 · 미드만 될 수 있어요. 신입은 주관자로 정할 수 없어요.")


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
                      아직 없는 id 면 그 id 로 새로 만듦 (AI 요약이 정한 회의 id 를 그대로 쓰기 · Neo4j 와 같은 id)
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

        existing = None
        if meeting_id is not None:
            cur.execute("SELECT * FROM meetings WHERE id = %s FOR UPDATE", (meeting_id,))
            existing = cur.fetchone()
        if existing is None:
            meeting_id = meeting_id or f"m-{uuid4().hex[:12]}"
            cur.execute(
                """INSERT INTO meetings (id, project_id, directory_id, host_user_id, title, held_at, time_label, file_name,
                                         status, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s) RETURNING *""",
                (meeting_id, project_id, directory_id, host, title.strip(), held_at, time_label, file_name, actor_id),
            )
        else:
            m = existing
            if m["deleted_at"] is not None:
                raise NotFoundError("회의를 찾을 수 없습니다.")
            if m["status"] != "draft":
                raise ValidationError("이미 생성된 회의록은 미리보기로 저장할 수 없습니다.")
            if not is_editor(m, actor_id):
                raise PermissionDenied("주관자에게 넘긴 회의록은 주관자만 고칠 수 있습니다." if m["handed_off_at"]
                                       else "이 미리보기를 고칠 수 없습니다.")
            cur.execute(
                """UPDATE meetings SET project_id = %s, directory_id = %s, host_user_id = %s, title = %s,
                       held_at = %s, time_label = %s, file_name = %s
                   WHERE id = %s RETURNING *""",
                (project_id, directory_id, host, title.strip(), held_at, time_label, file_name, meeting_id),
            )
        row = cur.fetchone()

        cur.execute("DELETE FROM meeting_attendees WHERE meeting_id = %s", (meeting_id,))
        # 여러 줄은 executemany 로 한 번에 보냄 (pipeline · 원격 DB 에서 줄마다 왕복하면 화면 15초 제한을 넘김)
        cur.executemany("INSERT INTO meeting_attendees (meeting_id, user_id, position) VALUES (%s, %s, %s)",
                        [(meeting_id, required(uid, "참석자 ID"), i)
                         for i, uid in enumerate(dict.fromkeys([host, *(attendees or [])]))])
        if transcript is not None:
            cur.execute("DELETE FROM transcript_lines WHERE meeting_id = %s", (meeting_id,))
            if lines:
                cur.executemany(
                    """INSERT INTO transcript_lines (meeting_id, line_id, position, speaker, speaker_role, text,
                                                     source_line)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    [(meeting_id, ids[i], i, p.get("speaker", ""), p.get("speaker_role", ""), p["text"],
                      p.get("source_line")) for i, p in enumerate(lines)],
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
    프로젝트 회의면 참석자 중 아직 멤버가 아닌 사람을 프로젝트 멤버로 넣어요 (미리보기 때는 참석자가 바뀔 수 있어 생성 시점에).
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
        # 검토자는 따로 두지 않아요 (주관자가 윗사람 역할) · 주관자는 시니어 · 미드만
        if reviewer_id:
            raise ValidationError("검토자는 따로 지정하지 않아요. 시니어 · 미드 주관자가 편집 · 확정해요.")
        _check_host_level(cur, m["host_user_id"])
        if actor_id != m["host_user_id"]:   # 작성자 ≠ 주관자 → 넘긴 뒤 주관자가 생성 (작성자는 주관자 역할 없음)
            raise PermissionDenied("회의록 생성은 주관자만 할 수 있어요. 분석이 끝나면 주관자에게 넘겨 주세요.")

        cur.execute(
            """UPDATE meetings SET status = 'review', reviewer_id = %s, submitted_at = now()
               WHERE id = %s RETURNING *""",
            (reviewer_id or None, meeting_id),
        )
        row = cur.fetchone()
        if row["project_id"]:   # 이미 있는 멤버는 그대로 · 새로 넣은 사람에게는 트리거가 member 알림을 보냄 (migrations/003 · 015)
            cur.execute("""INSERT INTO folder_members (folder_id, user_id, position, added_by)
                           SELECT %(f)s, a.user_id,
                                  (SELECT COALESCE(MAX(position) + 1, 0) FROM folder_members WHERE folder_id = %(f)s)
                                    + row_number() OVER (ORDER BY a.position) - 1,
                                  %(a)s
                           FROM meeting_attendees a WHERE a.meeting_id = %(m)s
                           ON CONFLICT (folder_id, user_id) DO NOTHING""",
                        {"f": row["project_id"], "m": meeting_id, "a": actor_id})
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
    """[홈 · 회의록 생성 검토 대기] 나에게 넘어온 회의록 (최근 넘어온 순).

    kind = 'handoff' : 작성자가 분석을 마치고 넘긴 미리보기 (draft) → 「검토하기」로 작성 2단계부터
           'review'  : 예전 방식으로 검토를 요청받은 회의록 (review · 내가 검토자) → 회의 상세에서 확정
    """
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute(
            """SELECT m.id, m.title, m.created_by, m.host_user_id, m.reviewer_id, m.status,
                      COALESCE(p.name, a.email, m.created_by) AS author_name,
                      CASE WHEN m.status = 'draft' THEN 'handoff' ELSE 'review' END AS kind,
                      COALESCE(m.handed_off_at, m.submitted_at) AS requested_at
               FROM meetings m
               LEFT JOIN people p ON p.user_id = m.created_by
               LEFT JOIN accounts a ON a.user_id = m.created_by
               WHERE m.deleted_at IS NULL
                 AND ((m.status = 'draft' AND m.handed_off_at IS NOT NULL AND m.host_user_id = %(a)s)
                      OR (m.status = 'review' AND m.reviewer_id = %(a)s AND m.created_by <> %(a)s))
               ORDER BY requested_at DESC NULLS LAST, m.created_at DESC""",
            {"a": actor_id},
        )
        return cur.fetchall()


def handoff_meeting(*, actor_id: str, meeting_id: str, request: dict, draft: dict | None = None) -> dict:
    """작성자 → 주관자: 분석이 끝난 미리보기를 넘김 (작성자 ≠ 주관자 · draft · 한 번만).

    request = 작성 화면 입력값 (POST /api/meeting-jobs 본문 그대로: 제목 · 날짜 · 주관자 · 참석자 · 형식 · 원문 …)
    draft   = 화면에서만 쓰는 값 (새 프로젝트 이름 nn_name 등)
    → 주관자가 「검토하기」를 누르면 이 값과 저장된 요약 JSON 으로 작성 2단계를 그대로 다시 열어요.
    주관자가 그 프로젝트 멤버가 아니면 멤버로 넣어요 (미리보기를 고쳐 저장하려면 프로젝트에 쓸 수 있어야 함).
    """
    import json

    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (meeting_id,))
        m = cur.fetchone()
        if m is None:
            raise NotFoundError("회의를 찾을 수 없습니다.")
        if m["created_by"] != actor_id:
            raise PermissionDenied("회의록을 쓴 사람만 주관자에게 넘길 수 있습니다.")
        if m["status"] != "draft" or m["handed_off_at"] is not None:
            raise ValidationError("이미 주관자에게 넘긴 회의록입니다.")
        if m["host_user_id"] == actor_id:
            raise ValidationError("내가 주관자인 회의록은 넘기지 않고 바로 확정해요.")
        _check_host_level(cur, m["host_user_id"])
        cur.execute("SELECT 1 FROM meeting_summaries WHERE meeting_id = %s LIMIT 1", (meeting_id,))
        if cur.fetchone() is None:
            raise ValidationError("미리보기를 먼저 저장해 주세요.")
        cur.execute("UPDATE meetings SET handed_off_at = now(), handoff = %s WHERE id = %s RETURNING *",
                    (json.dumps({"request": request, "draft": draft or {}}, ensure_ascii=False), meeting_id))
        row = cur.fetchone()
        if row["project_id"]:
            cur.execute("""INSERT INTO folder_members (folder_id, user_id, position, added_by)
                           SELECT %(f)s, %(h)s, COALESCE(MAX(position) + 1, 0), %(a)s FROM folder_members WHERE folder_id = %(f)s
                           ON CONFLICT (folder_id, user_id) DO NOTHING""",
                        {"f": row["project_id"], "h": row["host_user_id"], "a": actor_id})
        _notify(cur, row["host_user_id"], "review",
                f"「{row['title'] or '회의'}」 회의록 분석이 끝났어요. 홈의 「회의록 생성 검토 대기」에서 검토해 주세요.", meeting_id)
    invalidate("_base_meetings", "base_folders", "notifications")
    return row


def get_handoff(*, actor_id: str, meeting_id: str) -> dict:
    """주관자가 「검토하기」로 작성 2단계를 다시 열 때: 넘겨받은 입력값 + 저장된 요약 JSON (주관자만 · draft 일 때만)."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL", (meeting_id,))
        m = cur.fetchone()
        if m is None or m["handed_off_at"] is None:
            raise NotFoundError("넘겨받은 회의록을 찾을 수 없습니다.")
        if m["host_user_id"] != actor_id:
            raise PermissionDenied("주관자만 검토할 수 있습니다.")
        if m["status"] != "draft":
            raise ValidationError("이미 생성된 회의록이에요. 회의 상세에서 이어서 진행해 주세요.")
        cur.execute("""SELECT summary_json FROM meeting_summaries WHERE meeting_id = %s AND summary_json IS NOT NULL
                       ORDER BY version DESC LIMIT 1""", (meeting_id,))
        s = cur.fetchone()
    handoff = m["handoff"] or {}
    return {"meeting_id": meeting_id, "author": m["created_by"], "handed_off_at": m["handed_off_at"],
            "request": handoff.get("request") or {}, "draft": handoff.get("draft") or {},
            "result": s["summary_json"] if s else None}


# =====================================================================
# ④ 확정
# =====================================================================
def confirm_meeting(*, actor_id: str, meeting_id: str) -> dict:
    """회의록 확정 (review → confirmed). 주관자 (+ 예전 방식의 검토자)."""
    with transaction(actor_id) as cur:
        require_user(cur, actor_id)
        cur.execute("SELECT * FROM meetings WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (meeting_id,))
        m = cur.fetchone()
        if m is None:
            raise NotFoundError("회의를 찾을 수 없습니다.")
        if m["status"] != "review":
            raise ValidationError("검토 대기 중인 회의록만 확정할 수 있습니다.")
        if not is_editor(m, actor_id):
            raise PermissionDenied("확정 권한이 없습니다 (주관자만 확정할 수 있어요).")
        cur.execute(
            """UPDATE meetings SET status = 'confirmed', confirmed_at = now(), confirmed_by = %s
               WHERE id = %s RETURNING *""",
            (actor_id, meeting_id),
        )
        row = cur.fetchone()
    invalidate("_base_meetings", "base_folders")
    return row
