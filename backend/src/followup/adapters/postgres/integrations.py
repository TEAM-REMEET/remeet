"""외부 연동 (Slack · 메일 · Jira …) — 연결 상태 · 마지막 전송 시각 · 전송 실패 기록.

web 화면 흐름과 같은 규칙
    연결 / 해제 / 다시 연결          관리자만 (MCP 관리 · 조직 전체에 적용)        set_tool_status()
        다시 연결(on) 하면 그 도구의 남은 tech 실패 → simple + 주관자에게 알림 (실제 재전송은 주관자가)
    보내기 성공                      회의 주관자                                    record_export()  → 마지막 전송 시각
    보내기 실패 기록                 회의 주관자 (전송을 시작한 사람)               record_failure()
        kind = simple  일시적 오류 → 주관자가 「다시 보내기」로 해결
        kind = tech    API · 인증 문제 → 관리자에게 알림, 관리자가 해결
        keys           실패한 것 (Jira 할 일 id · 메일 user_id) · None = 모름 → 다시 보낼 때 전부 (migrations/022)
    전송 끝 → 회의 상태              실패가 남았으면 fail, 아니면 done             finish_delivery()
    다시 보내서 해결                 simple = 주관자 · tech = 관리자                resolve_failure()
        남은 실패가 없으면 회의 상태 fail → done. 관리자가 해결하면 주관자에게 「전달 완료」 알림
"""
from __future__ import annotations

from uuid import uuid4

from followup.adapters.postgres.db import transaction
from .common import (
    NotFoundError,
    PermissionDenied,
    ValidationError,
    account,
    can_access_meeting,
    invalidate,
    is_admin,
    required,
)

STATUSES = frozenset({"on", "off", "expired"})
KINDS = frozenset({"simple", "tech"})


