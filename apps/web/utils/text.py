"""문자열·식별자 유틸."""
from __future__ import annotations

import html
import itertools
import re

_counter = itertools.count(1000)


def new_id(prefix: str) -> str:
    return f"{prefix}{next(_counter)}"


def esc(value: object) -> str:
    """HTML에 넣을 값을 이스케이프합니다."""
    return html.escape(str(value), quote=True)


def sid(n: int) -> str:
    """원문 문단 번호 표기 (#05)."""
    return f"#{n:02d}"


def srange(a: int, b: int) -> str:
    return sid(a) if a == b else f"{sid(a)}–{sid(b)}"


def cut(value: str | None, size: int = 46) -> str:
    if not value:
        return ""
    return value if len(value) <= size else value[:size] + "…"


SPEAKER_RE = re.compile(r"^[^:：]{1,10}[:：]\s*")


def strip_speaker(line: str) -> str:
    return SPEAKER_RE.sub("", line)


def speaker_of(line: str) -> str | None:
    m = re.match(r"^([^:：]{1,10})[:：]", line)
    return m.group(1).strip() if m else None
