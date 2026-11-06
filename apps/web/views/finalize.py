"""회의록 확정 화면 — 새 회의록 · 회의 상세의 「확정하기」 → 4단계.

  1) 담당자 추천: 액션 아이템마다 담당자 확인 · 담당자가 없던 일은 추천 후보(담당자 지정 창)에서 고르기
  1) 회의록에서 담당자가 없던 일을 맨 위에 (고른 뒤에도 그 자리) · 제목 줄 오른쪽 「← 이전화면으로」 · 「다음: 최종 회의록 →」
  2) 최종 회의록: 회의 상세와 같은 화면(원문 · 요약 · 액션 아이템 …) · 고칠 수 없음 (추가 · 삭제 · 담당자 지정 없음)
     오른쪽 위 삭제 · 공유 · 결과 전달
     → 「결과 전달」 → 3단계
  3) 결과 전달 (views/delivery.py)
  4) 외부 도구 전송 (views/export.py)

단계 표시(flow_stepper)는 새 회의록 작성부터 이어지는 6단계 하나예요:
  1 회의 내용 입력 → 2 분석 및 검토 → 3 담당자 추천 → 4 최종 회의록 → 5 결과 전달 → 6 외부 도구 전송
(결과 전달 · 외부 도구 전송 화면에서는 확정 흐름으로 들어왔을 때만 보여요)
"""

from __future__ import annotations

import re
from datetime import date

import streamlit as st

from data import repository as repo
from data.api import ApiError
from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import empty_state, page, page_header, step_header
from ui.loading import loading_overlay
from ui.tags import tag
from utils.text import esc
from views.meeting import model
from views.meeting.header import render_final_header
from views.meeting.insight_tabs import render_insights
from views.meeting.raw_panel import render_raw_panel

# 새 회의록 작성부터 외부 도구 전송까지 한 줄로 이어지는 단계 (각 화면 맨 위에 같은 모양으로)
STEPS = ("회의 내용 입력", "분석 및 검토", "담당자 추천", "최종 회의록", "결과 전달", "외부 도구 전송")
FIN_OFFSET = 2  # 회의록 확정 화면의 1단계(담당자 추천)는 전체에서 3단계


def flow_stepper(step: int) -> None:
    """확정 흐름 단계 표시 (새 회의록 3단계와 같은 모양)."""
    items = []
    for i, name in enumerate(STEPS, 1):
        state = "done" if i < step else ("on" if i == step else "todo")
        mark = "✓" if state == "done" else str(i)
        items.append(f'<span class="wz-step {state}"><span class="dot">{mark}</span>{esc(name)}</span>')
    markup('<div class="wz-steps">' + '<span class="wz-arrow">→</span>'.join(items) + "</div>")


def flow_step_of(s: AppState, mid: str) -> int:
    """확정 흐름 중 회의 상세로 왔을 때 단계 표시에서 지금 차례인 단계.

    확정 전: 담당자 추천(3) · 최종 회의록(4) 중 보던 단계 / 결과 전달 중: 5 / 결과 전달이 끝남: 다음 차례인 외부 도구 전송(6)
    """
    status = sel.status_of(s, mid)
    if status == "review":
        return s.fin_step + FIN_OFFSET
    return 5 if status == "sending" else 6


def in_flow(s: AppState, mid: str) -> bool:
    """회의 상세의 「확정하기」로 시작한 흐름 안인지 (결과 전달 · 외부 도구 전송 화면에서 단계 표시용)."""
    return s.fin_flow == mid


def _box(html: str, tone: str = "info") -> None:
    markup(f'<div class="wz-box wz-{tone}">{html}</div>')


