"""오른쪽 패널: 요약 / 안건 / 액션 아이템 / 키워드·일정 / 연관 회의 탭.

문장 버튼(variant pick)을 누르면 왼쪽 원문에서 근거 발언이 강조됩니다.
"""

from __future__ import annotations

import streamlit as st

from data.models import Meeting
from state import actions
from state.store import AppState
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon_box
from ui.layout import card, card_title, hint, notice
from ui.tags import md_tag, tag
from utils.browser import copy_to_clipboard
from utils.text import esc, md_escape
from views.meeting import model


def _pick(label: str, key: str, s: AppState, src: tuple[str, ...]) -> None:
    """근거 문장 버튼. key 앞부분(disc/pt/sum/quote/ag)으로 CSS 모양이 정해집니다."""
    on = s.highlight_key == key
    button(label, key=key, variant="pickon" if on else "pick", on_click=actions.pick_evidence, args=(key, src),
           width="stretch")


def _copy_button(s: AppState, key: str, text: str) -> None:
    button("복사됨" if s.copied == key else "복사", key=f"copy_{key}", variant="mini",
           on_click=actions.mark_copied, args=(key,))
    if s.copied == key:
        copy_to_clipboard(text, nonce=key)


# ---------------------------------------------------------------- 요약
def _summary_tab(s: AppState, m: Meeting) -> None:
    host = model.is_host(m)
    editable = model.assignee_editable(s, m)
    items = model.assignable(s, m)
    if any(not who for *_, who in items) or (items and editable):
        with card("unassigned", tone="warn"):
            title = "담당자가 아직 없는 일" if any(not who for *_, who in items) else "담당자를 정한 일"
            markup(f'<h3 style="font-size:13px;font-weight:700;color:#7A5200">{title}'
                   + ('<span style="font-weight:500;color:#9A7A3A"> · 확정하기 전까지 바꿀 수 있어요</span>' if editable else "")
                   + "</h3>")
            for key, text, due, who in items:
                with st.container(horizontal=True, vertical_alignment="center"):
                    markup(f'<span style="display:flex;flex-direction:column;line-height:1.4"><b style="font-weight:600">{esc(text)}</b>'
                           f'<span style="font-size:12px;color:#80858E">{esc(due)}</span></span>')
                    if who:
                        st.html(tag(who, "att"), width="content")
                    if host and editable:
                        if who:
                            button("변경", key=f"assign_{key}", variant="secondary", on_click=actions.open_assign, args=(key, text))
                        else:
                            button("담당자 지정", key=f"assign_{key}", variant="primary", on_click=actions.open_assign, args=(key, text))
                    elif not who:
                        st.html('<span class="hint" style="font-size:13px">주관자가 지정할 예정</span>', width="content")

    with card("summary"):
        card_title("핵심 요약", icon_html=icon_box("lines-full", bg="#F1F2F4", color="#F37321"))
        if s.editing and host:
            st.text_area("핵심 요약 편집", value=m.summary.text, label_visibility="collapsed", key=f"edit_summary_{m.id}")
        else:
            _pick(md_escape(m.summary.text), "sum", s, m.summary.src)

    disc = model.discussion(m)
    with card("discussion"):
        with st.container(horizontal=True, vertical_alignment="center"):
            card_title("주요 논의 사항", icon_html=icon_box("bulb", bg="#FFF5D9", color="#9A6700"))
            _copy_button(s, "disc", "\n".join(f"- {d.label}: {d.text}" for d in disc))
        with st.container(gap=None):
            for i, d in enumerate(disc):
                _pick(f"**{md_escape(d.label)}:** {md_escape(d.text)}", f"disc{i}", s, d.src)

    with card("decisions"):
        card_title("결정된 것", icon_html=icon_box("check", bg="transparent", color="#1D6B43", size=15, box=16, stroke=2.4), size=14)
        if not m.decisions:
            hint("아직 결정된 것이 없어요.", size=13)
        for i, d in enumerate(m.decisions):
            _pick(md_escape(d.text), f"dec{i}", s, d.src)
    with card("open"):
        card_title("다음에 이어서", icon_html=icon_box("clock", bg="transparent", color="#9A6700", size=15, box=16), size=14)
        for i, d in enumerate(m.open_issues):
            _pick(md_escape(d.text), f"open{i}", s, d.src)


