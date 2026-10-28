"""작은 공통 UI 조각 (HTML 문자열을 만들거나 바로 그립니다)."""
from __future__ import annotations

import streamlit as st

from data.constants import SOURCE_LABEL, STATUS
from ui.theme import logo_data_uri
from utils.text import esc


def html(markup: str, width: str | int = "stretch") -> None:
    st.markdown(markup, unsafe_allow_html=True, width=width)


def logo_html() -> str:
    """사이트 로고(RE:MEET 글자 로고, assets/logo.svg)."""
    return f'<div class="logo"><img src="{logo_data_uri()}" height="40" alt="RE:MEET"></div>'


def status_badge(status: str) -> str:
    label, cls = STATUS[status]
    return f'<span class="badge b-{cls}">{esc(label)}</span>'


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="badge b-{kind}">{esc(text)}</span>'


def tag(text: str, kind: str = "") -> str:
    return f'<span class="tag {kind}">{esc(text)}</span>'


def source_tag(source: str | None) -> str:
    if not source:
        return ""
    kind = {"explicit": "", "suggested": "ai", "manual": "edit"}[source]
    return tag(SOURCE_LABEL[source], kind)


def phase(n: int) -> str:
    return f'<span class="phase">Phase {n}</span>'


def warn_chips(warnings: list[dict]) -> str:
    return "".join(f'<span class="warn-chip">⚠ {esc(w["t"])}</span>' for w in warnings)


def notice(text: str, kind: str = "") -> None:
    html(f'<div class="notice {kind}">{text}</div>')


def kv_list(rows: list[tuple[str, str]]) -> None:
    html("<dl>" + "".join(f'<div class="kv"><dt>{esc(k)}</dt><dd>{v}</dd></div>' for k, v in rows) + "</dl>")


def summary_cell(label: str, value: int, kind: str = "") -> None:
    color = {"run": "c-accent", "wait": "c-wait", "ok": "c-ok", "err": "c-err"}.get(kind, "")
    html(f'<div class="sum-cell"><div class="lbl {color}">{esc(label)}</div><div class="num">{value}<small>건</small></div></div>')


def history_line(h: dict) -> None:
    changes = "".join(f'<div class="diff">{esc(c["f"])}: <s>{esc(c["b"])}</s> → <b>{esc(c["a"])}</b></div>' for c in h.get("changes", []))
    html(f'<div class="row" style="align-items:flex-start"><span class="muted small" style="width:44px">{esc(h["t"])}</span>'
         f'<div class="grow"><strong>{esc(h["who"])}</strong> · {esc(h["text"])}{changes}</div></div>')
