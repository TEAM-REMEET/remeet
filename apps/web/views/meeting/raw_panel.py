"""왼쪽 패널: 회의록 원문 (검색 · 발화자 구분 · 근거 강조) / 발언 비율.

발화자: 사람으로 인식된 화자는 이름, 모르는 화자(예: 화자 3)는 「미지정」 태그 · 주관자는 이름을 지정할 수 있어요.
"""

from __future__ import annotations

import streamlit as st

from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState
from ui.avatar import avatar
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon
from ui.tags import tag
from utils.browser import scroll_into_view
from utils.text import esc
from views.meeting import model

_QUERY_KEY = "raw_query_input"


def _sync_query() -> None:
    actions.update(raw_query=st.session_state.get(_QUERY_KEY, ""))


def _speaker_html(s: AppState, m: Meeting, label: str, size: int = 28) -> tuple[str, str]:
    """(아바타, 이름 html) — 사람을 모르면 회색 ? 아바타 + 원문 화자 이름 + 「미지정」 태그."""
    pid = model.speaker_pid(s, m, label)
    if pid:
        p = sel.person(pid)
        return avatar(p.initial, size=size, host=pid == m.host), esc(p.name)
    return (f'<span class="spk-unknown" style="width:{size}px;height:{size}px">?</span>',
            f'{esc(label)} {tag("미지정", "abs")}')


def _paragraphs_html(s: AppState, m: Meeting, query: str) -> str:
    on = set(s.highlight)
    rows = []
    for p in m.paragraphs:
        if query and (p.is_heading or query not in p.text):
            continue
        cls = "para" + (" head" if p.is_heading else "") + (" on" if p.id in on else "")
        if p.is_heading:
            rows.append(f'<div id="src-{p.id}" class="{cls}">{esc(p.text)}</div>')
            continue
        av, who = _speaker_html(s, m, p.speaker)
        rows.append(
            f'<div id="src-{p.id}" class="{cls}"><div class="line">{av}'
            f'<div><span class="who">{who}</span>'
            f'<span class="tx">{esc(p.text)}</span></div></div></div>'
        )
    return "".join(rows)


def _speaker_bar(s: AppState, m: Meeting) -> None:
    """발화자 구분: 이 회의에 나온 사람(발언 수) · 모르는 화자는 「미지정」 + 주관자는 이름 지정. (누르는 기능 없음)"""
    spk = model.speakers(s, m)
    with st.container(key="spk-bar", horizontal=True, vertical_alignment="center", gap="xsmall"):
        chips = "".join(
            f'<span class="spk-chip">{esc(sel.name_of(pid))}<span class="n">{n}</span></span>' if pid
            else f'<span class="spk-chip unknown">{esc(label)} {tag("미지정", "abs")}<span class="n">{n}</span></span>'
            for label, pid, n in spk
        )
        markup(f'<div class="spk-list"><span class="inline-label">발화자</span>{chips}</div>')
        unknown = [(i, label) for i, (label, pid, _) in enumerate(spk) if not sel.speaker_id(label)]
        if unknown and model.is_host(m):
            with st.popover("이름 지정", icon=":material/person_add:", help="미지정 화자에 사람 이름을 붙여요"):
                markup('<b style="font-size:14px">미지정 화자 이름 지정</b>'
                       '<p class="hint" style="font-size:12px">고른 사람으로 이 회의의 그 화자 발언이 모두 바뀌어요.</p>')
                people = list(m.people) + [p for p in sel.all_people_ids() if p not in m.people]
                for i, label in unknown:
                    key = f"spk_pick_{m.id}_{i}"
                    cur = s.speaker_map.get(f"{m.id}:{label}")
                    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                        markup(f'<span class="inline-label">{esc(label)}</span>')
                        st.selectbox(f"{label} 이름", people, index=people.index(cur) if cur in people else None,
                                     key=key, label_visibility="collapsed", placeholder="사람 선택",
                                     format_func=lambda pid: f"{sel.name_of(pid)} · {sel.person(pid).team}"
                                     + ("" if pid in m.people else " (참석자 아님)"),
                                     on_change=actions.set_speaker, args=(m.id, label, key))
                        if cur:
                            button("", key=f"spk_clear_{i}", variant="ghost", icon=":material/close:",
                                   help="미지정으로 되돌리기", on_click=actions.clear_speaker, args=(m.id, label))


def _raw(s: AppState, m: Meeting) -> None:
    query = s.raw_query.strip()
    if st.session_state.get(_QUERY_KEY) != s.raw_query:
        st.session_state[_QUERY_KEY] = s.raw_query  # 키워드 칩으로 검색어가 바뀐 경우 입력창도 맞춤

    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        # live=True: 엔터 없이 입력이 잠깐 멈추면(250ms) 바로 검색
        st.text_input("원문 검색", key=_QUERY_KEY, placeholder="내용/키워드 검색", label_visibility="collapsed",
                      on_change=_sync_query, icon=":material/search:", live=True)

    _speaker_bar(s, m)

    # 상태 줄은 항상 같은 높이로 자리를 잡아 둡니다 (검색·강조 때 화면이 들썩이지 않도록)
    with st.container(key="raw-status", horizontal=True, vertical_alignment="center", gap="xsmall"):
        if query:
            hits = sum(1 for p in m.paragraphs if not p.is_heading and query in p.text)
            markup(f'<span class="hint">「{esc(query)}」 포함 문장 {hits}개 ·</span>')

        elif s.highlight:
            markup(f'<span class="hint">근거 발언 {len(s.highlight)}개를 강조했어요</span>')
        else:
            markup('<span class="hint">오른쪽 문장을 누르면 근거가 된 발언이 강조돼요</span>')

    with st.container(height=640, border=False, key="raw-scroll"):
        markup(_paragraphs_html(s, m, query) or '<p class="hint" style="padding:16px 10px">검색 결과가 없어요.</p>')
    if s.highlight_key and s.highlight:
        scroll_into_view(f"src-{s.highlight[0]}", nonce=s.highlight_key)


def _ratio(s: AppState, m: Meeting) -> None:
    markup('<p class="hint" style="font-size:13px;color:#5F646D">원문 글자 수 기준 발언 비율이에요.</p>')
    rows = []
    for n, pct in model.speaking_ratio(m):
        av, who = _speaker_html(s, m, n)
        rows.append(f'<div class="ratio-row">{av}<span class="nm">{who}</span>'
                    f'<span class="bar thick"><span style="width:{pct}%"></span></span><span class="pct">{pct}%</span></div>')
    rows = "".join(rows)
    markup(f'<div style="display:flex;flex-direction:column;gap:14px">{rows}</div>')


def render_raw_panel(s: AppState, m: Meeting) -> None:
    with st.container(horizontal=True, vertical_alignment="center"):
        markup('<h2 class="panel-title" id="raw-h">회의록 원문</h2>')
        button_group([("raw", "원문"), ("ratio", "발언 비율")], s.left_tab,
                     lambda v: actions.update(left_tab=v), key="left_tab", style="seg", gap=None)

    if model.raw_locked(m):
        markup(
            f'<div class="locked">{icon("lock", 26, "#80858E")}<b>원문은 공유되지 않았어요</b>'
            "<span>보낸 사람이 「요약·결정만」으로 공유해서, 정리된 내용만 볼 수 있어요.</span></div>"
        )
    elif s.left_tab == "ratio":
        _ratio(s, m)
    else:
        _raw(s, m)
