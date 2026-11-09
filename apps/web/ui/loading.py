"""화면 전체를 살짝 뿌옇게 덮고 점 12개가 도는 로딩 표시.

오래 걸리는 작업(LLM 호출 등) 바로 앞에 그리면, 작업이 끝나고 st.rerun() 할 때까지 화면 위에 떠 있어요.
"""

from __future__ import annotations

from ui.html import markup
from utils.text import esc

_DOTS = 12


def _spinner() -> str:
    """맨 앞 점이 가장 크고 진하고, 시계 반대 방향으로 갈수록 작고 옅어짐 · 통째로 시계 방향으로 돎."""
    dots = []
    for i in range(_DOTS):
        size = round(16 - i * 0.95, 1)  # 16px → 약 5.5px
        shade = 85 + i * 13  # #555 → #ddd 쯤
        dots.append(
            f'<span class="ld-dot" style="--a:{-i * 360 / _DOTS}deg;width:{size}px;height:{size}px;'
            f'margin:{-size / 2}px 0 0 {-size / 2}px;background:rgb({shade},{shade},{shade})"></span>'
        )
    return f'<div class="ld-wheel" aria-hidden="true">{"".join(dots)}</div>'


def callback_overlay(title: str, sub: str = "") -> None:
    """버튼 콜백 안에서 쓰는 로딩 표시. 콜백에서 그린 요소는 Streamlit 이 「다음」 실행 때 지우므로,
    화면을 다 그린 뒤 한 번 더 다시 그리라고 표시해 둠 (app.py 끝에서 st.rerun)."""
    import streamlit as st

    loading_overlay(title, sub)
    st.session_state["_clear_overlay"] = True


def loading_overlay(title: str, sub: str = "") -> None:
    markup(
        '<div class="ld-overlay" role="status" aria-live="polite">'
        f'<div class="ld-box">{_spinner()}<b>{esc(title)}</b>'
        + (f"<span>{esc(sub)}</span>" if sub else "")
        + "</div></div>"
    )
