"""담당자 지정 — 여러 명 (추천 상위 3명 + 팀에서 직접 고르기).

이름 옆 버튼을 누르면 고르고(✓), 다시 누르면 빠져요. 아래 「n명에게 맡기기」를 눌러야 반영돼요.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from data.models import Candidate
from state import actions
from state.store import get_state
from ui.buttons import button, button_group
from ui.html import markup
from utils.browser import keep_scroll
from utils.text import esc


def _bars(c: Candidate) -> list[tuple[str, int]]:
    return [("스킬", c.skill), ("부서·역할", c.dept), ("업무 여유", c.free)]


def _pick_button(name: str, key: str, *, stretch: bool = False) -> None:
    """고르기 / 다시 누르면 빼기 (여러 명). 처음엔 흰 바탕, 고르면 주황 바탕."""
    chosen = name in get_state().assign_draft
    button("선택됨" if chosen else "선택", key=key, variant="primary" if chosen else "secondary",
           on_click=actions.toggle_assignee, args=(name,), width="stretch" if stretch else "content")


def _top3(cands: list[Candidate]) -> None:
    cols = st.columns(3, gap="medium")
    for i, (col, c) in enumerate(zip(cols, cands[:3])):
        bars = "".join(
            f'<div class="r"><span class="l">{label}</span><span class="bar"><span style="width:{v}%"></span></span>'
            f'<span class="v">{v}</span></div>'
            for label, v in _bars(c)
        )
        sub = f"{c.team} · {c.role}" + ("" if c.att else " · 비참석자")
        with col, st.container(key=f"cand-{c.id}"):
            markup(f'<div class="cand-head"><span>{i + 1}. {esc(c.name)}</span><span>{c.score}점</span></div>'
                   f'<div class="cand-sub">{esc(sub)}</div><div class="cand-bars">{bars}</div>'
                   f'<p class="cand-match">보유 스킬 일치: {esc(c.match)}</p>')
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
    """추천 밖에서 고르기: 팀을 고르면 그 팀 구성원과 점수가 나옵니다."""
    s = get_state()
    teams = repo.teams()
    by_team = repo.team_candidates()
    top_rank = {c.id: i + 1 for i, c in enumerate(cands[:3])}
    default_team = next((tid for tid, t in teams.items() if t.name == cands[0].team), next(iter(teams)))
    team_id = s.assign_team if s.assign_team in teams else default_team

    with st.container(key="card-default-more", gap="small"):
        markup('<h3 style="font-size:15px;font-weight:700">팀에서 직접 고르기</h3>'
               '<p class="hint">팀을 고르면 구성원과 점수(스킬 · 부서·역할 · 업무 여유)가 나와요. 진행 중 업무는 건수만 보여요.</p>')
        button_group([(tid, f"{t.name} {len(by_team[tid])}") for tid, t in teams.items()], team_id,
                     lambda v: actions.update(assign_team=v), key="assign_team")
        members = sorted(by_team[team_id], key=lambda c: c.score, reverse=True)  # 종합 점수 높은 순
        markup(f'<b style="font-size:14px">{esc(teams[team_id].name)}</b> <span class="hint">구성원 {len(members)}명</span>')
        for c in members:
            _candidate_row(c, cfg, top_rank.get(c.id))
        if not members:
            markup('<p class="hint" style="padding:20px 0;text-align:center;font-size:14px">이 팀에는 고를 수 있는 사람이 없어요.</p>')



@st.dialog("담당자 지정", width="large", on_dismiss=actions.close_modal)
def assign_dialog() -> None:
    s = get_state()
    cfg = repo.assignee_config()
    cands: list[Candidate] = cfg["candidates"]
    current = s.assigned.get(s.assign_key, [])
    markup(f'<h3 style="font-size:17px;font-weight:700">{esc(s.assign_text)}</h3>'
           '<p class="hint" style="font-size:14px;color:#5F646D">회의록·보유 스킬·현재 업무량으로 점수를 매겼어요. '
           "여러 명에게 함께 맡길 수 있고, 최종 선택은 주관자가 해요.</p>"
           + (f'<p class="hint" style="font-size:13px;color:#2B2F36;margin-top:4px">현재 담당: <b>{esc(", ".join(current))}</b> · '
              "회의를 확정하기 전까지 바꿀 수 있어요</p>" if current else ""))
    # 모달이 길어지지 않도록 본문은 고정 높이 안에서 스크롤 (높이는 pages.css 의 .st-key-assign-body)
    with st.container(height=440, border=False, key="assign-body", gap="medium"):
        _top3(cands)
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
        label = f"{len(chosen)}명에게 맡기기" if chosen else ("담당자 비우기" if current else "사람을 골라 주세요")
        if button(label, key="assign_ok", variant="primary", disabled=not chosen and not current):
            actions.assign_to()
            st.rerun()
