"""액션 아이템 직접 추가 · 수정 (주관자만): 내용 + 담당자(여러 명 가능) + 마감일 → 추가하면 원문을 다시 훑는 동안 로딩 화면.

담당자 칸을 누르면 「담당자 선택」 창(새 회의록 참석자 선택과 같은 사람 찾기)으로 바뀌고,
고르거나 돌아오면 다시 이 모달로 와요. 적어 둔 내용 · 마감일은 AppState.todo_draft_text / todo_draft_due 에 남아 있어요.
"""

from __future__ import annotations

import streamlit as st

from components.dialogs.compose_pickers import people_picker
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state, me_id
from ui.buttons import button
from ui.html import markup
from utils.text import esc


def _host_only(m) -> bool:
    if sel.manages(m):
        return True
    markup('<p class="hint" style="font-size:13px">액션 아이템은 회의 주관자(검토자가 있으면 검토자)만 추가 · 수정할 수 있어요.</p>')
    return False


def _todo_form(m, *, editing: bool) -> None:
    """추가 · 수정 모달의 공통 입력칸 (내용 · 담당자 · 마감일)."""
    s = get_state()
    text_key = f"todo_text_{s.todo_nonce}"
    text = st.text_area("액션 아이템 내용", value=s.todo_draft_text, key=text_key, height=90, max_chars=200,
                        placeholder="예: 결제 오류 고객 안내 문구 초안 작성")

    owners = list(s.todo_draft_owners)
    markup('<p class="todo-field-label">담당자</p>')
    # 모달 안 버튼은 모달만 다시 그려서, 담당자 선택 창으로 바꾸려면 st.rerun() 으로 앱 전체를 다시 그려야 해요
    label = f"담당자 {len(owners)}명 · 바꾸기" if owners else "담당자 선택 (여러 명 가능 · 고르지 않으면 담당 미정)"
    if button(label, key="todo_owner_field", variant="field" if owners else "fieldempty", icon=":material/group:",
              width="stretch"):
        actions.open_todo_owner_picker(text)
        st.rerun()
    if owners:  # 고른 사람 칩 · X 로 한 명씩 빼기
        with st.container(key="todo-owner-chips", horizontal=True, gap="xsmall"):
            for pid in owners:
                name = sel.name_of(pid) + (" (나)" if pid == me_id() else "")
                button(name, key=f"todo_owner_rm_{pid}", variant="chip", icon=":material/close:",
                       help="담당자에서 빼기", on_click=actions.remove_todo_owner, args=(pid,))

    agenda = ""
    if not editing:  # 추가할 때만 안건을 고름 (AI가 만든 할 일의 안건은 그대로)
        options = sel.agenda_options(m)
        agenda_key = f"todo_agenda_{s.todo_nonce}"
        agenda = st.selectbox("안건", options,
                              index=options.index(s.todo_draft_agenda) if s.todo_draft_agenda in options else None,
                              placeholder="어느 안건의 일인지 골라 주세요", key=agenda_key,
                              help="어느 안건에도 해당하지 않으면 「기타」를 골라 주세요.",
                              on_change=actions.keep_todo_agenda, args=(agenda_key,)) or ""

    first_day = sel.meeting_date(m)
    value = s.todo_draft_due
    if value and value < first_day:  # 회의 날짜보다 앞선 기존 마감일은 고를 수 없어서 비워 둠
        markup(f'<p class="hint" style="font-size:13px;color:#B3261E">기존 마감일({value:%Y/%m/%d})이 회의 날짜보다 앞서요. '
               '다시 골라 주세요. 저장하면 비워진 값으로 바뀌어요.</p>')
        value = None
    due_key = f"todo_due_{s.todo_nonce}"
    due = st.date_input("마감일", value=value, min_value=first_day, key=due_key, format="YYYY/MM/DD",
                        on_change=actions.keep_todo_due, args=(due_key,), help="비워 두면 마감일 없이 저장돼요.")

    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="todo_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        submit = button("저장" if editing else "추가", key="todo_submit", variant="primary")
    if submit:
        if not (text or "").strip():
            markup('<p class="hint" style="font-size:13px;color:#B3261E">액션 아이템 내용을 적어 주세요.</p>')
        elif outside := [sel.name_of(p) for p in owners if p not in m.people]:
            markup(f'<p class="hint" style="font-size:13px;color:#B3261E">담당자는 이 회의 참석자만 지정할 수 있어요: '
                   f'{esc(", ".join(outside))}</p>')
        elif not editing and not agenda:
            markup('<p class="hint" style="font-size:13px;color:#B3261E">안건을 골라 주세요.</p>')
        elif editing:
            if actions.submit_todo_edit(m.id, s.todo_edit_index, text, owners, due):
                st.rerun()
        elif actions.submit_todo(m.id, text, owners, due, agenda):
            st.rerun()


