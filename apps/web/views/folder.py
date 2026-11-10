"""폴더 화면: 멤버 · 멤버 관리/폴더 삭제/회의록 올리기 · 탭(회의록/위키) · 내 할 일별로 나눈 회의 목록.

프로젝트 폴더(이 파일의 render)와 기간 폴더(views/archive.py 의 10월 4주차 같은 화면)가
같은 모양을 쓰도록 render_folder_page(FolderPage) 하나로 그립니다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import streamlit as st

from components.folder_graph import folder_graph
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


# ---------------------------------------------------------------- 위키 (결정 흐름 · 미해결 이슈)
_BOARD = (  # status → (칸 이름, 설명). 판정은 백엔드 application/folder_wiki
    ("split", "의견이 갈림", "반대·우려가 나왔는데 결정 없음"),
    ("open", "정해지지 않음", "미해결 이슈 · 보류 · 결정 없음"),
    ("done", "정리됨", "최근 안건에 결정, 남은 이슈 없음"),
)
_STATUS_TAG = {"split": ("의견이 갈림", "wk-con"), "open": ("정해지지 않음", "wk-open"), "done": ("정리됨", "wk-ok")}
_POINT_CLASS = {"제안": "wk-prop", "반대·우려": "wk-con", "질문": "wk-ask", "정보": "wk-gray"}
_FOLD_OVER, _FOLD_SHOW = 10, 8  # 주제가 10개를 넘으면 위에서 8개만 펼쳐 둠
# shortcut: 결정 흐름은 최근 회의 8개 칸만 그림. 오래된 회의까지 봐야 하면 가로 스크롤 표로 바꾸기
_MAX_COLS = 8


def _chip(label: str, cls: str) -> str:
    return f'<span class="wk-chip {cls}">{esc(label)}</span>'


def _short(text: str, n: int = 14) -> str:
    return text if len(text) <= n else text[:n] + "…"


def _src(rounds: list[dict]) -> tuple[str, ...]:
    """근거 발화 번호 → 원문 패널 id (state.summary_view._src 와 같은 p{n})."""
    return tuple(dict.fromkeys(f"p{i}" for r in rounds for ln in r["lines"] for i in ln["src"]))


def _cell(r: dict | None) -> str:
    if r is None:
        return ""
    parts = []
    if r["decisions"]:
        d = r["decisions"][-1]
        more = f" +{len(r['decisions']) - 1}" if len(r["decisions"]) > 1 else ""
        if d["superseded_by"]:
            parts.append(f'<span class="wk-dec old" title="이후 바뀜: {esc(d["superseded_by"])}">{esc(_short(d["text"]))}</span>')
        else:
            parts.append(f'<span class="wk-dec" title="{esc(d["text"])}">{esc(_short(d["text"]))}{more}</span>')
    if r["holds"]:
        parts.append(_chip("보류", "wk-hold"))
    if r["concerns"]:
        parts.append(_chip("이견", "wk-con"))
    if r["unresolved"]:
        parts.append(_chip(f"미해결 {len(r['unresolved'])}", "wk-open"))
    return "".join(parts) or '<span class="wk-dot"></span>'


def _flow_row(t: dict, cols: list[dict]) -> str:
    by_meeting = {r["meeting_id"]: r for r in t["rounds"]}
    hit = [i for i, m in enumerate(cols) if m["meeting_id"] in by_meeting]
    cells = []
    for i, m in enumerate(cols):
        line = ""
        if hit and hit[0] <= i <= hit[-1] and len(hit) > 1:  # 처음 다룬 회의 ~ 마지막 회의를 잇는 선
            line = " on" + (" first" if i == hit[0] else "") + (" last" if i == hit[-1] else "")
        cells.append(f'<div class="wk-cell{line}"><div class="wk-marks">{_cell(by_meeting.get(m["meeting_id"]))}</div></div>')
    return f'<div class="wk-grid" style="--cols:{len(cols)}">{"".join(cells)}</div>'


def _flow_detail(t: dict, meetings: dict[str, dict]) -> None:
    label, cls = _STATUS_TAG[t["status"]]
    with st.container(key="wkdetail", gap="small"):
        markup(f'<div class="wk-detail-head"><h3>{esc(t["title"])}</h3>{_chip(label, cls)}</div>')
        for k, r in enumerate(t["rounds"]):
            m = meetings[r["meeting_id"]]
            replaced = {d["text"]: d["superseded_by"] for d in r["decisions"] if d["superseded_by"]}
            rows = []
            for ln in r["lines"]:
                chips = (_chip(ln["tag"], _POINT_CLASS.get(ln["tag"], "wk-gray")) if ln["tag"] else "") + (
                    _chip(ln["decision_tag"], "wk-hold" if ln["decision_tag"] == "보류" else "wk-decchip") if ln["decision_tag"] else "")
                after = f'<span class="wk-after">→ 이후 바뀜: {esc(replaced[ln["text"]])}</span>' if ln["text"] in replaced else ""
                rows.append(f"<li>{chips}<span>{esc(ln['text'])}{after}</span></li>")
            rows += [f'<li>{_chip("미해결", "wk-open")}<span>{esc(u)}</span></li>' for u in r["unresolved"]]
            rows += [f'<li>{_chip("다음 회의", "wk-gray")}<span>{esc(n)}</span></li>' for n in r["next_topics"]]
            with st.container(horizontal=True, vertical_alignment="top", gap="small", key=f"wkround-{k}"):
                markup(f'<div class="wk-when"><b>{esc(m["date"][5:].replace("-", "."))}</b>{esc(m["title"])}</div>'
                       f'<ul class="wk-lines">{"".join(rows) or "<li><span>정리된 문장이 없어요</span></li>"}</ul>')
                src = _src([r])
                if src:
                    button("근거 보기", key=f"wksrc_{k}", variant="src", on_click=actions.open_source, args=(r["meeting_id"], src))


def _wiki_flow(s: AppState, topics: list[dict], meetings: list[dict]) -> None:
    cols = meetings[-_MAX_COLS:]
    folded = len(topics) > _FOLD_OVER and not s.wiki_more
    shown = topics[:_FOLD_SHOW] if folded else topics
    ids = [t["topic_id"] for t in topics]
    selected = s.wiki_topic if s.wiki_topic in ids else ids[0]
    if selected not in [t["topic_id"] for t in shown]:  # 고른 주제가 접힌 쪽에 있으면 펼쳐서 보여 줌
        shown = topics

    with st.container(key="wkflow", gap=None):
        head = "".join(f'<div class="wk-hd"><b>{esc(m["date"][5:].replace("-", "."))}</b>{esc(_short(m["title"], 10))}</div>' for m in cols)
        with st.container(horizontal=True, gap="small", key="wkhead"):
            older = f"이전 회의 {len(meetings) - len(cols)}개 생략" if len(meetings) > len(cols) else "주제"
            markup(f'<div class="wk-lane-hd">{older}</div>')
            markup(f'<div class="wk-grid" style="--cols:{len(cols)}">{head}</div>')
        for t in shown:
            on = "on" if t["topic_id"] == selected else "off"
            with st.container(horizontal=True, gap="small", vertical_alignment="center", key=f"wkrow-{on}-{safe_key(t['topic_id'])}"):
                button(t["title"], key=f"wktopic_{safe_key(t['topic_id'])}", variant="link",
                       on_click=actions.update, kwargs={"wiki_topic": t["topic_id"]})
                markup(_flow_row(t, cols))
        if folded:
            rest = topics[_FOLD_SHOW:]
            with st.container(horizontal=True, gap="small", vertical_alignment="center", key="wkfold"):
                button(f"▸ 나머지 {len(rest)}개 주제 펼치기", key="wkmore", variant="link", on_click=actions.update, kwargs={"wiki_more": True})
                markup(f'<span class="wk-rest">{esc(" · ".join(t["title"] for t in rest))}</span>')
        elif len(topics) > _FOLD_OVER:
            button("▴ 접기", key="wkless", variant="link", on_click=actions.update, kwargs={"wiki_more": False})
    markup('<div class="wk-legend"><span><span class="wk-dot"></span>논의만</span>'
           '<span><span class="wk-dec">결정</span></span><span><span class="wk-dec old">예전 결정</span>이후 바뀜</span>'
           f'<span>{_chip("이견", "wk-con")}반대·우려</span><span>{_chip("보류", "wk-hold")}</span>'
           f'<span>{_chip("미해결 n", "wk-open")}</span><span>주제를 누르면 회차별 내용이 아래에 펼쳐져요</span></div>')
    _flow_detail(next(t for t in topics if t["topic_id"] == selected), {m["meeting_id"]: m for m in meetings})


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


def _folder_wiki(s: AppState, fid: str) -> None:
    f = sel.folders(s)[fid]
    if len(f.notes) < FOLDER_WIKI_MIN_NOTES:  # 회의록이 모자람
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 위키를 만들 회의록이 없어요</span>'
            f'<span class="empty-text sub">회의록이 {FOLDER_WIKI_MIN_NOTES}개 이상 있으면 주제별 결정 흐름이 만들어져요 · 지금 {len(f.notes)}개</span></div>'
        )
        return
    try:
        wiki = repo.folder_wiki(fid)
    except api.ApiError as e:
        empty_state(str(e), title="위키를 불러오지 못했어요", pad=48)
        return
    if not wiki["topics"]:  # 회의록은 있지만 확정 · 적재된 회의가 아직 없음
        markup(
            f'<div class="empty" style="padding:48px 24px">{icon("wiki", 28, "#F37321")}'
            '<span class="empty-title">아직 정리된 주제가 없어요</span>'
            '<span class="empty-text sub">회의록을 확정하면 주제 · 결정 · 미해결 이슈가 여기에 쌓여요</span></div>'
        )
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        button_group([("flow", "결정 흐름"), ("board", "미해결 이슈")], s.wiki_view,
                     lambda v: actions.update(wiki_view=v), key="wkview", style="seg")
        markup(f'<p class="hint" style="font-size:13px;color:#5F646D">이 폴더 회의 {len(wiki["meetings"])}개 · 주제 {len(wiki["topics"])}개 · 내가 빠진 회차도 들어가요</p>')
    if s.wiki_view == "board":
        _wiki_board(wiki["topics"], wiki["meetings"])
    else:
        _wiki_flow(s, wiki["topics"], wiki["meetings"])


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
    is_host: bool  # 내가 만든 폴더인지 → 멤버 관리 / 폴더 삭제
    members: list[str]
    notes: list[str]  # 나에게 보이는 회의 (최근 순)
    wiki_folders: list[str]  # 위키 탭에서 보여 줄 프로젝트 폴더
    can_manage: bool | None = None  # 멤버 관리 가능 여부 (None 이면 is_host 와 같음)
    on_members: tuple[Callable, tuple] = (actions.open_members, ())
    on_delete: tuple[Callable, tuple] = (actions.open_delete, ())
    extra_tags: list[str] = field(default_factory=list)


def _graph_tab(fp: FolderPage) -> None:
    fids = [k for k in fp.wiki_folders if k in sel.folders(get_state())]
    try:
        data = repo.folder_graph(fids[0]) if fids else {"nodes": []}
    except api.ApiError as e:
        empty_state(str(e), title="그래프를 불러오지 못했어요", pad=48)
        return
    if not data["nodes"]:
        empty_state("회의록을 확정하면 회의 · 안건 · 주제 · 할 일이 이어진 그래프가 여기에 그려져요.", title="아직 그래프가 없어요", pad=48)
        return
    folder_graph(data)


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
            if fp.is_host:
                fn, args = fp.on_delete
                button("폴더 삭제", key="open_delete", variant="dangerline", icon=":material/delete:", on_click=fn, args=args)
            button("회의록 올리기", key="folder_upload", variant="primary", on_click=actions.open_new_note)

    button_group(
        [("all", f"회의록 {len(fp.notes)}"), ("wiki", "위키"), ("graph", "그래프")],
        s.folder_tab if s.folder_tab in ("all", "wiki", "graph") else "all",
        lambda v: actions.update(folder_tab=v, folder_role="todo"),
        key="ftab",
        style="tab",
    )
    if s.folder_tab == "wiki":
        _wiki_tab(s, fp)
        return
    if s.folder_tab == "graph":
        _graph_tab(fp)
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
        on_delete=(actions.open_delete, ("folder", fid)),
    ))


def render() -> None:
    s = get_state()
    with page("folder"):
        if s.folder_id in sel.all_my_folder_ids(s):
            _folder_screen(s, s.folder_id)
        else:
            empty_state("이 폴더를 볼 수 없어요. 삭제됐거나 멤버가 아닌 폴더예요.", title="폴더를 찾을 수 없어요", pad=48)
