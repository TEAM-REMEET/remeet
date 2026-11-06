"""회의에 물어보기 (온보딩 에이전트).

회의 기록에 자연어로 물으면, 회의 · 안건 · 원문 발화를 근거로 단 답을 보여 줘요.
답 형식은 온보딩 에이전트 ask 결과 JSON 그대로예요 (data/repository.py 의 ask, 명세는
playground/junbyong/onboarding_agent/온보딩 에이전트 화면·출력 보고서.md). 빈 칸은 화면에서 숨겨요.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state.store import AppState, get_state
from ui.avatar import avatar
from ui.buttons import button
from ui.html import markup
from ui.layout import notice, page, page_header
from ui.tags import tag
from utils.text import esc

_SHOW_CITATIONS = 3  # 문장마다 원문 근거는 이만큼만 펼쳐 보여 줌 (많으면 수가 많아져 읽기 어려움)


def _meeting_exists(mid: str) -> bool:
    return mid in repo.meetings()


# ---------------------------------------------------------------- 질문 입력
def _ask_box(s: AppState) -> None:
    key = f"ask_q_{s.ask_nonce}"
    with st.form(f"ask-form-{s.ask_nonce}", border=False):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.text_input("회의에 물어볼 질문", key=key, label_visibility="collapsed", icon=":material/forum:",
                          placeholder="예: PG사는 어디로 정했어요?  ·  1차 회의에서 뭘 했어요?  ·  결제 오류는 누구한테 물어봐요?")
            st.form_submit_button("물어보기", type="primary", on_click=actions.ask_submit, args=(key,))


# ---------------------------------------------------------------- 답 한 개
def _summary_html(a: dict) -> str:
    """문장마다 원문 근거가 있으면 각주 번호(¹ ²)를 붙여 이어 씀."""
    parts, n = [], 0
    for st_ in a.get("sentences") or []:
        mark = ""
        if st_.get("citations"):
            n += 1
            mark = f"<sup>{n}</sup>"
        parts.append(esc(st_["text"]) + mark)
    return " ".join(parts) or esc(a.get("summary", ""))


def _badge(a: dict) -> None:
    b = a.get("badge")
    if not b:
        return
    state = tag("현재 유효한 결정", "ok") if b["status"] == "active" else tag("바뀐 결정이에요", "warn")
    check = tag("사람이 확인함", "info") if b.get("verified_by") == "human" else tag("자동 추출", "off")
    markup(f'<div class="ask-badge">{state}{check}</div>')


def _timeline(a: dict) -> None:
    rows = a.get("decision_timeline") or []
    if not rows:
        return
    items = []
    for d in rows:
        on = d["status"] == "active"
        where = f'{esc(d.get("meeting_title") or "")}' + (f' · 안건 {d["agenda_no"]}' if d.get("agenda_no") else "")
        items.append(
            f'<li class="{"on" if on else "old"}"><span class="where">{where}</span>'
            f'<span class="what">{esc(d["content"])}</span>'
            + (tag("유효", "ok") if on else tag("바뀜", "off")) + "</li>"
        )
    markup('<div class="ask-sec">결정 이력</div><ul class="ask-timeline">' + "".join(items) + "</ul>")


def _sources(a: dict, i: int) -> None:
    rows = a.get("sources") or []
    if not rows:
        return
    markup('<div class="ask-sec">근거 회의</div>')
    with st.container(horizontal=True, gap="small", key=f"ask-src-{i}"):
        for j, r in enumerate(rows):
            label = f'{r["meeting_title"]} · 안건 {r["agenda_no"]} {r["agenda_title"]}'
            button(label, key=f"ask_src_{i}_{j}", variant="secondary", icon=":material/description:",
                   on_click=actions.open_meeting, args=(r["meeting_id"],), disabled=not _meeting_exists(r["meeting_id"]))


def _citations(a: dict, i: int) -> None:
    """문장별 「원문 보기」: 논의 한 줄 요약 + 근거 발화."""
    transcripts = a.get("transcripts") or {}
    n = 0
    for k, sentence in enumerate(a.get("sentences") or []):
        cites = sentence.get("citations") or []
        if not cites:
            continue
        n += 1
        with st.expander(f"{n}  원문 보기 · {sentence['text'][:40]}{'…' if len(sentence['text']) > 40 else ''}"):
            for j, c in enumerate(cites[:_SHOW_CITATIONS]):
                by_no = {u["i"]: u for u in transcripts.get(c["meeting_id"], [])}
                tags = "".join(tag(t, "info") for t in c.get("tags") or [])
                lines = "".join(
                    f'<li><span class="no">#{u["i"]}</span><b>{esc(u["speaker"])}</b>'
                    f'<span class="role">{esc(u.get("role") or "")}</span>{esc(u["text"])}</li>'
                    for u in (by_no[x] for x in c.get("utterances") or [] if x in by_no)
                )
                where = f'{esc(c["meeting_title"])}' + (f' · 안건 {c["agenda_no"]}' if c.get("agenda_no") else "")
                markup(f'<div class="ask-cite"><div class="head"><span class="where">{where}</span>{tags}</div>'
                       f'<p class="point">{esc(c["point"])}</p>' + (f'<ul class="utts">{lines}</ul>' if lines else "")
                       + "</div>")
                # shortcut: 회의 화면만 열고 그 발화로 이동하지 않음, 원문 번호 체계가 회의 상세와 맞춰지면 open_source 로
                button("회의록에서 보기", key=f"ask_open_{i}_{k}_{j}", variant="ghost", icon=":material/open_in_new:",
                       on_click=actions.open_meeting, args=(c["meeting_id"],), disabled=not _meeting_exists(c["meeting_id"]))
            if len(cites) > _SHOW_CITATIONS:
                markup(f'<p class="hint">원문 근거 {len(cites) - _SHOW_CITATIONS}개 더 있어요</p>')


def _contacts(a: dict) -> None:
    rows = a.get("contacts") or []
    if not rows:
        return
    items = "".join(
        f'<li>{avatar(p["name"][:1], size=26)}<span class="who"><b>{esc(p["name"])}</b>'
        f'<span>{esc(" · ".join(p.get("roles") or []))}</span></span><span class="why">{esc(p.get("why") or "")}</span></li>'
        for p in rows
    )
    markup('<div class="ask-sec">더 물어보려면</div>'
           f'<ul class="ask-contacts">{items}</ul>'
           '<p class="hint">회의에 참석했거나 관련 할 일이 있는 사람이에요 · 그 분야 전문가라는 뜻은 아니에요</p>')


def _follow_ups(a: dict, i: int) -> None:
    rows = a.get("follow_ups") or []
    if not rows:
        return
    markup('<div class="ask-sec">이어서 물어보기</div>')
    with st.container(horizontal=True, gap="small", key=f"ask-fu-{i}"):
        for j, q in enumerate(rows):
            button(q, key=f"ask_fu_{i}_{j}", variant="secondary", icon=":material/subdirectory_arrow_right:",
                   on_click=actions.ask_meetings, args=(q,))


def _answer(a: dict, i: int) -> None:
    with st.container(key=f"ask-card-{i}", gap="small"):
        markup(f'<div class="ask-q"><span class="mark">Q</span>{esc(a["question"])}</div>')
        status = a.get("answer_status")
        if status == "error":
            notice("답을 만들지 못했어요. 잠시 후 다시 물어봐 주세요.", "danger")
            return
        if status == "no_evidence":
            markup(f'<p class="ask-none">{esc(a["summary"])}</p>')
            return
        _badge(a)
        markup(f'<p class="ask-summary">{_summary_html(a)}</p>')
        for w in a.get("warnings") or []:
            markup(f'<p class="hint">{esc(w)}</p>')
        _timeline(a)
        _sources(a, i)
        _citations(a, i)
        _contacts(a)
        _follow_ups(a, i)


# ---------------------------------------------------------------- 질문 기록
_STATUS = {"grounded": ("근거 있음", "ok"), "partial": ("일부 근거", "hold"), "no_evidence": ("기록 없음", "off"),
           "error": ("오류", "fail")}


def _history(s: AppState, current: str) -> None:
    """질문 기록 목록: 누르면 그 답을 다시 봐요 (다시 묻지 않음)."""
    with st.container(key="ask-history", gap="small"):
        with st.container(horizontal=True, vertical_alignment="center"):
            markup(f'<span class="ask-history-title">질문 기록 <b>{len(s.ask_history)}</b></span>')
            button("지우기", key="ask_clear", variant="ghost", on_click=actions.ask_clear)
        for j, a in enumerate(s.ask_history):
            label, tone = _STATUS.get(a.get("answer_status"), ("", "off"))
            with st.container(key=f"ask-row-{'on' if a['question'] == current else 'off'}-{j}", gap=None):
                markup(f'<div class="ask-row-meta">{tag(label, tone)}<span>{esc(a.get("asked_at", ""))}</span></div>')
                button(a["question"], key=f"ask_row_{j}", variant="ghost", on_click=actions.ask_open,
                       args=(a["question"],), width="stretch")


# ---------------------------------------------------------------- 진입점
def render() -> None:
    s = get_state()
    with page("ask"):
        page_header("회의에 물어보기",
                    "회의에서 정한 것 · 진행 과정 · 할 일과 담당자 · 누구에게 물어볼지 물어보세요. 모든 답에 근거 회의가 달려요.")
        _ask_box(s)
        if not s.ask_history:
            return
        shown = next((a for a in s.ask_history if a["question"] == s.ask_current), s.ask_history[0])
        main, side = st.columns([3, 1], gap="medium")
        with main:
            _answer(shown, 0)
        with side:
            _history(s, shown["question"])
