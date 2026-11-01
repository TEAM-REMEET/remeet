"""날짜 관련 도우미."""

from __future__ import annotations


def date_key(md: str) -> int:
    """'10/27' → 1027 (정렬용 숫자)."""
    month, day = md.split("/")
    return int(month) * 100 + int(day)