@st.dialog("액션 아이템 추가", width="small", on_dismiss=actions.close_modal)
def add_todo_dialog() -> None:
    m = repo.meeting(get_state().meeting_id)
    if not _host_only(m):
        return
    note = ("「확정하기」를 누르면 AI가 회의 원문에서 이 할 일의 근거가 된 발언을 찾아 연결해요."
            if m.id == actions.PREVIEW_ID else "추가하면 AI가 회의 원문을 다시 훑어 이 할 일의 근거가 된 발언을 찾아 연결해요.")
    markup(f'<p class="hint" style="font-size:13px;color:#5F646D">{esc(note)}</p>')
    _todo_form(m, editing=False)


@st.dialog("액션 아이템 수정", width="small", on_dismiss=actions.close_modal)
def edit_todo_dialog() -> None:
    m = repo.meeting(get_state().meeting_id)
    if not _host_only(m):
        return
    if m.id == actions.PREVIEW_ID:
        markup('<p class="hint" style="font-size:13px;color:#5F646D">값을 바꾸면 「확정하기」를 누를 때 근거 발언을 다시 연결해요.</p>')
    _todo_form(m, editing=True)


@st.dialog("담당자 선택", width="medium", on_dismiss=actions.back_to_add_todo)
def todo_owner_dialog() -> None:
    s = get_state()
    if not _host_only(repo.meeting(s.meeting_id)):
        return
    m = repo.meeting(s.meeting_id)
    attendees = [m.host, *[pid for pid in m.people if pid != m.host]]  # 주관자 먼저
    people_picker("todo_owner", single=False, title=f"선택한 담당자 {len(s.pick_draft)}명",
                  hint="이름을 누르면 선택 · 다시 누르면 해제 (여러 명 가능)",
                  featured=attendees, featured_label=f"이 회의 참석자 {len(attendees)}")
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("← 이전화면으로", key="todo_owner_back", variant="secondary"):
            actions.back_to_add_todo()
            st.rerun()
        n = len(s.pick_draft)
        if button(f"{n}명 선택" if n else "담당 미정으로 두기", key="todo_owner_ok", variant="primary"):
            actions.back_to_add_todo(confirm=True)
            st.rerun()


@st.dialog("담당자 변경", width="medium", on_dismiss=actions.close_modal)
def owner_change_dialog() -> None:
    """담당자 추천 화면 「담당자 변경」: 새 회의록의 참석자 고르기와 같은 사람 찾기 창 (소속별 조직도 · 검색 · 최근 · 즐겨찾기)."""
    s = get_state()
    markup(f'<p class="hint owner-change-target">액션 아이템 <b>{esc(s.assign_text)}</b></p>')
    people_picker("owner_change", single=False, title=f"선택한 담당자 {len(s.pick_draft)}명",
                  hint="이름을 누르면 선택 · 다시 누르면 해제 (여러 명 가능)")
    with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
        if button("취소", key="owner_change_cancel", variant="secondary"):
            actions.close_modal()
            st.rerun()
        n = len(s.pick_draft)
        if button(f"{n}명으로 변경" if n else "사람을 골라 주세요", key="owner_change_ok", variant="primary",
                  disabled=not n):
            actions.confirm_owner_change()
            st.rerun()