# ---------------------------------------------------------------- 안건
def _agenda_tab(s: AppState, m: Meeting) -> None:
    weights = model.agenda_weights(s, m)
    with card("weights"):
        card_title("안건별 논의 비중", aside="원문 분량 기준")
        if not weights:
            hint("원문이 공유되지 않아 논의 비중을 계산할 수 없어요.", size=13)
        else:
            bars = "".join(f'<span style="width:{w.pct}%;background:{w.color}"></span>' for w in weights)
            markup(f'<div class="weight-bar" aria-hidden="true">{bars}</div>')
            for w in weights:
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.html(f'<span class="swatch" style="background:{w.color}"></span>', width="content")
                    _pick(f"**안건 {w.index}. {md_escape(w.title)}**  \n:gray[발언 {w.lines}개{w.long_note}]",
                          f"wt{w.index}", s, w.src)
                    st.html(tag(w.status, w.tone), width="content")
                    st.html(f'<span class="wt-pct">{w.pct}%</span>', width="content")

    host = model.is_host(m)
    with card("flow"):
        card_title("안건 흐름")
        for i, a in enumerate(m.agendas):
            status, tone, branched = model.agenda_status(s, m, i)
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                _pick(f"`안건 {i + 1}` **{md_escape(a.title)}**", f"ag{i}", s, a.src)
                st.html(tag(status, tone), width="content")
            with st.container(key=f"agpts-{i}"):
                for j, p in enumerate(a.points):
                    _pick(md_escape(p.text), f"pt{i}_{j}", s, p.src)
                if a.deep and not s.branched and m.id == "m1":
                    # 평소에는 접혀 있고, 펼치면 분리 안내와 버튼이 나옴
                    with st.container(key=f"branchwrap-{i}"):
                        with st.expander("논의가 길어졌어요 · 이 안건을 새 회의록으로 분리하기", icon=":material/call_split:"):
                            markup('<span class="branch-box">같은 폴더에 이 안건만 다루는 회의록을 새로 만들어 이어서 논의할 수 있어요.</span>')
                            if host:
                                button("새 회의록으로 분리", key="do_branch", variant="primary", on_click=actions.branch_agenda)
                            else:
                                button("주관자에게 요청", key="ask_branch", variant="secondary")
                if branched:
                    button("↳ 같은 폴더에 「결제 오류 대응」 회의록으로 분리됨 · 열기", key="openbranch", variant="ghost",
                           on_click=actions.open_meeting, args=("m9",), width="stretch")
        if s.editing and host:
            button("\\+ 안건 추가", key="add_agenda", variant="dashed", width="stretch")