# ================================================================ 1단계: 담당자 추천
def _due_date(due: str) -> date | None:
    """「11/5」 · 「11/5까지」 → 날짜 (올해) · 「확인 필요」처럼 날짜가 아니면 None."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})", due or "")
    if not m:
        return None
    try:
        return date(date.today().year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _todo_row(s: AppState, t: model.TodoView, recommend: bool) -> None:
    """할 일 · 담당자 태그 | 마감일(고치기) | 담당자 추천 보기(담당 미정) · 담당자 변경 — 모든 액션 아이템."""
    with st.container(key=f"fintodo-{t.index}", horizontal=True, vertical_alignment="center", gap="medium"):
        # 할 일(굵게) 아래 한 줄 띄우고 「담당 [사람 태그]  |  기한 날짜」 — 라벨을 붙여 한눈에 구분
        tags = "".join(tag(name, tone) for name, tone in t.who_tags)
        edited = '<span class="fin-edited">수정함</span>' if t.key in s.todo_due else ""
        due = f'<span class="todo-due{" urgent" if t.urgent else ""}">{esc(t.due)}</span>{edited}'
        st.html(f'<div class="fin-todo"><p class="fin-todo-title">{esc(t.text)}</p>'
                f'<div class="fin-todo-meta"><span class="lbl">담당</span><span class="who">{tags}</span>'
                f'<span class="sep"></span><span class="lbl">기한</span>{due}</div></div>', width="stretch")
        wk = f"fin_due_{t.key}"
        with st.container(key=f"findue-{t.index}", width=168, gap=None):
            st.date_input("마감일", value=_due_date(t.raw_due), key=wk, format="YYYY/MM/DD",
                          label_visibility="collapsed", on_change=actions.set_todo_due, args=(t.key, wk))
        if t.unassigned:
            button("담당자 추천 보기", key=f"fin_assign_{t.key}", variant="primary",
                   icon=":material/person_search:", on_click=actions.open_assign, args=(t.key, t.text))
        elif recommend:  # 회의록에 담당자가 없던 일: 고른 뒤에도 바꿀 때는 담당자 추천 창으로
            button("담당자 변경", key=f"fin_assign_{t.key}", variant="secondary", icon=":material/manage_accounts:",
                   on_click=actions.open_assign, args=(t.key, t.text))
        else:
            # 담당자가 있는 일: 추천 대신 새 회의록 참석자 고르기와 같은 사람 찾기 창 (소속별 조직도)
            button("담당자 변경", key=f"fin_assign_{t.key}", variant="secondary", icon=":material/manage_accounts:",
                   on_click=actions.open_owner_change, args=(t.key, t.text))


def _load_recommendations(s: AppState, m: Meeting) -> bool:
    """처음 들어올 때 한 번: 검토가 끝난 요약을 Neo4j에 적재하고 담당자를 추천받아 둠 (목업 회의는 건너뜀).
    실패하면 안내와 「다시 시도」를 보여 주고 False (같은 요청을 다시 보내도 됨)."""
    if m.id not in s.summaries or m.id in s.recommendations:
        return True
    if not st.session_state.pop("_fin_rec_failed", None):  # 실패 직후 화면에서는 다시 부르지 않음 (「다시 시도」를 눌러야)
        loading_overlay("담당자를 추천하고 있어요", "회의록을 저장하고 액션 아이템마다 후보를 찾는 중이에요 · 1~2분 걸릴 수 있어요")
        try:
            s.recommendations[m.id] = repo.recommend_assignees(s.summaries[m.id])
            st.rerun()  # 로딩 표시 지우기
        except ApiError as e:
            st.session_state["_fin_rec_error"] = str(e)
            st.session_state["_fin_rec_failed"] = True
            st.rerun()
    _box("<b>담당자 추천을 불러오지 못했어요</b>"
         f"<span>{esc(st.session_state.get('_fin_rec_error', ''))}</span>", "warn")
    button("다시 시도", key="fin_rec_retry", variant="primary")  # 누르면 다시 그려지면서 다시 요청
    return False


def _step_assign(s: AppState, m: Meeting) -> None:
    if not _load_recommendations(s, m):
        return
    items = model.todos(s, m)
    left = [t for t in items if t.unassigned]
    # 현재 단계 제목과 같은 줄 오른쪽: 이전화면으로 · 다음 단계 (모든 단계 화면에서 같은 자리)
    with step_header("담당자 추천", f"{m.title} · 액션 아이템 {len(items)}건 · 담당자와 마감일을 확인한 뒤 최종 회의록으로 넘어가요"):
        button("← 이전화면으로", key="fin_back", variant="secondary", on_click=actions.open_meeting, args=(m.id,))
        button("다음: 최종 회의록 →", key="fin_next", variant="primary", on_click=actions.confirm_assignees, args=(m.id,))
    with st.container(key="wz-card", gap="medium"):
        if left:
            _box(f"<b>담당 미정 {len(left)}건</b><span>「담당자 추천 보기」를 눌러 추천 후보 중에서 골라 주세요. "
                 "모든 액션 아이템의 마감일과 담당자를 여기서 고칠 수 있고, 결과를 전달한 뒤에는 바꿀 수 없어요.</span>", "warn")
        elif items:
            _box("<b>모든 액션 아이템에 담당자가 있어요</b><span>마감일과 담당자를 고칠 게 없으면 다음 단계로 넘어가면 돼요.</span>", "ok")
        else:
            _box("<span>이 회의에서 나온 액션 아이템이 없어요. 바로 다음 단계로 넘어가면 돼요.</span>")
        with st.container(key="fin-todos", gap="small"):
            # 회의록(원래 데이터)에서 담당자가 없던 일 → 늘 담당자 추천 창 (화면을 오가도 원래 데이터 기준이라 그대로)
            # 이 일들은 담당자를 고른 뒤에도 맨 위 자리 그대로 (지금 담당 미정인지가 아니라 원래 데이터 기준으로 정렬)
            recommend = {key for key, *_ in model.assignable(s, m)}
            for t in [t for t in items if t.key in recommend] + [t for t in items if t.key not in recommend]:
                _todo_row(s, t, t.key in recommend)


# ================================================================ 2단계: 최종 회의록 (회의 상세와 같은 화면 · 고칠 수 없음)
def _step_final(s: AppState, m: Meeting) -> None:
    render_final_header(s, m, deliver=True)  # 삭제 · 공유 · 결과 전달 (편집 없음)
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
        if m.id in s.deleted_meetings:  # 최종 회의록에서 「삭제」
            empty_state("주관자가 삭제한 회의예요. 회의록과 정리된 내용을 더 볼 수 없어요.", title="삭제된 회의예요", pad=48)
            return
        if sel.my_role(m) != "host" or sel.status_of(s, m.id) != "review":
            page_header("회의록 확정", m.title)
            if sel.my_role(m) != "host":
                markup('<p class="hint" style="font-size:14px">회의록은 주관자만 확정할 수 있어요.</p>')
            else:
                markup('<p class="hint" style="font-size:14px">이미 확정한 회의록이에요.</p>')
                button("← 이전화면으로", key="fin_done_back", variant="secondary", on_click=actions.open_meeting,
                       args=(m.id,))
            return
        flow_stepper(s.fin_step + FIN_OFFSET)  # 전체 흐름이 늘 맨 위 · 그 아래 이 단계의 제목
        if s.fin_step == 2:
            _step_final(s, m)
        else:
            _step_assign(s, m)
