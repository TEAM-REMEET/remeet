"""오른쪽 패널: 요약(안건 목차 · 회의 결과 · 안건별 상세) / 안건 / 액션 아이템 / 키워드·일정 / 연관 회의 탭.

문장 버튼(variant pick)을 누르면 왼쪽 원문에서 근거 발언이 강조됩니다.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState
from ui.buttons import button, button_group
from ui.html import markup
from ui.icons import icon_box
from ui.layout import card, card_title, hint
from ui.tags import md_tag, tag
from utils.browser import copy_to_clipboard, scroll_into_view
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
def _assign_card(s: AppState, m: Meeting, items: list, *, host: bool, editable: bool, pending: str = "") -> None:
    """담당자가 없던 일: 할 일을 누르면 원문 근거 강조 · 아래 줄에 「누구 → 누구」로 바뀐 내용.

    editable 이 아니면 지정 버튼 없이 보기만 · pending: 담당 미정 옆 안내 (없으면 「주관자가 지정할 예정」)
    """
    unassigned = sum(1 for _, _, who, _, _ in items if not who)  # 지금 담당 미정인 액션 아이템 개수
    with card("unassigned", tone="warn"):
        title = f"담당자가 아직 없는 일 - {unassigned}개" if unassigned else "담당자를 정한 일"
        markup(f'<h3 style="font-size:13px;font-weight:700;color:#7A5200">{title}'
               + ('<span style="font-weight:500;color:#9A7A3A"> · 확정하기 전까지 바꿀 수 있어요</span>' if editable else "")
               + "</h3>")
        for i, (key, text, who, _prev, src) in enumerate(items):
            with st.container(key=f"asgrow-{i}", horizontal=True, vertical_alignment="center", gap="small"):
                with st.container(gap=None):
                    _pick(f"**{md_escape(text)}**", f"asg{i}", s, src)
                    if who:  # 처음(담당 미정) → 지금 담당자들 · 중간에 바꾼 기록은 보이지 않음
                        markup(f'<span class="asg-change">{esc("담당 미정 → " + ", ".join(who))}</span>')
                    else:
                        markup('<span class="asg-change">담당 미정'
                               + ("" if host and editable else f" · {esc(pending or '주관자가 지정할 예정')}") + "</span>")
                if host and editable:
                    if who:
                        button("변경", key=f"assign_{key}", variant="secondary", on_click=actions.open_assign, args=(key, text))
                    else:
                        button("담당자 지정", key=f"assign_{key}", variant="primary", on_click=actions.open_assign,
                               args=(key, text))


# 회의 결과 · 주요 논의 태그 → Streamlit 마크다운 배경색
_RESULT_TONES = {"결정": "ok", "미해결": "open", "공유": "info"}  # 회의 결과 태그 색 (pages.css .res-*)
_POINT_COLORS = {"정보": "gray", "제안": "blue", "반대·우려": "red", "질문": "violet"}


def _count_label(d: model.AgendaDetail) -> str:
    return f"결정 {len(d.decisions)} · 미해결 {len(d.open_issues)}"


def _toc_card(s: AppState, m: Meeting, details: list[model.AgendaDetail]) -> None:
    """안건 목차: 누르면 아래 「안건별 상세」에서 그 안건을 펼치고 그 자리로 이동."""
    with card("toc"):
        card_title("안건 목차", aside="누르면 안건별 상세로 이동해요",
                   icon_html=icon_box("lines-full", bg="#F1F2F4", color="#F37321"))
        if not details:
            hint("정리된 안건이 없어요.", size=13)
        for d in details:
            button(f"**{d.index}.** {md_escape(d.title)}  :gray[{_count_label(d)}]", key=f"toc{d.index}",
                   variant="pick", on_click=actions.jump_to_agenda, args=(m.id, d.index - 1), width="stretch")


def _results_card(s: AppState, m: Meeting) -> None:
    results = model.meeting_results(m)
    with card("results"):
        with st.container(horizontal=True, vertical_alignment="center"):
            card_title("회의 결과", icon_html=icon_box("bulb", bg="#FFF5D9", color="#9A6700"))
            _copy_button(s, "results", "\n".join(
                f"- [{r.kind}] {r.text}" + "".join(f" [안건{n}]" for n in r.agendas) for r in results))
        if not results:
            hint("정리된 회의 결과가 없어요.", size=13)
        with st.container(gap=None):
            for i, r in enumerate(results):
                refs = " ".join(f"`안건 {n}`" for n in r.agendas)
                # 태그는 왼쪽 칸 · 글은 오른쪽 칸 → 글이 길어져 줄이 바뀌어도 태그 아래로 내려가지 않고 태그 뒤에 맞춰 정렬
                with st.container(key=f"resrow-{i}", horizontal=True, vertical_alignment="top", gap="small"):
                    tone = _RESULT_TONES.get(r.kind, "etc")
                    st.html(f'<span class="res-tag res-{tone}">{esc(r.kind)}</span>', width="content")
                    _pick(md_escape(r.text) + (f"  \n{refs}" if refs else ""), f"res{i}", s, r.src)


def _detail_section(title: str, items: tuple, key: str, s: AppState) -> None:
    """값이 없으면 제목까지 보이지 않아요."""
    if not items:
        return
    markup(f'<h4 class="agd-h">{esc(title)}</h4>')
    for j, e in enumerate(items):
        _pick(md_escape(e.text), f"{key}_{j}", s, e.src)


def _agenda_detail(s: AppState, m: Meeting, d: model.AgendaDetail) -> None:
    i = d.index - 1
    with st.container(key=f"agdet-{i}", gap=None):
        _detail_section("논의 배경", d.background, f"abg{i}", s)
        if d.topics:
            markup('<h4 class="agd-h">주요 논의</h4>')
        for t, topic in enumerate(d.topics):
            if topic.title:
                markup(f'<p class="agd-topic">{d.index}-{t + 1}. {esc(topic.title)}</p>')
            for j, p in enumerate(topic.points):
                kind = f":{_POINT_COLORS.get(p.kind, 'gray')}-background[{md_escape(p.kind)}] " if p.kind else ""
                _pick(kind + md_escape(p.text), f"atp{i}_{t}_{j}", s, p.src)
        _detail_section("결정사항", d.decisions, f"adec{i}", s)
        _detail_section("미해결 이슈", d.open_issues, f"aopen{i}", s)
        _detail_section("다음 회의 후보 안건", d.next_candidates, f"anext{i}", s)


def _details_card(s: AppState, m: Meeting, details: list[model.AgendaDetail]) -> None:
    opened = {k for k in s.agenda_open if k.startswith(f"{m.id}:")}
    with card("agdetails"):
        with st.container(horizontal=True, vertical_alignment="center"):
            card_title("안건별 상세", icon_html=icon_box("file-text", bg="#E8F0FB", color="#2F5FB3"))
            if details:
                all_open = len(opened) >= len(details)
                button("모두 접기" if all_open else "모두 펼치기", key="agd_all", variant="mini",
                       on_click=actions.set_all_agenda_details, args=(m.id, len(details), not all_open))
        if not details:
            hint("정리된 안건이 없어요.", size=13)
        for d in details:
            i = d.index - 1
            is_open = f"{m.id}:{i}" in opened
            markup(f'<div id="agd-{m.id}-{i}" class="agd-anchor"></div>')
            with st.container(key=f"agdhead-{i}", horizontal=True, vertical_alignment="center", gap="small"):
                button(f"{'▾' if is_open else '▸'} **{d.index}. {md_escape(d.title)}**  :gray[{_count_label(d)}]",
                       key=f"agh{i}", variant="pick", on_click=actions.toggle_agenda_detail, args=(m.id, i),
                       width="stretch")
            if is_open:
                _agenda_detail(s, m, d)
    if s.agenda_jump:  # 목차에서 누른 안건으로 한 번만 스크롤
        scroll_into_view(s.agenda_jump, nonce=str(s.agenda_jump_n), to_top=True)
        s.agenda_jump = ""


def _summary_tab(s: AppState, m: Meeting, mode: str = "edit") -> None:
    """회의록 형식: 안건 목차 → 회의 결과 → 안건별 상세 (+ 담당자 카드)."""
    host = model.is_host(m)
    editable = model.assignee_editable(s, m) and mode == "edit"  # 분석 및 검토 · 최종 회의록에서는 지정 버튼 없음
    items = model.assignable(s, m)
    show = any(not who for _, _, who, _, _ in items) or (items and editable)

    details = model.agenda_details(m)
    _toc_card(s, m, details)
    _results_card(s, m)
    _details_card(s, m, details)
    if show:  # 담당자 카드는 항상 맨 아래에
        _assign_card(s, m, items, host=host, editable=editable,
                     pending="확정한 뒤 담당자 추천에서 정해요" if mode == "preview" else "")


# ---------------------------------------------------------------- 안건
def _agenda_tab(s: AppState, m: Meeting) -> None:
    weights = model.agenda_weights(s, m)
    with card("weights"):
        card_title("안건별 논의 비중", aside=model.weight_basis(m),
                   icon_html=icon_box("chart", bg="#F1ECFB", color="#6B4FBB", stroke=2.4))
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
        card_title("안건 흐름", icon_html=icon_box("flow", bg="#E3F4F4", color="#1F7A7A"))
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
                            elif m.id in s.branch_requests:
                                button("요청을 보냈어요 · 주관자가 분리하면 알려드려요", key="ask_branch_done",
                                       variant="secondary", icon=":material/check:", disabled=True)
                            else:
                                button("주관자에게 요청", key="ask_branch", variant="secondary",
                                       icon=":material/send:", on_click=actions.request_branch, args=(m.id,))
                if branched:
                    button("↳ 같은 폴더에 「결제 오류 대응」 회의록으로 분리됨 · 열기", key="openbranch", variant="ghost",
                           on_click=actions.open_meeting, args=("m9",), width="stretch")
        _added_agendas(s, m, editing=s.editing and host)
        if s.editing and host:
            button("\\+ 안건 추가", key="add_agenda", variant="dashed", width="stretch",
                   on_click=actions.add_agenda, args=(m.id,))


def _added_agendas(s: AppState, m: Meeting, *, editing: bool) -> None:
    """「+ 안건 추가」로 넣은 안건: 편집 중에는 제목 입력칸 + 빼기, 아니면 제목만."""
    added = s.added_agendas.get(m.id, [])
    base = len(m.agendas)
    for k, title in enumerate(added):
        no = base + k + 1
        if editing:
            # key 에 개수를 붙여, 하나를 빼면 칸을 새로 만들어 남은 제목이 제자리에 오도록
            widget_key = f"agadd_{m.id}_{k}_{len(added)}"
            with st.container(key=f"agadd-{k}", horizontal=True, vertical_alignment="center", gap="small"):
                st.html(f'<span class="agadd-no">안건 {no}</span>', width="content")
                st.text_input(f"안건 {no} 제목", value=title, key=widget_key, label_visibility="collapsed",
                              placeholder="새 안건 제목을 적어 주세요",
                              on_change=actions.set_agenda_title, args=(m.id, k, widget_key))
                button("", key=f"agadd_rm_{k}", variant="ghost", icon=":material/close:", help="이 안건 빼기",
                       on_click=actions.remove_agenda, args=(m.id, k))
        elif title:
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                markup(f'<span class="agadd-view"><code>안건 {no}</code> <b>{esc(title)}</b></span>')
                st.html(tag("추가함", "host"), width="content")


# ---------------------------------------------------------------- 액션 아이템
def _todo_tab(s: AppState, m: Meeting, mode: str = "edit") -> None:
    """mode: edit(회의 상세 · 확정 전) | preview(새 회의록 분석 및 검토 · 담당자 지정 없음) | final(최종 회의록 · 고칠 수 없음)"""
    items = model.todos(s, m)
    mine = [t for t in items if t.mine]
    shown = items if s.todo_scope == "all" else mine
    with card("todos"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            card_title("액션 아이템", icon_html=icon_box("check", bg="#E7F5EC", color="#1D6B43", stroke=2.6))
            button_group([("all", f"전체 {len(items)}"), ("mine", f"내 할 일 {len(mine)}")], s.todo_scope,
                         lambda v: actions.update(todo_scope=v), key="todo_scope", style="seg", gap=None)
        if not shown:
            hint("이 회의에서 나온 액션 아이템이 없어요." if s.todo_scope == "all"
                 else "이 회의에서 나에게 배정된 일은 없어요. 「전체」에서 다른 사람의 일을 볼 수 있어요.", size=13)
        host = model.is_host(m)
        can_edit = host and mode != "final"  # 추가 · 삭제 (최종 회의록은 고칠 수 없음)
        can_assign = host and model.assignee_editable(s, m) and mode == "edit"  # 요약 탭 담당자 카드와 같은 조건
        for t in shown:
            with st.container(key=f"todo-{t.index}"):
                # 할 일 제목 · 지우기는 오른쪽 빨간 휴지통 (주관자만)
                with st.container(key=f"todohead-{t.index}", horizontal=True, vertical_alignment="top", gap="small"):
                    if can_edit:  # 내용을 누르면 수정 모달 (내용 · 담당자 · 마감일)
                        button(md_escape(t.text), key=f"todo_edit_{t.key}", variant="pick", width="stretch",
                               icon=":material/edit:", help="눌러서 내용 · 담당자 · 마감일 수정",
                               on_click=actions.open_edit_todo, args=(m.id, t.index))
                    else:
                        st.html(f'<p class="todo-title">{esc(t.text)}</p>', width="stretch")
                    if can_edit:
                        button("", key=f"todo_del_{t.key}", variant="trash", icon=":material/delete:",
                               help="이 액션 아이템 삭제 (목록 아래에서 되돌릴 수 있어요)",
                               on_click=actions.delete_todo, args=(m.id, t.index))
                due = f'<span class="todo-due{" urgent" if t.urgent else ""}">{esc(t.due)}</span>'
                if t.unassigned and can_assign:
                    # 「담당 미정」을 누르면 요약 탭 「담당자 지정」과 같은 담당자 지정 창
                    with st.container(key=f"todometa-{t.index}", horizontal=True, vertical_alignment="center",
                                      gap="small"):
                        button("담당 미정", key=f"todo_assign_{t.key}", variant="tagwarn",
                               icon=":material/person_add:", help="눌러서 담당자 지정",
                               on_click=actions.open_assign, args=(t.key, t.text))
                        st.html(due, width="content")
                else:
                    check = tag("담당자 확인 필요", "warn") if t.assignee_check and mode == "preview" else ""
                    markup(f'<div class="todo-meta">{check}{"".join(tag(name, tone) for name, tone in t.who_tags)}{due}</div>')
                with st.container(key=f"quotewrap-{t.index}"):
                    _pick(f"`{md_escape(t.speaker)}` “{md_escape(t.quote)}”", f"quote{t.index}", s, t.src)
        if can_edit:  # 액션 아이템 직접 추가는 주관자만 (최종 회의록 제외)
            button("\\+ 액션 아이템 추가", key="add_todo", variant="dashed", width="stretch",
                   on_click=actions.open_add_todo)
    if can_edit:
        _deleted_todos(s, m)


def _deleted_todos(s: AppState, m: Meeting) -> None:
    """삭제한 액션 아이템: 하나씩 되돌리기 + 모두 되돌리기(리셋)."""
    gone = model.deleted_todos(s, m)
    if not gone:
        return
    with card("todo-trash", tone="muted"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            card_title(f"삭제한 액션 아이템 {len(gone)}개", size=14,
                       icon_html=icon_box("trash", bg="transparent", color="#B3261E", size=15, box=16))
            button("모두 되돌리기", key="todo_restore_all", variant="secondary", icon=":material/restart_alt:",
                   on_click=actions.restore_all_todos, args=(m.id,))
        for i, text in gone:
            with st.container(key=f"todogone-{i}", horizontal=True, vertical_alignment="center", gap="small"):
                markup(f'<span class="todo-gone">{esc(text)}</span>')
                button("되돌리기", key=f"todo_restore_{i}", variant="mini", icon=":material/undo:",
                       on_click=actions.restore_todo, args=(m.id, i))


# ---------------------------------------------------------------- 키워드·일정

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


_TABS = {"sum": _summary_tab, "ag": _agenda_tab, "todo": _todo_tab, "rel": _related_tab}


def render_insights(s: AppState, m: Meeting, mode: str = "edit") -> None:
    """mode: edit(회의 상세 · 확정 전) | preview(새 회의록 분석 및 검토) | final(최종 회의록 · 확정 후 · 고칠 수 없음)"""
    m = repo.meeting(m.id)  # 직접 추가한 액션 아이템까지 합친 회의 (새 회의록 미리보기 포함)
    with st.container(key="md-tabs"):
        button_group(
            [("sum", "요약"), ("ag", f"안건 {len(m.agendas) + sum(1 for t in s.added_agendas.get(m.id, []) if t)}"), ("todo", f"액션 아이템 {len(sel.todo_items(s, m.id))}"), ("rel", f"연관 회의 {len(model.related(s, m))}")],
            s.insight_tab, actions.set_insight_tab, key="insight", style="tab",
        )
    with st.container(height=700, border=False, key="md-body", gap="small"):
        if not model.raw_locked(m) and s.insight_tab != "rel":
            hint("문장을 누르면 왼쪽 원문에서 근거가 된 발언을 표시해요")
        tab = _TABS.get(s.insight_tab, _summary_tab)
        if tab in (_summary_tab, _todo_tab):
            tab(s, m, mode=mode)
        else:
            tab(s, m)