# ---------------------------------------------------------------- 액션 아이템
def _todo_tab(s: AppState, m: Meeting) -> None:
    items = model.todos(s, m)
    mine = [t for t in items if t.mine]
    shown = items if s.todo_scope == "all" else mine
    with card("todos"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            card_title("액션 아이템", icon_html=icon_box("check", bg="#E7F5EC", color="#1D6B43", stroke=2.6))
            button_group([("mine", f"내 할 일 {len(mine)}"), ("all", f"전체 {len(items)}")], s.todo_scope,
                         lambda v: actions.update(todo_scope=v), key="todo_scope", style="seg", gap=None)
        if not shown:
            hint("이 회의에서 나온 액션 아이템이 없어요." if s.todo_scope == "all"
                 else "이 회의에서 나에게 배정된 일은 없어요. 「전체」에서 다른 사람의 일을 볼 수 있어요.", size=13)
        for t in shown:
            with st.container(key=f"todo-{t.index}{'-done' if t.done else ''}"):
                st.checkbox(md_escape(t.text), value=t.done, key=f"todo_cb_{t.key}",
                            on_change=actions.toggle_todo, args=(t.key,))
                markup(f'<div class="todo-meta">{tag(t.who, t.who_tone)}'
                       f'<span class="todo-due{" urgent" if t.urgent else ""}">{esc(t.due)}</span></div>')
                with st.container(key=f"quotewrap-{t.index}"):
                    _pick(f"`{md_escape(t.speaker)}` “{md_escape(t.quote)}”", f"quote{t.index}", s, t.src)


# ---------------------------------------------------------------- 키워드·일정
def _keyword_tab(s: AppState, m: Meeting) -> None:
    with card("keywords"):
        card_title("주요 키워드", aside="누르면 원문에서 찾아요",
                   icon_html='<span class="icon-box" style="width:22px;height:22px;border-radius:3px;background:#2B2D33;'
                             'color:#FFC24D;font-size:10px;font-weight:800">AB</span>')
        with st.container(horizontal=True, gap="xsmall"):
            for i, kw in enumerate(m.keywords):
                button(md_escape(kw), key=f"kw{i}", variant="kwon" if s.raw_query == kw else "kw",
                       on_click=actions.search_keyword, args=(kw,))
    lines = model.date_lines(m)
    with card("dates"):
        card_title("날짜·시간이 들어간 대화", icon_html=icon_box("clock-hand", bg="#F37321", color="#FFFFFF", round_=True, stroke=2.4))
        if not lines:
            hint("원문이 공유되지 않아 날짜·시간을 찾을 수 없어요." if model.raw_locked(m)
                 else "대화 내용에서 날짜·시간을 찾지 못했어요.", size=13)
        for d in lines:
            _pick(f"`{md_escape(d.token)}` :gray[**{md_escape(d.who)}**]  \n“{md_escape(d.text)}”", f"date{d.pid}", s, (d.pid,))


# ---------------------------------------------------------------- 연관 회의
def _related_tab(s: AppState, m: Meeting) -> None:
    items = model.related(s, m)
    if not items:
        markup('<p class="hint" style="padding:24px 0;text-align:center;font-size:14px">같은 폴더에 연관된 회의가 없어요.</p>')
    for r in items:
        # 태그를 제목과 같은 줄(버튼 라벨 안)에 넣고, CSS 로 태그만 왼쪽 칸으로 빼서 윗선을 맞춤
        label = f"{md_tag(r.role, r.tone)} **{md_escape(r.title)}**  \n:gray[{r.date} · {md_escape(r.relation)}]"
        with st.container(key=f"relcard-{r.id}"):
            button(label, key=f"rel_{r.id}", variant="card", on_click=actions.open_meeting, args=(r.id,), width="stretch")


_TABS = {"sum": _summary_tab, "ag": _agenda_tab, "todo": _todo_tab, "kw": _keyword_tab, "rel": _related_tab}


def render_insights(s: AppState, m: Meeting) -> None:
    with st.container(key="md-tabs"):
        button_group(
            [("sum", "요약"), ("ag", f"안건 {len(m.agendas)}"), ("todo", f"액션 아이템 {len(m.todos)}"),
             ("kw", "키워드·일정"), ("rel", f"연관 회의 {len(model.related(s, m))}")],
            s.insight_tab, actions.set_insight_tab, key="insight", style="tab",
        )
    with st.container(height=700, border=False, key="md-body", gap="small"):
        if not model.raw_locked(m) and s.insight_tab != "rel":
            hint("문장을 누르면 왼쪽 원문에서 근거가 된 발언을 표시해요")
        _TABS.get(s.insight_tab, _summary_tab)(s, m)
