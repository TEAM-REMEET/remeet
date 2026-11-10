"""폴더 화면: 멤버 · 멤버 관리/회의록 올리기 · 탭(회의록/위키) · 내 할 일별로 나눈 회의 목록.

프로젝트 폴더(이 파일의 render)와 기간 폴더(views/archive.py 의 10월 4주차 같은 화면)가
같은 모양을 쓰도록 render_folder_page(FolderPage) 하나로 그립니다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import streamlit as st

from components.folder_tree import folder_tree
from components.meeting_table import meeting_table
from components.member_pills import member_pills
from state.store import me_id
from config import FOLDER_WIKI_MIN_NOTES
from data import api
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page
from ui.tags import tag
from utils.text import esc, safe_key


# ================================================================ 폴더 화면
def _empty_texts(role: str) -> tuple[str, str]:
    if role == "todo":
        return "내 할 일이 있는 회의가 없어요", "이 폴더에서 나에게 맡겨진 할 일이 있는 회의는 없어요."
    if role == "host":
        return "내가 주관한 회의가 없어요", "이 폴더에서 내가 주관한 회의는 아직 없어요."
    if role == "att":
        return "참석으로 분류된 회의가 없어요", "이 폴더에서 다른 사람이 주관하고 내가 참석한 회의가 없어요."
    return "아직 회의록이 없어요", "이 주제로 회의를 하면 여기에 쌓여요."


# ---------------------------------------------------------------- 위키 (그래프 · 미해결 이슈)
_BOARD = (  # status → (칸 이름, 설명). 판정은 백엔드 application/folder_wiki
    ("split", "의견이 갈림", "반대·우려가 나왔는데 결정 없음"),
    ("open", "정해지지 않음", "미해결 이슈 · 보류 · 결정 없음"),
    ("done", "정리됨", "최근 안건에 결정, 남은 이슈 없음"),
)
def _chip(label: str, cls: str) -> str:
    return f'<span class="wk-chip {cls}">{esc(label)}</span>'


def _short(text: str, n: int = 14) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _src(rounds: list[dict]) -> tuple[str, ...]:
    """근거 발화 번호 → 원문 패널 id (state.summary_view._src 와 같은 p{n})."""
    return tuple(dict.fromkeys(f"p{i}" for r in rounds for ln in r["lines"] for i in ln["src"]))


def _board_card(t: dict, meetings: list[dict]) -> None:
    r = t["rounds"][-1]
    m = next(x for x in meetings if x["meeting_id"] == r["meeting_id"])
    seen = {x["meeting_id"] for x in t["rounds"]}
    bar = "".join(f'<i class="{"x" if x["meeting_id"] in seen else ""}"></i>' for x in meetings)
    if t["status"] == "done":
        groups = [("결정", "wk-decchip", [d["text"] for d in r["decisions"]])]
    else:
        groups = [("우려", "wk-con", r["concerns"]), ("보류", "wk-hold", r["holds"]), ("미해결", "wk-open", r["unresolved"])]
    # 종류마다 라벨 한 번 + 그 아래 문장들 (긴 문장이 라벨 옆으로 밀려 들여쓰기처럼 보이지 않게)
    body = "".join(f'<div class="wk-group">{_chip(label, cls)}{"".join(f"<p>{esc(x)}</p>" for x in items)}</div>'
                   for label, cls, items in groups if items) or '<p class="wk-none">결정이나 남은 이슈 없이 논의만 했어요</p>'
    prev = [d["text"] for x in t["rounds"][:-1] for d in x["decisions"] if not d["superseded_by"]]
    foot = ([f"<span><b>이전 결정</b> {esc(_short(prev[-1], 24))}</span>"] if t["status"] != "done" and prev else []) + (
        [f"<span><b>다음 회의</b> {esc(_short(r['next_topics'][0], 24))}</span>"] if r["next_topics"] else [])
    with st.container(key=f"wkcard-{safe_key(t['topic_id'])}", gap="small"):
        markup(f'<div class="wk-card-top"><b>{esc(t["title"])}</b>'
               f'<span class="wk-bar" title="이 폴더 회의 {len(meetings)}개 중 {len(seen)}개에서 다룸">{bar}</span></div>'
               f'<div class="wk-meta">마지막 논의 {esc(m["date"][5:].replace("-", "."))} · {esc(m["title"])}</div>'
               f'<div class="wk-body">{body}</div>'
               + (f'<div class="wk-foot">{"".join(foot)}</div>' if foot else ""))
        src = _src([r])
        if src:
            button("근거 보기 →", key=f"wkcsrc_{safe_key(t['topic_id'])}", variant="link", on_click=actions.open_source, args=(r["meeting_id"], src))


def _wiki_board(topics: list[dict], meetings: list[dict]) -> None:
    topics = [t for t in topics if not t["misc"]]  # 「기타」 주제는 보드에서 뺌
    for col, (status, label, sub) in zip(st.columns(3, gap="medium"), _BOARD):
        items = [t for t in topics if t["status"] == status]
        with col, st.container(key=f"wkcol-{status}", gap="small"):
            markup(f'<div class="wk-col-hd"><div><b>{label}</b><small>{sub}</small></div><span class="wk-n">{len(items)}</span></div>')
            if not items:
                markup('<p class="wk-none">해당하는 주제가 없어요</p>')
            for t in items:
                _board_card(t, meetings)


def _wiki_data(s: AppState, fid: str) -> dict | None:
    """미해결 이슈 보드 데이터. 없거나 실패하면 빈 화면을 그리고 None."""
    f = sel.folders(s)[fid]
    if len(f.notes) < FOLDER_WIKI_MIN_NOTES:  # 회의록이 모자람
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 위키를 만들 회의록이 없어요</span>'
            f'<span class="empty-text sub">회의록이 {FOLDER_WIKI_MIN_NOTES}개 이상 있으면 주제별 미해결 이슈가 정리돼요 · 지금 {len(f.notes)}개</span></div>'
        )
        return None
    try:
        wiki = repo.folder_wiki(fid)
    except api.ApiError as e:
        empty_state(str(e), title="위키를 불러오지 못했어요", pad=48)
        return None
    if not wiki["topics"]:  # 회의록은 있지만 확정 · 적재된 회의가 아직 없음
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 정리된 주제가 없어요</span>'
            '<span class="empty-text sub">회의록을 확정하면 주제 · 결정 · 미해결 이슈가 여기에 쌓여요</span></div>'
        )
        return None
    markup(f'<p class="hint" style="font-size:13px;color:#5F646D">이 폴더 회의 {len(wiki["meetings"])}개 · 주제 {len(wiki["topics"])}개 · 내가 빠진 회차도 들어가요</p>')
    return wiki


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
    is_host: bool  # 내가 만든 폴더인지 → 멤버 관리
    members: list[str]
    notes: list[str]  # 나에게 보이는 회의 (최근 순)
    wiki_folders: list[str]  # 위키 탭에서 보여 줄 프로젝트 폴더
    can_manage: bool | None = None  # 멤버 관리 가능 여부 (None 이면 is_host 와 같음)
    on_members: tuple[Callable, tuple] = (actions.open_members, ())
    on_delete: tuple[Callable, tuple] | None = None  # 회의가 하나도 없는 내 프로젝트만 (서버가 정한 Folder.can_delete)
    extra_tags: list[str] = field(default_factory=list)


def _wiki_graph(fid: str, root: str) -> None:
    try:
        data = repo.folder_graph(fid)
    except api.ApiError as e:
        empty_state(str(e), title="그래프를 불러오지 못했어요", pad=48)
        return
    if not data["nodes"]:
        empty_state("회의록을 확정하면 회의 · 안건 · 주제 · 할 일이 이어진 그래프가 여기에 그려져요.", title="아직 그래프가 없어요", pad=48)
        return
    folder_tree(data, root=root)


def _wiki_tab(s: AppState, fp: FolderPage) -> None:
    """위키 탭: 그래프 | 미해결 이슈."""
    fids = [k for k in fp.wiki_folders if k in sel.folders(s)]
    if not fids:
        empty_state("아직 위키를 만들 회의록이 없어요.", pad=40)
        return
    view = "board" if s.wiki_view == "board" else "graph"
    button_group([("graph", "그래프"), ("board", "미해결 이슈")], view,
                 lambda v: actions.update(wiki_view=v), key="wkview", style="seg")
    if view != "board":
        _wiki_graph(fids[0], sel.folders(s)[fids[0]].name)
    elif wiki := _wiki_data(s, fids[0]):
        _wiki_board(wiki["topics"], wiki["meetings"])


def render_folder_page(s: AppState, fp: FolderPage) -> None:
    host_ids = [i for i in fp.notes if sel.my_role(repo.meeting(i)) == "host"]
    att_ids = [i for i in fp.notes if sel.my_role(repo.meeting(i)) == "att"]
    todo_ids = [i for i in fp.notes if sel.my_todo_status(s, i) == "left"]
    shown = {"todo": todo_ids, "host": host_ids, "att": att_ids}.get(s.folder_role, fp.notes)

    with st.container(horizontal=True, vertical_alignment="top"):
        with st.container(gap="small"):
            tags = "".join([tag("내가 만든 폴더" if fp.is_host else "멤버", "host" if fp.is_host else "att"), *fp.extra_tags])
            markup(f'<div style="display:flex;align-items:center;gap:10px"><h1 class="section-title" style="font-size:20px">'
                   f'{esc(fp.title)}</h1>{tags}</div>')
            member_pills(f"멤버 {len(fp.members)}명", None, fp.members)  # 프로젝트엔 주관자 표시 없음 (만든 사람은 folders.host_user_id 로만 저장)
        with st.container(horizontal=True, width="content", gap="small"):
            fn, args = fp.on_members
            manage = fp.is_host if fp.can_manage is None else fp.can_manage
            button("멤버 관리" if manage else "멤버 보기", key="open_members", variant="secondary",
                   on_click=fn, args=args)
            if fp.on_delete:
                fn, args = fp.on_delete
                button("폴더 삭제", key="open_delete", variant="dangerline", icon=":material/delete:", on_click=fn, args=args)
            button("회의록 올리기", key="folder_upload", variant="primary", on_click=actions.open_new_note)

    button_group(
        [("all", f"회의록 {len(fp.notes)}"), ("wiki", "RE:MAP")],
        s.folder_tab if s.folder_tab in ("all", "wiki") else "all",
        lambda v: actions.update(folder_tab=v, folder_role="todo"),
        key="ftab",
        style="tab",
    )
    if s.folder_tab == "wiki":
        _wiki_tab(s, fp)
        return

    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        button_group(
            [("all", f"전체 {len(fp.notes)}"), ("todo", f"내 할 일이 있는 회의 {len(todo_ids)}"),
             ("host", f"내가 주관 {len(host_ids)}"), ("att", f"참석 {len(att_ids)}")],
            s.folder_role,
            lambda v: actions.update(folder_role=v),
            key="frole",
        )
        markup(info_tip(_VISIBILITY_HELP))
    if shown:
        meeting_table([sel.meeting_row(s, i) for i in shown], key=f"{fp.key}-{s.folder_role}")
    else:
        title, text = _empty_texts(s.folder_role)
        empty_state(text, title=title)


def _folder_screen(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    render_folder_page(s, FolderPage(
        key="folder",
        title=f.name,
        is_host=f.host == me_id(),
        members=sel.members_of(s, fid),
        notes=sel.folder_note_ids(s, fid),  # 참석·주관한 회의 + 빠졌지만 주관자가 공유해 준 회의
        wiki_folders=[fid],
        on_members=(actions.open_members, (fid,)),
        on_delete=(actions.open_delete, ("folder", fid)) if f.can_delete else None,
    ))


def render() -> None:
    s = get_state()
    with page("folder"):
        if s.folder_id in sel.all_my_folder_ids(s):
            _folder_screen(s, s.folder_id)
        else:
            empty_state("이 폴더를 볼 수 없어요. 삭제됐거나 멤버가 아닌 폴더예요.", title="폴더를 찾을 수 없어요", pad=48)
