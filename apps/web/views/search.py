"""검색 결과 페이지.

회의록 첫 화면의 검색칸에 치면 이 페이지로 넘어와요. 위쪽 버튼(회의명 · 폴더명 · 구성원 · 회의 내용)으로 결과를 나눠 보고,
결과마다 회의록 안의 글을 조금 보여 주면서 찾은 글자를 진하게 표시합니다.
검색 범위: 내가 볼 수 있는 회의록 (주관 · 참석 · 미참석이지만 공유받은 회의)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from data import repository as repo
from state import actions
from state import archive as arc
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.layout import empty_state, page
from utils.browser import focus_input, ime_live
from utils.text import esc

_LABEL = "검색어"
_TABS = (("title", "회의명"), ("folder", "폴더명"), ("people", "구성원"), ("content", "회의 내용"))


@dataclass(frozen=True)
class Hit:
    key: str
    title_html: str
    meta: str
    snippets: tuple[str, ...]
    on_click: Callable
    args: tuple
    kind: str = "meeting"  # meeting | folder


# ---------------------------------------------------------------- 글자 표시
def _hl(text: str, q: str) -> str:
    """찾은 글자를 모두 <mark> 로 (대소문자 무시)."""
    out, low, ql, i = [], text.lower(), q.lower(), 0
    while True:
        j = low.find(ql, i)
        if j < 0:
            out.append(esc(text[i:]))
            return "".join(out)
        out.append(esc(text[i:j]) + f"<mark>{esc(text[j:j + len(q)])}</mark>")
        i = j + len(q)


def _snip(text: str, q: str, width: int = 38) -> str:
    """찾은 글자 앞뒤로 조금만 잘라 보여 줌."""
    j = text.lower().find(q.lower())
    if j < 0:
        return esc(text[: width * 2]) + ("…" if len(text) > width * 2 else "")
    a, b = max(0, j - width), min(len(text), j + len(q) + width)
    return ("…" if a else "") + _hl(text[a:b], q) + ("…" if b < len(text) else "")


# ---------------------------------------------------------------- 결과 모으기
def _meetings(s: AppState) -> list[str]:
    return sel.by_date_desc(list(dict.fromkeys(sel.visible_note_ids(s))))


def _meta(s: AppState, mid: str) -> str:
    m = repo.meeting(mid)
    return f"{sel.folder_label(s, m)} · {m.date} · 주관 {sel.name_of(m.host)}"


def _by_title(s: AppState, q: str) -> list[Hit]:
    hits = []
    for mid in _meetings(s):
        m = repo.meeting(mid)
        if q.lower() in m.title.lower():
            hits.append(Hit(f"t_{mid}", _hl(m.title, q), _meta(s, mid), (esc(m.summary.text),),
                            actions.open_meeting, (mid,)))
    return hits


def _by_folder(s: AppState, q: str) -> list[Hit]:
    hits = []
    types = repo.meeting_types()
    fs = sel.folders(s)
    for t in sel.TYPE_ORDER:
        for fid in sel.folder_ids_of_type(s, t):
            f = fs[fid]
            if q.lower() not in f.name.lower():
                continue
            notes = sel.folder_note_ids(s, fid)
            last = f"최근 회의 · {repo.meeting(notes[0]).title}" if notes else "아직 회의록이 없어요"
            hits.append(Hit(f"f_{fid}", _hl(f.name, q),
                            f"{types[t].name} · 프로젝트 폴더 · 회의록 {len(notes)}개 · 멤버 {len(sel.members_of(s, fid))}명",
                            (esc(last),), actions.open_folder, (fid,), "folder"))
        for p in arc.periods(s, t):
            if q.replace(" ", "") in p.label.replace(" ", ""):
                hits.append(Hit(f"p_{t}_{p.key}", _hl(p.label, q),
                                f"{types[t].name} · {arc.PERIOD_LABEL[t]} 폴더 · 회의록 {len(p.meeting_ids)}개",
                                (esc(f"최근 회의 · {repo.meeting(p.meeting_ids[0]).title}"),),
                                actions.open_archive, (t, "period", p.key), "folder"))
    return hits


def _by_people(s: AppState, q: str) -> list[Hit]:
    """이름에 검색어가 들어간 사람이 주관하거나 참석한 회의 · 그 사람의 발언을 조금 보여 줌."""
    matched = [pid for pid, p in repo.people().items() if q.lower() in p.name.lower()]
    hits = []
    for mid in _meetings(s):
        m = repo.meeting(mid)
        for pid in matched:
            if pid != m.host and pid not in m.people:
                continue
            p = sel.person(pid)
            role = "주관" if pid == m.host else "참석"
            said = [x.text for x in m.paragraphs if not x.is_heading and sel.speaker_id(x.speaker) == pid][:2]
            snippets = tuple(f"<b>{_hl(p.name, q)}</b> · {esc(t[:70])}{'…' if len(t) > 70 else ''}" for t in said) \
                or (f"<b>{_hl(p.name, q)}</b> 님의 발언은 원문에 없어요",)
            hits.append(Hit(f"u_{pid}_{mid}", esc(m.title), f"{p.name} · {role} · {_meta(s, mid)}", snippets,
                            actions.open_meeting, (mid,)))
    return hits


def _by_content(s: AppState, q: str) -> list[Hit]:
    """회의록 안의 글(원문 · 요약 · 결정 · 할 일)에서 찾기 · 찾은 곳 앞뒤 글을 조금 보여 줌."""
    hits = []
    for mid in _meetings(s):
        m = repo.meeting(mid)
        texts = [x.text for x in m.paragraphs if not x.is_heading]
        texts += [m.summary.text] + [d.text for d in m.decisions] + [t.text for _, t in sel.todo_items(s, mid)]
        found = [t for t in texts if q.lower() in t.lower()]
        if found:
            snippets = tuple(_snip(t, q) for t in found[:2])
            more = f" · {len(found)}곳" if len(found) > 1 else ""
            hits.append(Hit(f"c_{mid}", esc(m.title), _meta(s, mid) + more, snippets,
                            actions.open_meeting_search, (mid, q)))
    return hits


# ---------------------------------------------------------------- 화면
def _hit_card(hit: Hit) -> None:
    ic = icon("folder" if hit.kind == "folder" else "file", 18, "#F37321", 1.9)
    snips = "".join(f'<span class="sn">{x}</span>' for x in hit.snippets)
    with st.container(key=f"sres-{hit.key}"):
        markup(f'<div class="sres">{ic}<span class="tx"><b class="tt">{hit.title_html}</b>'
               f'<span class="meta">{esc(hit.meta)}</span>{snips}</span></div>')
        button("열기", key=f"sres_open_{hit.key}", variant="secondary", on_click=hit.on_click, args=hit.args,
               width="stretch")


def render() -> None:
    s = get_state()
    key = f"search_q_{s.search_nonce}"
    with page("search"):
        st.text_input(_LABEL, key=key, value=s.search_query, live=True, icon=":material/search:",
                      placeholder="회의명 · 폴더명 · 구성원 · 회의 내용으로 찾기", label_visibility="collapsed",
                      on_change=actions.set_search_query, args=(key,))
        ime_live(_LABEL)  # 한글도 치는 대로 바로 검색
        focus_input(_LABEL, nonce=str(s.search_nonce))  # 회의록 화면에서 넘어와도 이어서 칠 수 있게

        q = s.search_query.strip()
        if not q:
            empty_state("찾고 싶은 회의명 · 폴더명 · 사람 이름 · 회의 내용을 적어 주세요.", title="무엇을 찾을까요?", pad=48)
            return
        results = {"title": _by_title(s, q), "folder": _by_folder(s, q), "people": _by_people(s, q),
                   "content": _by_content(s, q)}
        current = s.search_tab if s.search_tab in results else next((k for k, _ in _TABS if results[k]), "title")

        markup(f'<div class="search-head"><b>「{esc(q)}」 검색 결과</b>'
               f'<span>모두 {sum(len(v) for v in results.values())}건</span></div>')
        button_group([(k, f"{label} {len(results[k])}") for k, label in _TABS], current,
                     lambda v: actions.update(search_tab=v), key="stab", style="tab")
        hits = results[current]
        if not hits:
            empty_state(f"{dict(_TABS)[current]}에서 「{q}」을(를) 찾지 못했어요. 다른 버튼도 눌러 보세요.", pad=40)
            return
        with st.container(key="search-list", gap="small"):  # 결과가 많으면 이 영역만 스크롤
            for hit in hits:
                _hit_card(hit)
