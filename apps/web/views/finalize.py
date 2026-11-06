"""회의록 확정 화면 — 새 회의록 · 회의 상세의 「확정하기」 → 4단계.

  1) 담당자 추천: 액션 아이템마다 담당자 확인 · 담당자가 없던 일은 추천 후보(담당자 지정 창)에서 고르기
  1) 담당 미정인 일을 맨 위에 · 오른쪽 위 「← 이전 화면」(회의 상세) · 「다음: 최종 회의록 →」
  2) 최종 회의록: 회의 상세와 같은 화면(원문 · 요약 · 액션 아이템 …) · 고칠 수 없음 (추가 · 삭제 · 담당자 지정 없음)
     오른쪽 위 회의 삭제 · 회의 공유하기 · 결과 전달
     → 「결과 전달」 → 3단계
  3) 결과 전달 (views/delivery.py)
  4) 외부 도구 전송 (views/export.py)

3 · 4단계 화면 위에도 같은 단계 표시(flow_stepper)를 보여 줘요 (확정 흐름으로 들어왔을 때만).
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import empty_state, page, page_header
from ui.tags import tag
from utils.text import esc
from views.meeting import model
from views.meeting.header import render_final_header
from views.meeting.insight_tabs import render_insights
from views.meeting.raw_panel import render_raw_panel

STEPS = ("담당자 추천", "최종 회의록", "결과 전달", "외부 도구 전송")


def flow_stepper(step: int) -> None:
    """확정 흐름 단계 표시 (새 회의록 3단계와 같은 모양)."""
    items = []
    for i, name in enumerate(STEPS, 1):
        state = "done" if i < step else ("on" if i == step else "todo")
        mark = "✓" if state == "done" else str(i)
        items.append(f'<span class="wz-step {state}"><span class="dot">{mark}</span>{esc(name)}</span>')
    markup('<div class="wz-steps">' + '<span class="wz-arrow">→</span>'.join(items) + "</div>")


def in_flow(s: AppState, mid: str) -> bool:
    """회의 상세의 「확정하기」로 시작한 흐름 안인지 (결과 전달 · 외부 도구 전송 화면에서 단계 표시용)."""
    return s.fin_flow == mid


def _box(html: str, tone: str = "info") -> None:
    markup(f'<div class="wz-box wz-{tone}">{html}</div>')


# ================================================================ 1단계: 담당자 추천
def _todo_row(t: model.TodoView, assignable: set[str]) -> None:
    with st.container(key=f"fintodo-{t.index}", horizontal=True, vertical_alignment="center", gap="medium"):
        tags = "".join(tag(name, tone) for name, tone in t.who_tags)
        due = f'<span class="todo-due{" urgent" if t.urgent else ""}">{esc(t.due)}</span>'
        st.html(f'<div class="fin-todo"><p class="todo-title">{esc(t.text)}</p>'
                f'<div class="todo-meta">{tags}{due}</div></div>', width="stretch")
        if t.key in assignable:  # 회의록에 담당자가 없던 일만 추천 · 변경 (확정 전까지)
            if t.unassigned:
                button("담당자 추천 보기", key=f"fin_assign_{t.key}", variant="primary",
                       icon=":material/person_search:", on_click=actions.open_assign, args=(t.key, t.text))
            else:
                button("변경", key=f"fin_assign_{t.key}", variant="secondary",
                       on_click=actions.open_assign, args=(t.key, t.text))


def _step_assign(s: AppState, m: Meeting) -> None:
    items = model.todos(s, m)
    assignable = {key for key, *_ in model.assignable(s, m)}  # 회의록에서 담당자가 정해지지 않았던 일
    left = [t for t in items if t.unassigned]  # 담당 미정은 맨 위에
    with st.container(key="wz-card", gap="medium"):
        # 제목 줄 오른쪽 위: 이전 화면 · 다음 단계
        with st.container(key="fin-head", horizontal=True, vertical_alignment="center", gap="small"):
            markup(f'<div class="wz-head"><h2>담당자 추천</h2><span class="wz-badge">액션 아이템 {len(items)}건</span></div>')
            st.space("stretch")
            button("← 이전 화면", key="fin_back", variant="secondary", on_click=actions.open_meeting, args=(m.id,))
            button("다음: 최종 회의록 →", key="fin_next", variant="primary", on_click=actions.finalize_goto, args=(2,))
        if left:
            _box(f"<b>담당 미정 {len(left)}건</b><span>「담당자 추천 보기」를 눌러 추천 후보 중에서 골라 주세요. "
                 "그대로 넘어가도 되지만, 결과를 전달한 뒤에는 담당자를 바꿀 수 없어요.</span>", "warn")
        elif items:
            _box("<b>모든 액션 아이템에 담당자가 있어요</b><span>바로 다음 단계로 넘어가면 돼요.</span>", "ok")
        else:
            _box("<span>이 회의에서 나온 액션 아이템이 없어요. 바로 다음 단계로 넘어가면 돼요.</span>")
        with st.container(key="fin-todos", gap="small"):
            for t in left + [t for t in items if not t.unassigned]:
                _todo_row(t, assignable)


# ================================================================ 2단계: 최종 회의록 (회의 상세와 같은 화면 · 고칠 수 없음)
def _step_final(s: AppState, m: Meeting) -> None:
    render_final_header(s, m, deliver=True)  # 회의 삭제 · 회의 공유하기 · 결과 전달 (편집 없음)
    with st.container(key="md-split"):
        left, right = st.columns([1, 1.15], gap=None)
        with left, st.container(key="md-left", gap="small"):
            render_raw_panel(s, m)
        with right, st.container(key="md-right", gap=None):
            render_insights(s, m, mode="final")  # 액션 아이템 추가 · 삭제 · 담당자 지정 없음


# ================================================================ 진입점
def render() -> None:
    s = get_state()
    m = repo.meeting(s.meeting_id)
    with page("finalize"):
        if m.id in s.deleted_meetings:  # 최종 회의록에서 「회의 삭제」
            empty_state("주관자가 삭제한 회의예요. 회의록과 정리된 내용을 더 볼 수 없어요.", title="삭제된 회의예요", pad=48)
            return
        if s.fin_step != 2:  # 최종 회의록은 회의 상세와 같은 머리말을 씀
            page_header("회의록 확정", f"{m.title} · 담당자를 정하고 최종 회의록을 확인한 뒤 결과를 전달해요")
        if sel.my_role(m) != "host":
            markup('<p class="hint" style="font-size:14px">회의록은 주관자만 확정할 수 있어요.</p>')
            return
        if sel.status_of(s, m.id) != "review":
            markup('<p class="hint" style="font-size:14px">이미 확정한 회의록이에요.</p>')
            button("회의 상세로", key="fin_done_back", variant="secondary", on_click=actions.open_meeting, args=(m.id,))
            return
        flow_stepper(s.fin_step)
        if s.fin_step == 2:
            _step_final(s, m)
        else:
            _step_assign(s, m)
