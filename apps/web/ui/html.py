"""HTML 조각을 그리는 도우미."""

from __future__ import annotations

import streamlit as st


def markup(html: str) -> None:
    """정적인 HTML 블록 출력 (스크립트 없음)."""
    st.html(html)


def style_block(css: str) -> None:
    """화면마다 달라지는 CSS (그래프 노드 위치 등)."""
    if css.strip():
        st.html(f"<style>{css}</style>")


def classes(*names: str | None) -> str:
    return " ".join(n for n in names if n)
