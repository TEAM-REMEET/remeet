"""CSS·이미지 같은 정적 에셋 로딩."""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent.parent / "assets"


CSS_FILES = ["styles.css", "widgets.css"]  

HEADER_LOGO_H = 30               
HEADER_LOGO_RATIO = 1688 / 306   


def _css() -> str:
    return "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in CSS_FILES)


@st.cache_resource
def logo_data_uri(name: str = "logo.svg") -> str:
    """assets 폴더의 SVG를 화면에 바로 넣을 수 있는 data URI로 바꿔요.
    logo.svg = RE:MEET 글자 로고(헤더 · 로그인 카드), logo_header.svg = E 아이콘."""
    svg = (ASSETS / name).read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def inject_css() -> None:
    h = HEADER_LOGO_H
    w = round(h * HEADER_LOGO_RATIO)
    logo = (".st-key-nav_brand,.st-key-nav_brand [data-testid=\"stTooltipHoverTarget\"]{width:auto!important;max-width:none!important}"
            f".st-key-nav_brand button::before{{content:'';display:block;flex:none;width:{w}px;min-width:{w}px;height:{h}px;"
            f"background:url({logo_data_uri('logo.svg')}) no-repeat center/contain}}"
            ".st-key-nav_brand button p{display:none!important}"
            f".st-key-nav_brand button{{min-height:{h + 12}px!important;width:{w + 16}px!important;max-width:none!important;padding:6px 8px!important;border-radius:8px!important;justify-content:center!important}}"
            ".st-key-nav_brand button:hover{background:var(--accent-tint)!important}")
    st.markdown(f"<style>{_css()}\n{logo}</style>", unsafe_allow_html=True)