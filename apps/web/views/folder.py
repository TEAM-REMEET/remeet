"""폴더 화면.

- 회의 종류(주간/월간/일반) 화면: 내가 멤버인 폴더 카드 목록
- 폴더 화면: 멤버 · 탭(전체/참석/미참석/위키) · 회의 목록 또는 폴더 위키
"""

from __future__ import annotations

import streamlit as st

from components.knowledge_graph import (
    KIND_COLORS,
    Avatar,
    TimelineEntry,
    entity_panel,
    knowledge_graph,
)
from components.meeting_table import meeting_table
from components.member_pills import member_pills
from config import CURRENT_USER_ID as ME
from config import FOLDER_WIKI_MIN_NOTES
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page, page_header
from ui.tags import tag
from utils.text import esc

_OWNER_EMPTY = {
    "mine": "내가 만든 폴더가 없어요. 회의록을 올릴 때 새 주제를 적으면 폴더가 만들어져요.",
    "member": "멤버로 들어가 있는 폴더가 없어요.",
    "all": "아직 속한 폴더가 없어요. 회의록을 올릴 때 큰 주제를 적으면 폴더가 만들어져요.",
}


# ================================================================ 회의 종류 화면
def _topic_card(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    notes = sel.by_date_desc(list(f.notes))
    last = f"최근 {repo.meeting(notes[0]).date} · {repo.meeting(notes[0]).title}" if notes else "아직 회의록이 없어요"
    mine = sum(sel.mine_count(i) for i in f.notes)
    is_host = f.host == ME
    with st.container(key=f"topic-{fid}"):
        markup(
            '<div class="topic-card">'
            f'<div class="head"><span class="nm">{icon("folder", 18, "#F37321", 1.9)}{esc(f.name)}</span>'
            f'{tag("내가 만든 폴더" if is_host else "멤버", "host" if is_host else "att")}</div>'
            f'<span class="meta">회의록 {len(f.notes)}개 · 멤버 {len(sel.members_of(s, fid))}명</span>'
            f'<span class="last">{esc(last)}</span>'
            + (f'<span>{tag(f"내 할 일 {mine}", "mine")}</span>' if mine else "")
            + "</div>"
        )
        button(f.name, key=f"open_topic_{fid}", variant="secondary", on_click=actions.open_folder, args=(fid,), width="stretch")


def _type_screen(s: AppState) -> None:
    mtype = repo.meeting_types()[s.folder_type]
    mine_ids = sel.folder_ids_of_type(s, s.folder_type)
    own = [k for k in mine_ids if sel.folders(s)[k].host == ME]
    member = [k for k in mine_ids if sel.folders(s)[k].host != ME]
    shown = {"mine": own, "member": member}.get(s.folder_owner_filter, mine_ids)

    with st.container(horizontal=True, vertical_alignment="bottom"):
        page_header(mtype.name, f"{mtype.meta} · 큰 주제별로 폴더에 쌓여요")
        button("회의록 올리기", key="type_upload", variant="primary", on_click=actions.open_new_note)
    button_group(
        [("all", f"전체 {len(mine_ids)}"), ("mine", f"내가 만든 폴더 {len(own)}"), ("member", f"멤버 {len(member)}")],
        s.folder_owner_filter,
        lambda v: actions.update(folder_owner_filter=v),
        key="owner",
    )
    if shown:
        with st.container(key="topic-grid", gap="small"):  # 카드가 많아지면 이 영역만 스크롤
            for start in range(0, len(shown), 3):
                cols = st.columns(3, gap="small")
                for col, fid in zip(cols, shown[start:start + 3]):
                    with col:
                        _topic_card(s, fid)
    else:
        empty_state(_OWNER_EMPTY.get(s.folder_owner_filter, _OWNER_EMPTY["all"]), pad=40)
    markup(
        f'<span class="lock-note">{icon("lock", 12, "#80858E", 2)}'
        f"내가 멤버인 폴더만 보여요 · 멤버가 아닌 폴더 {mtype.hidden_count}개는 보이지 않아요</span>"
    )


# ================================================================ 폴더 화면
def _empty_texts(tab: str, role: str) -> tuple[str, str]:
    tab_name = {"att": "참석한 회의", "abs": "미참석 회의"}.get(tab, "이 폴더")
    if role == "host":
        return "내가 주관한 회의가 없어요", (
            "미참석 회의는 내가 주관할 수 없어서 항상 비어 있어요." if tab == "abs"
            else f"{tab_name} 중에 내가 주관한 회의는 아직 없어요.")
    if role == "att":
        return "참석으로 분류된 회의가 없어요", (
            "미참석 회의는 참석 회의와 겹치지 않아서 항상 비어 있어요." if tab == "abs"
            else f"{tab_name} 중에 다른 사람이 주관하고 내가 참석한 회의가 없어요.")
    if tab == "abs":
        return "빠진 회차가 없어요", "이 폴더의 모든 회의에 참석했어요."
    return "아직 회의록이 없어요", "이 주제로 회의를 하면 여기에 쌓여요."


def _folder_wiki(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    graph = repo.folder_wiki()
    if not (fid == graph.extra.get("folder") and len(f.notes) >= FOLDER_WIKI_MIN_NOTES):
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 관계도를 만들 만큼 회의가 쌓이지 않았어요</span>'
            f'<span class="empty-text sub">회의록이 {FOLDER_WIKI_MIN_NOTES}개 이상 쌓이면 사람·주제·결정을 잇는 위키가 만들어져요 · 지금 {len(f.notes)}개</span></div>'
        )
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        markup(f'<p class="hint" style="font-size:13px;color:#5F646D">이 폴더 회의록 {len(f.notes)}개로 만들어져요 · 내가 빠진 회차도 들어가요 · 멤버가 아니면 볼 수 없어요</p>')
        button("처음 보는 멤버용 요약", key="toggle_brief", variant="secondary", on_click=actions.toggle, args=("show_brief",))
    if s.show_brief:
        markup('<div class="brief">' + "".join(f"<div><b>{esc(b.label)}</b><span>{esc(b.text)}</span></div>" for b in f.brief) + "</div>")

    selected = s.folder_wiki_node if s.folder_wiki_node in graph.nodes else "c"
    near = graph.neighbors(selected)
    members = sel.members_of(s, fid)
    opacity = {}
    for nid, n in graph.nodes.items():
        gone = n.kind == "person" and n.person not in members
        opacity[nid] = 0.12 if gone else (1.0 if nid == selected or nid in near else 0.35)
    avatars = {nid: Avatar(n.initial, n.person == ME) for nid, n in graph.nodes.items() if n.kind == "person"}
    legend = (
        '<span><i style="width:14px;height:10px;border-radius:3px;background:#2B2D33"></i>이 폴더</span>'
        '<span><i style="width:14px;height:10px;border-radius:3px;border:1.5px solid #F37321"></i>논의 주제</span>'
        '<span><i style="width:10px;height:10px;border-radius:50%;background:#F1F2F4;border:1px solid #D5D8DD"></i>멤버</span>'
        '<span><i style="width:14px;height:10px;border-radius:2px;border:1.5px dashed #2F538F"></i>결정</span>'
    )
    timeline = list(graph.timeline.get(selected, ()))
    if s.branched:
        timeline += list(graph.extra.get("branch_timeline", {}).get(selected, ()))
    node = graph.nodes[selected]
    kind_label = {"project": "이 폴더", "topic": "논의 주제", "person": "멤버", "decision": "결정"}[node.kind]

    left, right = st.columns([2.2, 1], gap="medium")
    with left:
        knowledge_graph(graph, scope="folder", selected=selected,
                        on_select=lambda nid: actions.update(folder_wiki_node=nid),
                        height="max(320px, calc(100vh - 540px))", opacity=opacity, avatars=avatars, legend_html=legend)
    with right:
        entity_panel(
            scope="folder",
            kind_label=kind_label,
            kind_color=KIND_COLORS[node.kind],
            title=node.label,
            desc=node.desc,
            timeline=[
                TimelineEntry(t.when, t.text, sel.source_label(t.meeting, t.src), t.meeting, t.src,
                              absent=ME not in repo.meeting(t.meeting).people)
                for t in timeline
            ],
            links=[(nid, graph.nodes[nid].label, rel) for nid, rel in near.items()],
            on_link=lambda nid: actions.update(folder_wiki_node=nid),
            on_source=actions.open_source,
        )


def _folder_screen(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    members = sel.members_of(s, fid)
    is_host = f.host == ME
    notes = sel.by_date_desc(list(f.notes))
    att = [i for i in notes if ME in repo.meeting(i).people]
    abs_ = [i for i in notes if ME not in repo.meeting(i).people]
    base = {"att": att, "abs": abs_}.get(s.folder_tab, notes)
    host_ids = [i for i in base if sel.my_role(repo.meeting(i)) == "host"]
    att_ids = [i for i in base if sel.my_role(repo.meeting(i)) == "att"]
    shown = {"host": host_ids, "att": att_ids}.get(s.folder_role, base)

    with st.container(horizontal=True, vertical_alignment="top"):
        with st.container(gap="small"):
            markup(f'<div style="display:flex;align-items:center;gap:10px"><h1 class="section-title" style="font-size:20px">{esc(f.name)}</h1>'
                   f'{tag("내가 만든 폴더" if is_host else "멤버", "host" if is_host else "att")}</div>')
            member_pills(f"멤버 {len(members)}명", f.host, members)
        with st.container(horizontal=True, width="content", gap="small"):
            button("멤버 관리" if is_host else "멤버 보기", key="open_members", variant="secondary",
                   on_click=actions.open_modal, args=("members",))
            if is_host:
                button("폴더 삭제", key="open_delete", variant="dangerline", on_click=actions.open_modal, args=("delete_folder",))
            button("회의록 올리기", key="folder_upload", variant="primary", on_click=actions.open_new_note)

    button_group(
        [("all", f"전체 {len(notes)}"), ("att", f"참석 {len(att)}"), ("abs", f"미참석 {len(abs_)}"), ("wiki", "위키")],
        s.folder_tab,
        lambda v: actions.update(folder_tab=v, folder_role="all"),
        key="ftab",
        style="tab",
    )
    if s.folder_tab == "wiki":
        _folder_wiki(s, fid)
        return

    button_group(
        [("all", f"전체 {len(base)}"), ("host", f"내가 주관 {len(host_ids)}"), ("att", f"참석 {len(att_ids)}")],
        s.folder_role,
        lambda v: actions.update(folder_role=v),
        key="frole",
    )
    if shown:
        meeting_table([sel.meeting_row(s, i) for i in shown], key="folder")
    else:
        title, text = _empty_texts(s.folder_tab, s.folder_role)
        empty_state(text, title=title)


def render() -> None:
    s = get_state()
    fid = s.folder_id if s.folder_id in sel.folder_ids_of_type(s, s.folder_type) else "all"
    with page("folder"):
        if fid == "all":
            _type_screen(s)
        else:
            _folder_screen(s, fid)
