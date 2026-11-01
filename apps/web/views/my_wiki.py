"""내 위키: 내가 주관·참석한 회의에서 나와 관련된 것만 모은 지식 그래프."""

from __future__ import annotations

import streamlit as st

from components.knowledge_graph import (
    KIND_COLORS,
    Avatar,
    TimelineEntry,
    entity_panel,
    knowledge_graph,
)
from config import CURRENT_USER_ID as ME
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button_group
from ui.layout import page, page_header

_KIND_LABEL = {"me": "나", "project": "주제 폴더", "topic": "논의 주제", "decision": "결정", "person": "멤버"}
_LEGEND = (
    '<span><i style="width:10px;height:10px;border-radius:50%;background:#F37321"></i>나</span>'
    '<span><i style="width:14px;height:10px;border-radius:3px;background:#2B2D33"></i>주제 폴더</span>'
    '<span><i style="width:14px;height:10px;border-radius:3px;border:1.5px solid #F37321"></i>논의 주제</span>'
    '<span><i style="width:14px;height:10px;border-radius:2px;border:1.5px dashed #2F538F"></i>결정</span>'
)


def _in_scope(s: AppState, mid: str, visible: list[str]) -> bool:
    m = repo.meeting(mid)
    if ME not in m.people or mid not in visible:
        return False
    if s.wiki_series == "all":
        return True
    folders = sel.folders(s)
    return m.topic in folders and folders[m.topic].type == s.wiki_series


def render() -> None:
    s = get_state()
    graph = repo.personal_wiki()
    visible = sel.visible_note_ids(s)
    my_count = sum(1 for i in visible if ME in repo.meeting(i).people)
    selected = s.wiki_node if s.wiki_node in graph.nodes else "me"
    near = graph.neighbors(selected)

    opacity = {}
    for nid in graph.nodes:
        has = nid == "me" or any(_in_scope(s, t.meeting, visible) for t in graph.timeline.get(nid, ()))
        opacity[nid] = 0.12 if not has else (1.0 if nid == selected or nid in near else 0.35)

    timeline = [t for t in graph.timeline.get(selected, ()) if _in_scope(s, t.meeting, visible)]
    node = graph.nodes[selected]

    with page("wiki"):
        page_header(f"{sel.me().short}님의 위키",
                    f"내가 주관하거나 참석한 회의 {my_count}건에서, 나와 관련된 것만 모았어요 · 나만 볼 수 있어요")
        with st.container(horizontal=True, vertical_alignment="center", gap="xsmall"):
            st.html('<span style="font-size:13px;color:#4A4F57;margin-right:4px">회의 종류</span>', width="content")
            button_group([("all", "전체"), ("weekly", "주간 회의"), ("monthly", "월간 회의"), ("normal", "일반 회의")],
                         s.wiki_series, lambda v: actions.update(wiki_series=v), key="series")
        left, right = st.columns([2.2, 1], gap="medium")
        with left:
            knowledge_graph(graph, scope="my", selected=selected, on_select=lambda nid: actions.update(wiki_node=nid),
                            height="max(380px, calc(100vh - 360px))", opacity=opacity, avatars={"me": Avatar(sel.me().initial, True)}, legend_html=_LEGEND)
        with right:
            entity_panel(
                scope="my",
                kind_label=_KIND_LABEL[node.kind],
                kind_color=KIND_COLORS[node.kind],
                title=node.label,
                desc=node.desc,
                relation="" if selected == "me" else graph.neighbors("me").get(selected, "연결된 주제를 통해 관련"),
                timeline=[TimelineEntry(t.when, t.text, sel.source_label(t.meeting, t.src), t.meeting, t.src) for t in timeline],
                empty_text="이 회의 종류에서는 나온 내용이 없어요.",
                links=[(nid, graph.nodes[nid].label, rel) for nid, rel in near.items() if nid != "me"],
                on_link=lambda nid: actions.update(wiki_node=nid),
                on_source=actions.open_source,
            )
