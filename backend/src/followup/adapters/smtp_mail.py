"""메일 (SMTP): 한 번 접속 · 로그인해서 받는 사람마다 한 통씩.

playground/hyungjin/external_integration 에서 확인한 방식 그대로 (Gmail: smtp.gmail.com:587 + 앱 비밀번호).
465 는 SSL, 그 밖의 포트는 STARTTLS · 둘 다 서버 인증서를 검증한다.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from followup import settings


def connect(cfg: dict[str, str]) -> smtplib.SMTP:
    """로그인까지 마친 SMTP 연결 (다 쓰면 close)."""
    host, port, tls = cfg["SMTP_HOST"], int(cfg["SMTP_PORT"]), ssl.create_default_context()
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=settings.EXPORT_CALL_TIMEOUT, context=tls)
    else:
        smtp = smtplib.SMTP(host, port, timeout=settings.EXPORT_CALL_TIMEOUT)
    try:
        if port != 465:
            smtp.starttls(context=tls)
        smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
    except BaseException:
        close(smtp)
        raise
    return smtp


def close(smtp) -> None:
    try:
        smtp.quit()
    except (OSError, smtplib.SMTPException):  # 이미 끊긴 연결 · 보낸 메일에는 영향 없음
        pass


def message(sender: str, to: str, subject: str, body: str) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = " ".join(subject.split())  # 줄바꿈이 들어가면 헤더 주입이 되므로 공백으로
    m["From"], m["To"] = sender, to
    m.set_content(body)  # 글자만 (text/plain) · HTML 로 해석되지 않음
    return m
