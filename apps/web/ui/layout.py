"""페이지 틀 · 카드 · 안내 문구 같은 레이아웃 프리미티브."""

from __future__ import annotations

from contextlib import contextmanager

import streamlit as st

from config import PAGE_MAX_WIDTH
from ui.html import markup
from utils.text import esc


@contextmanager
def page(name: str, max_width: int = PAGE_MAX_WIDTH):
    """페이지 영역. 모든 페이지가 같은 너비·같은 시작 위치를 쓰도록 기본값을 하나로 둡니다."""
    markup(f"<style>.st-key-page-{name}{{max-width:{max_width}px;margin:0 auto;}}</style>")
    with st.container(key=f"page-{name}", gap="medium"):
        yield


@contextmanager
def card(key: str, *, tone: str = "default", gap: str = "small"):
    """흰 카드. tone: default | warn(담당자 없음) | accent(주황 테두리) | muted"""
    with st.container(key=f"card-{tone}-{key}", gap=gap):
        yield


def page_header(title: str, desc: str = "", *, desc_html: str | None = None) -> None:
    body = desc_html if desc_html is not None else esc(desc)
    markup(
        '<header class="page-header">'
        f"<h1>{esc(title)}</h1>"
        + (f"<p>{body}</p>" if body else "")
        + "</header>"
    )


def card_title(title: str, *, icon_html: str = "", aside: str = "", size: int = 15) -> None:
    markup(
        f'<div class="card-title">{icon_html}<h3 style="font-size:{size}px">{esc(title)}</h3>'
        + (f'<span class="card-aside">{esc(aside)}</span>' if aside else "")
        + "</div>"
    )


def empty_state(text: str, *, title: str = "", icon_html: str = "", pad: int = 36) -> None:
    markup(
        f'<div class="empty" style="padding:{pad}px 24px">{icon_html}'
        + (f'<span class="empty-title">{esc(title)}</span>' if title else "")
        + f'<span class="empty-text{" sub" if title else ""}">{esc(text)}</span></div>'
    )


def notice(text_html: str, tone: str = "neutral") -> None:
    """한 줄 안내 배너. tone: neutral | success | danger"""
    markup(f'<div class="notice notice-{tone}" role="status">{text_html}</div>')


def hint(text: str, *, size: int = 12) -> None:
    markup(f'<p class="hint" style="font-size:{size}px">{esc(text)}</p>')


def spacer(px: int) -> None:
    markup(f'<div style="height:{px}px"></div>')
