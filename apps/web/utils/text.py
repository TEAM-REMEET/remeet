"""문자열 도우미."""

from __future__ import annotations

import html
import re

# 대화에서 날짜·시간 표현 찾기 (키워드·일정 탭)
DATE_TOKEN = re.compile(r"(\d{1,2}/\d{1,2}|\d{1,2}시|오늘|내일|다음 주|금요일|월요일|영업일 \d+일|D\+\d)")

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|~:$])")


def esc(value: object) -> str:
    """HTML 안에 넣을 문자열 이스케이프."""
    return html.escape(str(value), quote=True)


def md_escape(value: str) -> str:
    """st.button 등 마크다운 라벨에 원문을 그대로 보이게 할 때 사용."""
    return _MD_SPECIAL.sub(r"\\\1", value)


def find_date_token(text: str) -> str | None:
    m = DATE_TOKEN.search(text)
    return m.group(0) if m else None


def safe_key(value: str) -> str:
    """위젯 key / CSS 클래스에 쓸 수 있도록 영문·숫자·_ 만 남김."""
    return re.sub(r"[^0-9A-Za-z_]", "_", value)
