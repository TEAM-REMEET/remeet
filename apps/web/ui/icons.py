"""assets/icons/*.svg 를 HTML 안에서 쓰기 위한 도우미."""

from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path
from config import LOGO_SVG

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


@lru_cache(maxsize=None)
def _raw(name: str) -> str:
    return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8").strip()


def icon(name: str, size: int = 16, color: str = "#5F646D", stroke: float | None = None) -> str:
    """st.html 은 인라인 <svg> 를 지우므로 data URI <img> 로 넣습니다. 색은 미리 칠해서 넘깁니다."""
    svg = _raw(name)
    svg = re.sub(r'width="\d+"', f'width="{size}"', svg, count=1)
    svg = re.sub(r'height="\d+"', f'height="{size}"', svg, count=1)
    svg = svg.replace('stroke="currentColor"', f'stroke="{color}"', 1)
    if stroke is not None:
        svg = re.sub(r'stroke-width="[\d.]+"', f'stroke-width="{stroke}"', svg, count=1)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img class="ico" src="data:image/svg+xml;base64,{data}" width="{size}" height="{size}" alt="" aria-hidden="true">'


def icon_box(name: str, *, bg: str, color: str, size: int = 13, box: int = 22, round_: bool = False, stroke: float = 2.2) -> str:
    """카드 제목 앞의 작은 아이콘 상자."""
    radius = "50%" if round_ else "3px"
    return (
        f'<span class="icon-box" style="width:{box}px;height:{box}px;background:{bg};border-radius:{radius}">'
        f"{icon(name, size, color, stroke)}</span>"
    )



@lru_cache(maxsize=1)
def _logo_data() -> str:
    return base64.b64encode(LOGO_SVG.read_bytes()).decode("ascii")


def brand_logo(height: int = 28) -> str:
    """가로형 로고 이미지 (assets/brand/logo.svg)."""
    return (f'<img class="brand-logo" src="data:image/svg+xml;base64,{_logo_data()}" '
            f'style="height:{height}px;width:auto" alt="RE:MEET">')


# ---------------------------------------------------------------- 외부 도구(MCP) 마크
_TOOL_LOGOS = {"jira": "jira.png", "slack": "slack.png", "notion": "notion.png"}  # assets/icons/tools/
_TOOL_ICONS = {"mail": "mail", "cal": "calendar"}  # 사내 메일 · 캘린더는 일반 아이콘


@lru_cache(maxsize=None)
def _png(name: str) -> str:
    return base64.b64encode((ICON_DIR / "tools" / name).read_bytes()).decode("ascii")


def tool_mark(tool_id: str, mark: str, extra_class: str = "") -> str:
    """도구 목록의 네모 마크: 로고(Jira · Slack · Notion) → 아이콘(메일 · 캘린더) → 없으면 글자(mark)."""
    cls = f"integ-mark {extra_class}".strip()
    if tool_id in _TOOL_LOGOS:
        return (f'<span class="{cls} logo"><img src="data:image/png;base64,{_png(_TOOL_LOGOS[tool_id])}" '
                f'alt="" aria-hidden="true"></span>')
    if tool_id in _TOOL_ICONS:
        return f'<span class="{cls} logo">{icon(_TOOL_ICONS[tool_id], 20, "#2B2F36")}</span>'
    return f'<span class="{cls}">{mark}</span>'

