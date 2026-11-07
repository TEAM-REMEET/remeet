"""날짜 관련 도우미."""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo


def today() -> date:
    """한국 시간 기준 오늘 (서버가 UTC여도 밤 9시 이후에 날짜가 하루 밀리지 않게)."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def date_key(md: str) -> int:
    """'10/27' → 1027 (정렬용 숫자)."""
    month, day = md.split("/")
    return int(month) * 100 + int(day)


def parse_due(due: str | None) -> date | None:
    """마감일 문자열 → 날짜. 「2026-11-20」(요약 JSON · 확정 값) · 「11/5」 · 「11/5까지」(올해). 날짜가 아니면 None."""
    iso = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", due or "")
    md = re.match(r"\s*(\d{1,2})/(\d{1,2})", due or "")
    if iso:
        year, month, day = map(int, iso.groups())
    elif md:
        year, (month, day) = date.today().year, map(int, md.groups())
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def due_label(due: str | None) -> str:
    """화면 표시용: 「2026-11-20」 → 「11/20」 (날짜가 아니면 그대로)."""
    d = parse_due(due) if due and re.match(r"\s*\d{4}-", due) else None
    return f"{d.month}/{d.day}" if d else (due or "")
