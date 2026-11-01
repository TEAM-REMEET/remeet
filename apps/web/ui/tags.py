"""상태·역할 태그 (배지)."""

from __future__ import annotations

from utils.text import esc

# tone → CSS 클래스 (components.css 의 .tag-*)
TONES = {
    "host", "att", "abs", "shared", "ok", "hold", "warn", "split", "info",
    "mine", "deep", "fail", "wait", "run", "on", "off", "expired",
}

_MD_COLORS = {"host": "orange", "mine": "orange", "att": "gray", "abs": "blue", "shared": "violet", "info": "violet"}


def tag(label: str, tone: str = "att", *, extra_class: str = "", pulse: bool = False) -> str:
    tone = tone if tone in TONES else "att"
    cls = f"tag tag-{tone} {extra_class}".strip() + (" pulse" if pulse else "")
    return f'<span class="{cls}">{esc(label)}</span>'


def badge_count(value: object, active: bool = False) -> str:
    return f'<span class="cnt{" on" if active else ""}">{esc(value)}</span>'


def soft_label(label: str) -> str:
    """'안건 1' 같은 회색 작은 라벨."""
    return f'<span class="soft-label">{esc(label)}</span>'

def md_tag(label: str, tone: str = "att") -> str:
    return f":{_MD_COLORS.get(tone, 'gray')}-background[**{label}**]"