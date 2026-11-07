"""외부 도구 전송 · 메일: 회의 요약본 → 참석자마다 메일 한 통 (SMTP).

한 명이 실패해도 나머지는 계속 보내고 사람별 결과를 돌려준다.
화면은 실패한 사람(key = user_id)만 다시 보내므로 이미 받은 사람이 두 번 받지 않는다.
메일 주소가 없는 사람은 보내지 않고 ok=True, sent=False (다시 보내도 같으니 실패로 두지 않음).
"""

from __future__ import annotations

import logging
import smtplib
import time
from typing import Any, Callable

from followup import settings
from followup.adapters import smtp_mail
from followup.application.export_jira import emails_of

logger = logging.getLogger(__name__)


class MailNotConfigured(RuntimeError):
    """메일 연동 설정(.env)이 비어 있음."""


class MailUnavailable(RuntimeError):
    """메일 서버에 접속 · 로그인하지 못함 (주소 · 앱 비밀번호 · 네트워크)."""


def export(meeting_title: str, body: str, recipients: list[str], *, connect: Callable | None = None,
           emails: Callable[[set[str]], dict[str, str]] = emails_of) -> dict[str, Any]:
    """recipients: [user_id] → {"items": [{key, ok, sent} | {key, ok: False, error}]}"""
    cfg = settings.mail_config()
    if cfg is None:
        raise MailNotConfigured("메일 연동 설정이 없어요.")
    found = emails(set(recipients))
    subject = f"[RE:MEET] 회의 요약: {meeting_title}"
    try:
        smtp = (connect or smtp_mail.connect)(cfg)
    except (OSError, smtplib.SMTPException) as e:  # 아무에게도 못 보냄 (사람별 실패는 아래에서 결과에 담음)
        raise MailUnavailable(str(e)) from e
    out = []
    deadline = time.monotonic() + settings.EXPORT_BUDGET_SECONDS
    try:
        for uid in dict.fromkeys(recipients):
            if uid not in found:
                out.append({"key": uid, "ok": True, "sent": False})
            elif time.monotonic() > deadline:  # 화면이 기다리다 포기하기 전에 끝냄 · 남은 사람은 다시 보내기로
                out.append({"key": uid, "ok": False, "error": "시간 초과 (다시 보내 주세요)"})
            else:
                try:
                    smtp.send_message(smtp_mail.message(cfg["MAIL_FROM"], found[uid], subject, body))
                    out.append({"key": uid, "ok": True, "sent": True})
                except (OSError, smtplib.SMTPException) as e:  # 이 사람만 실패로 두고 다음 사람 계속
                    logger.warning("메일 전송 실패 (%s): %s", uid, e)
                    out.append({"key": uid, "ok": False, "error": str(e)[:200]})
    finally:
        smtp_mail.close(smtp)
    return {"items": out}
