"""CSS·이미지 같은 정적 에셋 로딩."""
from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent.parent / "assets"


CSS_FILES = ["styles.css", "widgets.css"]  # 디자인 → Streamlit 위젯 덮어쓰기 순서


def _css() -> str:
    # 캐시하지 않아요: CSS를 고친 뒤 브라우저에서 새로고침(R)만 하면 바로 반영돼요.
    return "\n".join((ASSETS / name).read_text(encoding="utf-8") for name in CSS_FILES)


@st.cache_resource
def logo_data_uri() -> str:
    svg = (ASSETS / "logo.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def inject_css() -> None:
    logo = f".st-key-nav_brand button::before{{content:'';width:26px;height:26px;margin-right:8px;background:url({logo_data_uri()}) no-repeat center/contain}}"
    st.markdown(f"<style>{_css()}\n{logo}</style>", unsafe_allow_html=True)
