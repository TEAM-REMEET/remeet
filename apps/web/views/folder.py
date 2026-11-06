"""폴더 화면: 멤버 · 멤버 관리/폴더 삭제/회의록 올리기 · 탭(회의록/위키) · 내 할 일별로 나눈 회의 목록.

프로젝트 폴더(이 파일의 render)와 기간 폴더(views/archive.py 의 10월 4주차 같은 화면)가
같은 모양을 쓰도록 render_folder_page(FolderPage) 하나로 그립니다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import streamlit as st

from components.knowledge_graph import (
    KIND_COLORS,
    Avatar,
    TimelineEntry,
    entity_panel,
    knowledge_graph,
)
from components.meeting_table import meeting_sections
from components.member_pills import member_pills
from state.store import me_id
from config import FOLDER_WIKI_MIN_NOTES
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page
from ui.tags import tag
from utils.text import esc


# ================================================================ 폴더 화면
def _empty_texts(role: str) -> tuple[str, str]:
    if role == "host":
        return "내가 주관한 회의가 없어요", "이 폴더에서 내가 주관한 회의는 아직 없어요."
    if role == "att":
        return "참석으로 분류된 회의가 없어요", "이 폴더에서 다른 사람이 주관하고 내가 참석한 회의가 없어요."
    return "아직 회의록이 없어요", "이 주제로 회의를 하면 여기에 쌓여요."


def _folder_wiki(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    graph = repo.folder_wiki()
    if len(f.notes) < FOLDER_WIKI_MIN_NOTES:  # 회의록이 모자람
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 관계도를 만들 회의록이 없어요</span>'
            f'<span class="empty-text sub">회의록이 {FOLDER_WIKI_MIN_NOTES}개 이상 있으면 사람·주제·결정을 잇는 위키가 만들어져요 · 지금 {len(f.notes)}개</span></div>'
        )
        return
    if fid != graph.extra.get("folder"):  # 회의록은 충분한데 이 폴더의 그래프 데이터가 아직 없음 (목업은 한 폴더만 있음)
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">이 폴더의 관계도를 아직 만들지 못했어요</span>'
            '<span class="empty-text sub">폴더의 그래프 데이터가 생성되면 위키를 만들 예정이에요</span></div>'
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
    avatars = {nid: Avatar(n.initial, n.person == me_id()) for nid, n in graph.nodes.items() if n.kind == "person"}
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
                              absent=me_id() not in repo.meeting(t.meeting).people)
                for t in timeline
            ],
            links=[(nid, graph.nodes[nid].label, rel) for nid, rel in near.items()],
            on_link=lambda nid: actions.update(folder_wiki_node=nid),
            on_source=actions.open_source,
        )


_VISIBILITY_HELP = (
    "내가 주관하거나 참석한 회의만 보여요.",
    "빠진 회의(조퇴·불참)는 주관자가 공유해 주면",
    "「미참석 · 공유받음」으로 떠요.",
)


def info_tip(lines: tuple[str, ...]) -> str:
    """ⓘ 아이콘: 마우스를 올리거나(포커스) 누르면 아이콘 왼쪽에 말풍선으로 안내 (줄마다 끊어 보여 줌)."""
    body = "<br>".join(esc(line) for line in lines)
    return (f'<span class="info-tip" tabindex="0" role="button" aria-label="{esc(" ".join(lines))}">'
            f'<span class="i">i</span><span class="bubble" role="tooltip">{body}</span></span>')


@dataclass
class FolderPage:
    """프로젝트 화면 한 장의 내용."""

    key: str  # 위젯 key 접두어
    title: str
    host: str  # 멤버 알약에서 '주관'으로 표시할 사람
    is_host: bool  # 내가 만든 폴더인지 → 멤버 관리 / 폴더 삭제
    members: list[str]
    notes: list[str]  # 나에게 보이는 회의 (최근 순)
    wiki_folders: list[str]  # 위키 탭에서 보여 줄 프로젝트 폴더
    can_manage: bool | None = None  # 멤버 관리 가능 여부 (None 이면 is_host 와 같음)
    on_members: tuple[Callable, tuple] = (actions.open_members, ())
    on_delete: tuple[Callable, tuple] = (actions.open_delete, ())
    extra_tags: list[str] = field(default_factory=list)


def _wiki_tab(s: AppState, fp: FolderPage) -> None:
    fs = sel.folders(s)
    fids = [k for k in fp.wiki_folders if k in fs]
    if not fids:
        empty_state("아직 위키를 만들 회의록이 없어요.", pad=40)
        return
    _folder_wiki(s, fids[0])


def render_folder_page(s: AppState, fp: FolderPage) -> None:
    host_ids = [i for i in fp.notes if sel.my_role(repo.meeting(i)) == "host"]
    att_ids = [i for i in fp.notes if sel.my_role(repo.meeting(i)) == "att"]
    shown = {"host": host_ids, "att": att_ids}.get(s.folder_role, fp.notes)

    with st.container(horizontal=True, vertical_alignment="top"):
        with st.container(gap="small"):
            tags = "".join([tag("내가 만든 폴더" if fp.is_host else "멤버", "host" if fp.is_host else "att"), *fp.extra_tags])
            markup(f'<div style="display:flex;align-items:center;gap:10px"><h1 class="section-title" style="font-size:20px">'
                   f'{esc(fp.title)}</h1>{tags}</div>')
            member_pills(f"멤버 {len(fp.members)}명", fp.host, fp.members)
        with st.container(horizontal=True, width="content", gap="small"):
            fn, args = fp.on_members
            manage = fp.is_host if fp.can_manage is None else fp.can_manage
            button("멤버 관리" if manage else "멤버 보기", key="open_members", variant="secondary",
                   on_click=fn, args=args)
            if fp.is_host:
                fn, args = fp.on_delete
                button("폴더 삭제", key="open_delete", variant="dangerline", on_click=fn, args=args)
            button("회의록 올리기", key="folder_upload", variant="primary", on_click=actions.open_new_note)

    button_group(
        [("all", f"회의록 {len(fp.notes)}"), ("wiki", "위키")],
        s.folder_tab if s.folder_tab in ("all", "wiki") else "all",
        lambda v: actions.update(folder_tab=v, folder_role="all"),
        key="ftab",
        style="tab",
    )
    if s.folder_tab == "wiki":
        _wiki_tab(s, fp)
        return

    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        button_group(
            [("all", f"전체 {len(fp.notes)}"), ("host", f"내가 주관 {len(host_ids)}"), ("att", f"참석 {len(att_ids)}")],
            s.folder_role,
            lambda v: actions.update(folder_role=v),
            key="frole",
        )
        markup(info_tip(_VISIBILITY_HELP))
    if shown:
        meeting_sections(s, shown, key=fp.key)
    else:
        title, text = _empty_texts(s.folder_role)
        empty_state(text, title=title)


def _folder_screen(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    render_folder_page(s, FolderPage(
        key="folder",
        title=f.name,
        host=f.host,
        is_host=f.host == me_id(),
        members=sel.members_of(s, fid),
        notes=sel.folder_note_ids(s, fid),  # 참석·주관한 회의 + 빠졌지만 주관자가 공유해 준 회의
        wiki_folders=[fid],
        on_members=(actions.open_members, (fid,)),
        on_delete=(actions.open_delete, ("folder", fid)),
    ))


def render() -> None:
    s = get_state()
    with page("folder"):
        if s.folder_id in sel.all_my_folder_ids(s):
            _folder_screen(s, s.folder_id)
        else:
            empty_state("이 폴더를 볼 수 없어요. 삭제됐거나 멤버가 아닌 폴더예요.", title="폴더를 찾을 수 없어요", pad=48)
