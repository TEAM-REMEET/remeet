"""담당자 지정 — 여러 명 (선행 업무 담당자 + 추천 상위 6명 + 팀에서 직접 고르기).

선행 업무를 맡았던 사람은 맨 위에 따로 보여 주고(점수 · 스킬 없이), 추천 6명 안에도 있으면 거기에도 한 번 더 나와요.

이름 옆 버튼을 누르면 고르고(✓), 다시 누르면 빠져요. 아래 「n명에게 맡기기」를 눌러야 반영돼요.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from data.models import Candidate, Predecessor
from components.org_people import org_people_picker
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button, button_group
from ui.html import markup
from utils.browser import keep_scroll
from utils.text import esc, md_escape


def _bars(c: Candidate) -> list[tuple[str, int]]:
    return [("스킬", c.skill), ("부서·역할", c.dept), ("업무 여유", c.free)]


def _pick_button(name: str, key: str, *, stretch: bool = False) -> None:
    """고르기 / 다시 누르면 빼기 (여러 명). 처음엔 흰 바탕, 고르면 주황 바탕."""
    chosen = name in get_state().assign_draft
    button("✓ 선택됨" if chosen else "선택", key=key, variant="primary" if chosen else "secondary",
           on_click=actions.toggle_assignee, args=(name,), width="stretch" if stretch else "content")


TOP_N = 6  # 추천 후보 수 (한 줄에 모두)


PREV_PER_ROW = 3  # 선행 업무 담당자 카드: 한 줄에 최대 3장 (가로로 긴 카드)


def _predecessors(prev: list[Predecessor]) -> None:
    """선행 업무 담당자 카드: 이름 · 소속 · 추천 이유 · 맡았던 선행 업무 · 선택 (점수 · 스킬은 보여 주지 않음)."""
    with st.container(key="prev-box", gap="small"):
        markup('<div class="prev-title"><b>선행 업무 담당자</b>'
               '<span class="hint">이 일과 이어지는 업무를 먼저 맡았던 사람이에요</span></div>')
        for start in range(0, len(prev), PREV_PER_ROW):
            cols = st.columns(PREV_PER_ROW, gap="small")
            for col, (i, p) in zip(cols, list(enumerate(prev))[start:start + PREV_PER_ROW]):
                # 가로로 긴 카드: (왼쪽) 이름 · 소속 · 추천 이유 · 선행 업무  /  (오른쪽) 선택 — 세로 높이를 줄임
                with col, st.container(key=f"prevcard-{i}", horizontal=True, vertical_alignment="center", gap="small"):
                    markup(f'<div class="prev-who"><b>{esc(p.name)}</b><span>{esc(p.team)} · {esc(p.role)}</span>'
                           '<span class="tag tag-host small">선행 업무 담당자여서 추천해요</span></div>'
                           f'<div class="prev-task">선행 업무 <b>{esc(p.task)}</b>'
                           f'<span>「{esc(p.meeting)}」 {esc(p.date)}</span></div>')
                    _pick_button(p.name, f"prev_{i}")


def _top(cands: list[Candidate]) -> None:
    """추천 상위 6명을 한 줄에."""
    top = cands[:TOP_N]
    with st.container(key="top-cands"):
        cols = st.columns(TOP_N, gap="small")
        for i, (col, c) in enumerate(zip(cols, top)):
            bars = "".join(
                f'<div class="r"><span class="l">{label}</span><span class="bar"><span style="width:{v}%"></span></span>'
                f'<span class="v">{v}</span></div>'
                for label, v in _bars(c)
            )
            sub = f"{c.team} · {c.role}" + ("" if c.att else " · 비참석자")
            with col, st.container(key=f"cand-{c.id}"):
                markup(f'<div class="cand-head"><span>{i + 1}. {esc(c.name)}</span><span>{c.score}점</span></div>'
                       f'<div class="cand-sub">{esc(sub)}</div><div class="cand-bars">{bars}</div>'
                       + (f'<p class="cand-match">{esc(c.match)}</p>' if c.match else '<p class="cand-match"></p>'))
                _pick_button(c.name, f"pick_{c.id}", stretch=True)


def _candidate_row(c: Candidate, cfg: dict, top_rank: int | None) -> None:
    """후보 한 줄: 이름·팀·역할 / 보유 스킬 / 스킬·부서·역할·업무 여유 점수 / 지정 버튼."""
    skills = "".join(f'<span class="skill{" need" if k in cfg["needed_skills"] else ""}">{esc(k)}</span>' for k in c.skills)
    bars = "".join(
        f'<span><span class="lv"><span>{label}</span><span>{v}</span></span>'
        f'<span class="bar"><span style="width:{v}%"></span></span></span>'
        for label, v in _bars(c)
    )
    badge = f'<span class="tag tag-host small">추천 {top_rank}순위</span>' if top_rank else ""
    with st.container(key=f"crow-{c.id}", horizontal=True, vertical_alignment="center", gap="medium"):
        markup(f'<div style="display:flex;flex-direction:column;gap:6px">'
               f'<span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><b style="font-size:15px">{esc(c.name)}</b>'
               f'<span style="font-size:13px;color:#80858E">{esc(c.role)} · 진행 중 {c.tasks}건</span>'
               + ("" if c.att else '<span class="notatt">비참석자</span>') + badge
               + f'</span><span style="display:flex;flex-wrap:wrap;gap:4px">{skills}</span></div>')
        st.html(f'<div class="mini-bars">{bars}</div>', width=330)
        st.html(f'<b style="width:52px;text-align:right;display:block">{c.score}점</b>', width="content")
        _pick_button(c.name, f"pend_{c.id}")


def _team_picker(cands: list[Candidate], cfg: dict) -> None:
    """추천 밖에서 고르기: 조직도(본부 › 부 › 팀 › 사람) · 검색 · 최근 · 즐겨찾기. 이름 옆에는 직급만."""
    s = get_state()
    name_to_pid = {p.name: pid for pid, p in repo.people().items()}

    with st.container(key="card-default-more", gap="small"):
        markup('<h3 style="font-size:15px;font-weight:700">팀에서 직접 고르기</h3>'
               '<p class="hint">이름 · 팀으로 찾거나 조직도를 펼쳐 고르세요.</p>')
        org_people_picker("assign", chosen=[name_to_pid[n] for n in s.assign_draft if n in name_to_pid],
                          on_pick=actions.toggle_assignee_pid, height=320)  # 이름 옆엔 직급만 (기본값)


def _assign_body() -> None:
    s = get_state()
    cfg = repo.assignee_config()
    cands: list[Candidate] = cfg["candidates"]
    t = sel.todo_at(s, s.assign_key)
    current = sel.todo_owner_names(s, s.assign_key, t) if t else s.assigned.get(s.assign_key, [])
    # 어떤 일의 담당자를 정하는지는 창 제목 줄에 (「담당자 지정 · 할 일」) · 안내는 한 줄로
    markup('<p class="hint assign-guide">여러 명에게 함께 맡길 수 있고, 최종 선택은 주관자가 해요.'
           + (f' · 현재 담당 <b>{esc(", ".join(current))}</b>' if current else "") + "</p>")
    # 모달이 길어지지 않도록 본문은 고정 높이 안에서 스크롤 (높이는 pages.css 의 .st-key-assign-body)
    with st.container(height=440, border=False, key="assign-body", gap="small"):
        if cfg["predecessors"]:  # 선행 업무 담당자는 추천 6명 위, 맨 위에
            _predecessors(cfg["predecessors"])
        # 점수 안내는 점수가 나오는 추천 후보 바로 위에 (선행 업무 담당자 카드에는 점수가 없음)
        markup(f'<div class="prev-title top-title"><b>추천 후보 {min(TOP_N, len(cands))}명</b>'
               '<span class="hint">회의록·보유 스킬·현재 업무량으로 점수를 매겼어요</span></div>')
        _top(cands)
        if not s.assign_searching:
            button("추천에 원하는 사람이 없나요? 팀에서 직접 고르기", key="start_search", variant="dashed",
                   width="stretch", on_click=actions.update, kwargs={"assign_searching": True})
        else:
            _team_picker(cands, cfg)
    keep_scroll(".st-key-assign-body")  # 고를 때마다 다시 그려져도 스크롤 위치 그대로

    # 고른 사람 + 맡기기 (모달 아래에 고정)
    chosen = s.assign_draft
    with st.container(key="assign-foot", horizontal=True, vertical_alignment="center", gap="small"):
        chips = "".join(f'<span class="tag tag-att">{esc(n)}</span>' for n in chosen) or \
            '<span class="hint" style="font-size:13px">아직 고른 사람이 없어요</span>'
        markup(f'<div class="assign-chosen"><b>고른 담당자 {len(chosen)}명</b>{chips}</div>')
        if button("취소", key="assign_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        label = f"{len(chosen)}명에게 맡기기" if chosen else ("담당자 비우기" if s.assigned.get(s.assign_key) else "사람을 골라 주세요")
        if button(label, key="assign_ok", variant="primary", disabled=not chosen and not s.assigned.get(s.assign_key)):
            actions.assign_to()
            st.rerun()


def assign_dialog() -> None:
    """담당자 지정 창. 제목 줄 한 줄에 「담당자 지정」과 할 일을 구분해서 (할 일은 주황 바탕 · 열 때마다 제목을 정함)."""
    title = f"담당자 지정 :gray[|] :orange-background[{md_escape(get_state().assign_text)}]"
    st.dialog(title, width="large", on_dismiss=actions.close_modal)(_assign_body)()
