"""날짜·시간 표시 유틸.

데모 데이터의 '오늘'은 2026-10-22로 고정합니다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

TODAY = date(2026, 10, 22)
DAYS = ["월", "화", "수", "목", "금", "토", "일"]  # date.weekday(): 월=0


def to_date(value: str) -> date:
    return date.fromisoformat(value)


def to_iso(value: date) -> str:
    return value.isoformat()


def add_days(value: str | date, days: int) -> date:
    base = to_date(value) if isinstance(value, str) else value
    return base + timedelta(days=days)


def fmt_d(value: str | None, year: bool = False) -> str:
    """'2026-10-28' → '10.28 (수)' / year=True → '2026.10.28 (수)'"""
    if not value:
        return ""
    d = to_date(value)
    head = f"{d.year}." if year else ""
    return f"{head}{d.month:02d}.{d.day:02d} ({DAYS[d.weekday()]})"


def dday(value: str | None) -> int | None:
    if not value:
        return None
    return (to_date(value) - TODAY).days


def dday_label(n: int | None) -> str:
    if n is None:
        return "미정"
    if n == 0:
        return "D-DAY"
    return f"D-{n}" if n > 0 else f"D+{-n}"


def now_time() -> str:
    return datetime.now().strftime("%H:%M")


def stamp() -> str:
    """데모 기준 일시 문자열 (예: '10.22 15:10')."""
    return f"{TODAY.month:02d}.{TODAY.day:02d} {now_time()}"
