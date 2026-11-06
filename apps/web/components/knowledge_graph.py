"""지식 그래프 + 오른쪽 상세 패널 (폴더 위키).

노드는 실제 st.button 이고, CSS 로 그래프 영역 위 절대 위치에 올립니다.
노드마다 위치·모양·투명도가 달라서 그때그때 CSS 를 만들어 넣습니다.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from data.models import KnowledgeGraph
from ui.buttons import button
from ui.html import markup, style_block
from utils.text import esc, safe_key

NODE_LOOK = {
    "me": "background:#FFFFFF;color:#1A1A1A;border:2px solid #F37321;border-radius:99px;padding:5px 14px 5px 5px;font-size:15px;font-weight:700;",
    "project": "background:#2B2D33;color:#FFFFFF;border:none;border-radius:4px;padding:10px 16px;font-size:15px;font-weight:700;",
    "topic": "background:#FFFFFF;color:#2B2F36;border:1.5px solid #F37321;border-radius:99px;padding:7px 14px;font-size:14px;font-weight:600;",
    "person": "background:#FFFFFF;color:#1A1A1A;border:1px solid #DDDFE3;border-radius:99px;padding:4px 12px 4px 4px;font-size:13px;font-weight:600;",
    "decision": "background:#F5F8FD;color:#2F538F;border:1.5px dashed #2F538F;border-radius:3px;padding:5px 10px;font-size:12px;font-weight:600;",
}

KIND_COLORS = {
    "me": "#1A1A1A",
    "project": "#2B2D33",
    "topic": "#2B2F36",
    "decision": "#2F538F",
    "person": "#474C55",
}


@dataclass(frozen=True)
class Avatar:
    letter: str
    accent: bool  # True: 주황 동그라미 (나/주관자)


def _node_css(scope: str, nid: str, kind: str, x: float, y: float, opacity: float, selected: bool,
              avatar: Avatar | None) -> str:
    sel = f".st-key-kgn-{scope}-{safe_key(nid)}"
    ring = "box-shadow:0 0 0 3px #D5D8DD;" if selected else "box-shadow:0 1px 2px rgba(0,0,0,.05);"
    look = NODE_LOOK[kind]
    css = (
        f"{sel}{{left:{x}% !important;top:{y}% !important;opacity:{opacity} !important;}}"
        f"{sel} button,{sel} button:hover,{sel} button:focus{{{look}{ring}min-height:0;line-height:1.5;}}"
        f"{sel} button p{{font-size:inherit !important;font-weight:inherit !important;display:flex;align-items:center;gap:6px;}}"
    )
    if avatar:
        size = 28 if kind == "me" else 26
        bg, fg = ("#F37321", "#FFFFFF") if kind == "me" else (("#F37321", "#1A1A1A") if avatar.accent else ("#EEF0F3", "#3B4048"))
        css += (
            f'{sel} button p::before{{content:"{avatar.letter}";width:{size}px;height:{size}px;border-radius:50%;'
            f"display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;"
            f"background:{bg};color:{fg};flex:none;}}"
        )
    return css


def knowledge_graph(
    graph: KnowledgeGraph,
    *,
    scope: str,
    selected: str,
    on_select: Callable[[str], None],
    height: int | str,
    opacity: dict[str, float],
    avatars: dict[str, Avatar] | None = None,
    legend_html: str = "",
) -> None:
    avatars = avatars or {}
    h = f"{height}px" if isinstance(height, int) else height  # 숫자(px) 또는 CSS 값 (예: calc(100vh - 500px))
    css = [f".st-key-kg-{scope}{{height:{h} !important;min-height:{h} !important;flex:none !important;}}"]
    for nid, n in graph.nodes.items():
        css.append(_node_css(scope, nid, n.kind, n.x, n.y, opacity.get(nid, 1.0), nid == selected, avatars.get(nid)))

    lines = "".join(
        f'<line x1="{graph.nodes[e.a].x}" y1="{graph.nodes[e.a].y}" x2="{graph.nodes[e.b].x}" y2="{graph.nodes[e.b].y}" '
        f'stroke-width="{e.weight}" vector-effect="non-scaling-stroke"/>'
        for e in graph.edges
    )
    # st.html 은 인라인 <svg> 를 지우므로 선은 data URI 이미지로 그립니다.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" preserveAspectRatio="none">'
        f'<g stroke="#D3D6DB" fill="none">{lines}</g></svg>'
    )
    edges_img = f'<img class="kg-lines" alt="" src="data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}">'
    labels = "".join(
        f'<span class="kg-edge" style="left:{(graph.nodes[e.a].x + graph.nodes[e.b].x) / 2}%;'
        f'top:{(graph.nodes[e.a].y + graph.nodes[e.b].y) / 2}%">{esc(e.label)}</span>'
        for e in graph.edges
        if selected in (e.a, e.b)
    )

    with st.container(key=f"kgwrap-{scope}", gap="small"):
        style_block("".join(css))
        with st.container(key=f"kg-{scope}", gap=None):
            markup(
                '<div class="kg-canvas" aria-hidden="true">'
                f"{edges_img}"
                f"{labels}</div>"
            )
            for nid, n in graph.nodes.items():
                with st.container(key=f"kgn-{scope}-{safe_key(nid)}", width="content"):
                    st.button(n.label, key=f"kgbtn_{scope}_{safe_key(nid)}", on_click=on_select, args=(nid,))
        if legend_html:
            markup(f'<div class="kg-legend">{legend_html}</div>')


# ---------------------------------------------------------------- 오른쪽 상세 패널
@dataclass(frozen=True)
class TimelineEntry:
    when: str
    text: str
    source_label: str
    meeting: str
    src: tuple[str, ...]
    absent: bool = False


def entity_panel(
    *,
    scope: str,
    kind_label: str,
    kind_color: str,
    title: str,
    desc: str,
    timeline: list[TimelineEntry],
    links: list[tuple[str, str, str]],  # (node_id, label, relation)
    on_link: Callable[[str], None],
    on_source: Callable[[str, tuple[str, ...]], None],
    relation: str = "",
    empty_text: str = "",
) -> None:
    with st.container(key=f"kgside-{scope}"):
        markup(
            f'<div><span class="ent-kind" style="background:{kind_color}">{esc(kind_label)}</span>'
            f'<h2 class="ent-title">{esc(title)}</h2><p class="ent-desc">{esc(desc)}</p></div>'
        )
        if relation:
            markup(f'<div><span class="ent-rel">나와의 관계: {esc(relation)}</span></div>')

        with st.container(key=f"tl-{scope}", gap=None):
            for i, t in enumerate(timeline):
                with st.container(key=f"tlrow-{scope}-{i}", horizontal=True, gap="small"):
                    st.html('<span class="tl-rail"><i></i><b></b></span>', width="content")
                    with st.container(key=f"tlbody-{scope}-{i}"):
                        markup(f'<span class="tl-when">{esc(t.when)}</span><br><span class="tl-text">{esc(t.text)}</span>')
                        label = t.source_label + (" · :orange[**미참석**]" if t.absent else "")
                        button(label, key=f"src_{scope}_{i}", variant="src", on_click=on_source, args=(t.meeting, t.src))
        if not timeline and empty_text:
            markup(f'<p class="hint" style="font-size:13px">{esc(empty_text)}</p>')

        if links:
            with st.container(horizontal=True, gap="xsmall"):
                for nid, label, rel in links:
                    button(f":gray[{rel} ·] {label}", key=f"lk_{scope}_{safe_key(nid)}", variant="rel",
                           on_click=on_link, args=(nid,))
        markup('<p class="hint">출처를 누르면 그 회의의 원본 발언으로 이동해요.</p>')