def _tool(cur, tool_id: str) -> dict:
    cur.execute("""SELECT t.*, COALESCE(s.status, 'off') AS status FROM integration_tools t
                   LEFT JOIN integration_status s ON s.tool_id = t.id WHERE t.id = %s""", (tool_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError(f"없는 연동 도구입니다: {tool_id}")
    return row


def _notify(cur, user_id: str, kind: str, text: str, meeting_id: str | None) -> None:
    cur.execute("INSERT INTO notifications (id, user_id, kind, text, meeting_id) VALUES (%s, %s, %s, %s, %s)",
                (f"n-{uuid4().hex[:16]}", user_id, kind, text, meeting_id))


# =====================================================================
# 도구 · 연결 상태
# =====================================================================
def organization(*, actor_id: str) -> str:
    """조직 이름 (외부 연동 화면 상단 · web integrations()["organization"]). app_settings 의 organization_name."""
    with transaction(actor_id) as cur:
        account(cur, actor_id)
        cur.execute("SELECT value FROM app_settings WHERE key = 'organization_name'")
        row = cur.fetchone()
    return row["value"] if row and isinstance(row["value"], str) else ""


def list_tools(*, actor_id: str) -> list[dict]:
    """연동 도구 목록 + 연결 상태 · 마지막 전송 시각 (로그인한 누구나)."""
    with transaction(actor_id) as cur:
        account(cur, actor_id)
        cur.execute("""SELECT t.id, t.mark, t.name, t.description, t.target, t.is_phase2, t.last_sync_at,
                              COALESCE(s.status, 'off') AS status, s.updated_at, s.updated_by
                       FROM integration_tools t LEFT JOIN integration_status s ON s.tool_id = t.id
                       ORDER BY t.sort_order, t.id""")
        return cur.fetchall()


def set_tool_status(*, actor_id: str, tool_id: str, status: str) -> dict:
    """연결 · 해제 · 다시 연결 (관리자만). 2단계(phase2) 도구는 아직 연결할 수 없음."""
    if status not in STATUSES:
        raise ValidationError(f"연결 상태는 {sorted(STATUSES)} 중 하나입니다.")
    with transaction(actor_id) as cur:
        if not is_admin(cur, actor_id):
            raise PermissionDenied("외부 연동 연결은 관리자만 바꿀 수 있습니다.")
        t = _tool(cur, tool_id)
        if t["is_phase2"] and status == "on":
            raise ValidationError(f"{t['name']} 연동은 2단계(Phase 2)에서 제공될 예정이에요.")
        cur.execute("""INSERT INTO integration_status (tool_id, status, updated_at, updated_by)
                       VALUES (%s, %s, now(), %s)
                       ON CONFLICT (tool_id) DO UPDATE SET status = EXCLUDED.status,
                           updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
                       RETURNING *""", (tool_id, status, actor_id))
        row = cur.fetchone()
        if status == "on":  # 관리자는 회의 내용을 볼 수 없어 보낼 수 없음 → 주관자가 다시 보낼 수 있게
            cur.execute("""UPDATE failed_exports f SET kind = 'simple' FROM meetings m
                           WHERE m.id = f.meeting_id AND f.tool_id = %s AND f.kind = 'tech' AND f.resolved_at IS NULL
                           RETURNING f.meeting_id, m.host_user_id, m.title""", (tool_id,))
            for r in cur.fetchall():
                _notify(cur, r["host_user_id"], "integration",
                        f"{t['name']} 연결이 복구됐어요. 「{r['title'] or r['meeting_id']}」 회의에서 「다시 보내기」를 눌러 주세요.",
                        r["meeting_id"])
    invalidate("integrations", "notifications")
    return row


# =====================================================================
# 보내기 결과
# =====================================================================
def record_export(*, actor_id: str, meeting_id: str, tool_id: str) -> None:
    """보내기 성공 → 도구의 마지막 전송 시각 갱신 (회의 주관자)."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        _tool(cur, tool_id)
        cur.execute("UPDATE integration_tools SET last_sync_at = now() WHERE id = %s", (tool_id,))
    invalidate("integrations")


def record_failure(*, actor_id: str, meeting_id: str, tool_id: str, kind: str, reason: str,
                   code: str = "", keys: list[str] | None = None) -> dict:
    """보내기 실패 기록 (회의 주관자). tech 실패면 관리자 전원에게 알림.
    keys: 실패한 것 (None = 모름 → 다시 보낼 때 전부). 빈 목록은 「없음」과 「모름」이 섞이므로 거절."""
    if kind not in KINDS:
        raise ValidationError("실패 종류는 simple 또는 tech 입니다.")
    if keys is not None and not keys:
        raise ValidationError("실패한 항목이 없으면 keys 를 보내지 마세요 (빈 목록은 쓰지 않음).")
    with transaction(actor_id) as cur:
        m = can_access_meeting(cur, meeting_id, actor_id, write=True)
        t = _tool(cur, tool_id)
        cur.execute("""INSERT INTO failed_exports (tool_id, meeting_id, kind, reason, code, failed_keys)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                    (tool_id, meeting_id, kind, required(reason, "실패 이유"), code, keys))
        row = cur.fetchone()
        if kind == "tech":
            cur.execute("SELECT user_id FROM accounts WHERE role = 'ADMIN'")
            for a in cur.fetchall():
                _notify(cur, a["user_id"], "fail",
                        f"「{m['title'] or meeting_id}」 결과를 {t['name']}(으)로 보내지 못했어요. {reason}", meeting_id)
    invalidate("integrations", "notifications")
    return row


def finish_delivery(*, actor_id: str, meeting_id: str) -> str:
    """전송이 끝났을 때 회의 상태 정하기 (주관자): 아직 해결 안 된 실패가 있으면 fail, 없으면 done."""
    with transaction(actor_id) as cur:
        can_access_meeting(cur, meeting_id, actor_id, write=True)
        cur.execute("SELECT 1 FROM failed_exports WHERE meeting_id = %s AND resolved_at IS NULL LIMIT 1", (meeting_id,))
        status = "fail" if cur.fetchone() else "done"
        cur.execute("UPDATE meetings SET status = %s WHERE id = %s", (status, meeting_id))
    invalidate("_base_meetings")
    return status


def list_failures(*, actor_id: str, meeting_id: str | None = None, open_only: bool = False) -> list[dict]:
    """전송 실패 기록 (새로 생긴 것이 먼저).
    관리자: 전부 (MCP 관리) · 일반 사용자: meeting_id 를 꼭 주고, 볼 수 있는 회의 것만."""
    with transaction(actor_id) as cur:
        admin = is_admin(cur, actor_id)
        if not admin:
            if not meeting_id:
                raise PermissionDenied("전체 전송 실패 기록은 관리자만 볼 수 있습니다.")
            can_access_meeting(cur, meeting_id, actor_id)
        cur.execute("""SELECT f.*, t.name AS tool_name, m.title AS meeting_title, m.host_user_id
                       FROM failed_exports f
                       JOIN integration_tools t ON t.id = f.tool_id
                       JOIN meetings m ON m.id = f.meeting_id
                       WHERE (%(mid)s::text IS NULL OR f.meeting_id = %(mid)s::text)
                         AND (NOT %(open)s OR f.resolved_at IS NULL)
                       ORDER BY f.failed_at DESC, f.id""",
                    {"mid": meeting_id, "open": open_only})
        return cur.fetchall()


def resolve_failure(*, actor_id: str, failure_id: str) -> dict:
    """다시 보내서 해결. simple = 회의 주관자 · tech = 관리자.
    남은 실패가 없으면 회의 상태 fail → done. 관리자가 해결했으면 주관자에게 「전달 완료」 알림."""
    with transaction(actor_id) as cur:
        cur.execute("SELECT * FROM failed_exports WHERE id = %s FOR UPDATE", (failure_id,))
        f = cur.fetchone()
        if f is None:
            raise NotFoundError("전송 실패 기록을 찾을 수 없습니다.")
        if f["resolved_at"] is not None:
            raise ValidationError("이미 해결된 실패입니다.")
        admin = is_admin(cur, actor_id)
        if f["kind"] == "tech" and not admin:
            raise PermissionDenied("기술 이슈는 관리자가 처리합니다. 관리자에게 문의해 주세요.")
        if f["kind"] == "simple" and not admin:
            can_access_meeting(cur, f["meeting_id"], actor_id, write=True)
        cur.execute("UPDATE failed_exports SET resolved_at = now(), resolved_by = %s WHERE id = %s RETURNING *",
                    (actor_id, failure_id))
        row = cur.fetchone()
        cur.execute("UPDATE integration_tools SET last_sync_at = now() WHERE id = %s", (f["tool_id"],))
        cur.execute("SELECT 1 FROM failed_exports WHERE meeting_id = %s AND resolved_at IS NULL LIMIT 1",
                    (f["meeting_id"],))
        if cur.fetchone() is None:
            cur.execute("UPDATE meetings SET status = 'done' WHERE id = %s AND status = 'fail'", (f["meeting_id"],))
        if admin:
            cur.execute("SELECT m.host_user_id, m.title, t.name FROM meetings m, integration_tools t "
                        "WHERE m.id = %s AND t.id = %s", (f["meeting_id"], f["tool_id"]))
            info = cur.fetchone()
            _notify(cur, info["host_user_id"], "delivered",
                    f"관리자가 「{info['title'] or f['meeting_id']}」 결과를 {info['name']}(으)로 다시 보냈어요. 전달 완료!",
                    f["meeting_id"])
    invalidate("integrations", "notifications", "_base_meetings")
    return row
