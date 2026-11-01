"""디자인 토큰 + 전역 CSS 주입.

색상 값은 assets/styles/base.css 의 :root 변수와 같은 값을 씁니다.
파이썬에서 인라인 스타일(막대 그래프 색 등)이 필요할 때만 여기 값을 참조하세요.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

STYLES_DIR = Path(__file__).resolve().parent.parent / "assets" / "styles"
CSS_FILES = ("base.css", "components.css", "pages.css", "landing.css")

COLORS = {
    "accent": "#F37321",
    "accent_soft": "#FEF1E8",
    "accent_line": "#FBB584",
    "ink": "#1A1A1A",
    "ink_2": "#2B2F36",
    "muted": "#80858E",
    "line": "#DDE0E4",
    "dark": "#2B2D33",
    "danger": "#B3261E",
    "success": "#2E9E62",
    "decision": "#2F538F",
}

# 안건 비중 막대 색 (순서대로 반복)
SEGMENT_COLORS = ("#F37321", "#1A1A1A", "#F89B6C", "#FBB584")


def _css() -> str:
    return "\n".join((STYLES_DIR / name).read_text(encoding="utf-8") for name in CSS_FILES)


def inject_css(extra: str = "") -> None:
    """모든 페이지 맨 위에서 한 번 호출."""
    st.html(f"<style>{_css()}\n{extra}</style>")
