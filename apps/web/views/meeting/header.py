"""회의 상세 머리말: 경로 되돌아가기, 제목·역할, 참석자, 편집/공유/확정 버튼."""

from __future__ import annotations

import streamlit as st

from components.member_pills import member_pills
from data import repository as repo
from data.models import Meeting
from state import actions
from state import minutes_text
from state import selectors as sel
from state.store import AppState
from ui.buttons import button
from ui.html import markup
from ui.layout import notice
from ui.tags import stage_badge, tag
from utils.text import esc
from views.meeting import model


def _title(s: AppState, m: Meeting, tag_html: str, *, boxed: bool = False, box_class: str = "") -> None:
    """boxed: 최종 회의록 머리말 — 제목 · 태그를 왼쪽 주황 줄 상자로 감싸 눈에 띄게 (.md-title-box · pages.css)
    (폴더 이름은 상자 밖 위에 · 상자와 살짝 띄움)
    """
    folder = sel.folder_label(s, m)  # 제목을 크게 · 위에 폴더(어느 회의인지)를 작은 글씨로
    row = f'<div class="md-title-row"><h1 class="md-title">{esc(m.title)}</h1>{tag_html}</div>'
    markup((f'<div class="md-eyebrow">{esc(folder)}</div>' if folder else "")
           + (f'<div class="md-title-box {box_class}">{row}</div>' if boxed else row))


def _people_row(m: Meeting) -> None:
    # (왼쪽) 일시 · 참석자 알약  /  (오른쪽, 위 버튼과 같은 오른쪽 끝) 참석자 보기
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        button("참석자 보기", key="open_people", variant="secondary", icon=":material/group:",
               on_click=actions.open_modal, args=("people",))


def _shared_notice(s: AppState) -> None:
    if s.just_shared:
        notice(f"{esc(s.last_share_to)}에게 공유했어요. 「공유한 회의」에서 확인할 수 있어요.", "success")


def _minutes_download(s: AppState, m: Meeting) -> None:
    """회의록 TXT 다운로드 · 사용자 수정과 근거 연결까지 끝난 최종 JSON이 있는 회의만."""
    final = s.final_summaries.get(m.id)
    if not final:
        return
    with st.container(key="b-secondary-md_minutes", width="content"):
        st.download_button("회의록 다운로드", minutes_text.render_minutes(final), file_name=minutes_text.minutes_filename(final),
                           mime="text/plain", key="md_minutes", icon=":material/download:")


def render_final_header(s: AppState, m: Meeting, *, deliver: bool) -> None:
    """최종 회의록 머리말 (편집 없음): 삭제 · 회의록 다운로드 · 공유 · (deliver) 결과 전달 / (전달 뒤) 외부 도구 전송.

    deliver: 회의록 확정 2단계 — 아직 결과 전달 전이라 「결과 전달」 버튼
    """
    host = model.is_host(m)  # 확정 · 결과 전달 (주관자 · 검토자)
    owner = sel.owns(m)  # 삭제 · 공유 (주관자 · 작성자 · 검토자는 못 함)
    role = sel.my_role(m)
    role_label, role_tone = ("내가 주관", "host") if role == "host" else sel.ROLE_TAGS[role]
    status = sel.status_of(s, m.id)
    stage = sel.stage_of(s, m.id)  # 결과 전달까지 마쳤어도 외부 도구 전송 전이면 unexported
    deliver_in_flow = deliver  # 회의록 확정 4단계 화면 (finalize) · 단계 표시가 위에 있음
    # 확정 흐름 밖에서 연 「확정 · 전달 전」 회의도 여기서 바로 결과 전달로 이어 갈 수 있게
    deliver = deliver or status == "confirmed"
    if s.fin_flow != m.id and not deliver_in_flow:  # 확정 흐름 중에는 위 단계 표시가 이미 알려 줌
        _stage_panel(s, m, stage, host)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(gap="small"):
            _title(s, m, tag("최종 회의록", "ok") + stage_badge(stage) + tag(role_label, role_tone), boxed=True)
        with st.container(horizontal=True, width="content", gap="small"):
            if owner:
                button("삭제", key="md_delete", variant="dangerline", icon=":material/delete:",
                       on_click=actions.open_delete, args=("meeting", m.id))
            _minutes_download(s, m)
            if owner:
                button("공유", key="md_share", variant="secondary", icon=":material/share:",
                       on_click=actions.open_share)
            if host:
                if deliver:  # → 결과 전달 화면 (그다음 외부 도구 전송)
                    button("결과 전달", key="md_deliver", variant="primary", icon=":material/send:",
                           on_click=actions.deliver_final, args=(m.id,))
                elif stage == "deliver_fail":  # 결과 전달에서 실패한 참석자 → 먼저 다시 보내기 (외부 도구는 그다음)
                    button("결과 재전송", key="md_redeliver", variant="primary", icon=":material/refresh:",
                           help="전송에 실패한 참석자에게만 다시 보내요",
                           on_click=actions.start_delivery, args=(m.id, 2))
                elif status in ("done", "fail"):  # 결과 전달이 끝난 회의: 외부 도구(MCP)로 (다시) 보내기
                    button("외부 도구 전송", key="md_export", variant="primary", icon=":material/output:",
                           on_click=actions.open_export, args=(m.id,))
            else:
                st.html(f'<span class="read-note">{esc(model.read_note(m))}</span>', width="content")
    _people_row(m)
    _shared_notice(s)
    if not deliver:
        _export_notices(s, m)


# 남은 단계가 있는 회의 → 회의 상세 맨 위 진행 상황 상자
#   stage: (제목, 지금 차례인 단계(1~6, finalize.STEPS), 색, 주관자 안내, 그 밖의 사람 안내)
_STAGE_PANEL = {
    "review": ("검토 진행 중인 회의록이에요", 3, "warn",
               "오른쪽 위 「확정하기」를 눌러 담당자 추천 → 최종 회의록 → 결과 전달 → 외부 도구 전송까지 마쳐 주세요.",
               "주관자가 검토를 마치고 확정하면 최종 회의록이 돼요."),
    "confirmed": ("최종 회의록은 만들어졌지만, 아직 결과를 전달하지 않았어요", 5, "warn",
                  "오른쪽 위 「결과 전달」을 눌러 참석자에게 보내고, 이어서 외부 도구 전송까지 마쳐 주세요.",
                  "주관자가 결과를 전달하면 할 일 알림을 받을 수 있어요."),
    "sending": ("참석자에게 결과를 전달하는 중이에요", 5, "run", "", ""),
    "unexported": ("결과는 전달했지만, 아직 외부 도구로 보내지 않았어요", 6, "warn",
                   "오른쪽 위 「외부 도구 전송」을 눌러 Jira · 메일로 보내면 모든 단계가 끝나요.",
                   "주관자가 외부 도구 전송까지 마치면 모든 단계가 끝나요."),
    "deliver_fail": ("참석자 일부에게 결과를 전달하지 못했어요", 5, "fail",
                     "오른쪽 위 「결과 재전송」을 눌러 실패한 참석자에게 다시 보내 주세요. "
                     "모두 전달된 뒤에 외부 도구 전송으로 넘어갈 수 있어요.",
                     "주관자가 결과를 다시 보내면 할 일 알림을 받을 수 있어요."),
    "fail": ("외부 도구로 보내지 못한 곳이 있어 다시 보내야 해요", 6, "fail",
             "아래 안내에서 다시 보내 주세요.", "주관자가 다시 보내면 모든 단계가 끝나요."),
}


def _stage_panel(s: AppState, m: Meeting, stage: str, host: bool) -> None:
    """남은 단계가 있으면 맨 위에 색 상자: 제목 · 1~6단계 중 지금 어디인지 · 무엇을 하면 되는지."""
    if stage not in _STAGE_PANEL:
        return
    from views.finalize import STEPS  # finalize 가 이 파일을 불러서 함수 안에서

    title, step, tone, host_text, other_text = _STAGE_PANEL[stage]
    items = []
    for i, name in enumerate(STEPS, 1):
        state = "done" if i < step else ("on" if i == step else "todo")
        mark = "✓" if state == "done" else str(i)
        items.append(f'<span class="sp-step {state}"><span class="dot">{mark}</span>{esc(name)}</span>')
    arrow = '<span class="sp-arrow">›</span>'
    text = host_text if host else other_text
    markup(f'<div class="stage-panel stage-{tone}" role="status">'
           f'<div class="sp-head"><span class="sp-title">{esc(title)}</span>'
           f'<span class="sp-left">남은 단계 {len(STEPS) - step + 1}개</span></div>'
           f'<div class="sp-steps">{arrow.join(items)}</div>'
           + (f'<p class="sp-text">{esc(text)}</p>' if text else "") + "</div>")


def render_header(s: AppState, m: Meeting) -> None:
    """회의 상세 머리말. 확정(결과 전달)한 회의는 최종 회의록과 같은 머리말.

    편집은 다른 사람이 등록해 주관자에게 검토를 요청한 회의에서만 (그 밖의 검토는 새 회의록을 만들 때 끝냄).
    """
    if sel.status_of(s, m.id) != "review":
        render_final_header(s, m, deliver=False)
        return
    host = model.is_host(m)  # 편집 · 확정 (주관자 · 검토자)
    owner = sel.owns(m)  # 삭제 · 공유 (주관자 · 작성자 · 검토자는 못 함)
    # 편집: 검토 요청을 받은 사람(주관자 · 신입이 고른 윗사람) + 검토자를 둔 신입 주관자
    can_edit = host and (sel.review_requested(s, m) or bool(m.reviewer))
    editing = s.editing and can_edit
    role = sel.my_role(m)
    role_label, role_tone = ("내가 주관", "host") if role == "host" else sel.ROLE_TAGS[role]

    if not editing and s.fin_flow != m.id:  # 확정 흐름 중에는 위 단계 표시가 이미 알려 줌
        _stage_panel(s, m, "review", host)
    # 1행: (왼쪽) 제목·역할  /  (오른쪽) 삭제 · 공유 · (편집) · 확정하기 또는 읽기 전용 안내
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(gap="small"):
            _title(s, m, stage_badge("review") + tag(role_label, role_tone)
                   + (tag("검토 요청 받음", "warn") if host and sel.review_requested(s, m) else ""),
                   boxed=True, box_class="review")
        with st.container(horizontal=True, width="content", gap="small"):
            if owner and not editing:
                button("삭제", key="md_delete", variant="dangerline", icon=":material/delete:",
                       on_click=actions.open_delete, args=("meeting", m.id))
                if s.fin_flow != m.id:  # 확정하기를 누른 뒤(확정 흐름 중)에는 공유 빼기
                    button("공유", key="md_share", variant="secondary", icon=":material/share:",
                           on_click=actions.open_share)
            if host and not editing:
                if can_edit:
                    button("편집", key="md_edit", variant="secondary", icon=":material/edit:", on_click=actions.start_edit)
                # 가장 오른쪽 · 누르면 회의록 확정 화면(담당자 추천 → 최종 회의록 → 결과 전달 → 외부 도구 전송)
                button("확정하기", key="md_confirm", variant="primary", icon=":material/task_alt:",
                       on_click=actions.start_finalize, args=(m.id,))
            elif host:
                button("취소", key="md_cancel", variant="secondary", on_click=actions.stop_edit)
                button("확정하기", key="md_confirm", variant="primary", icon=":material/task_alt:",
                       on_click=actions.start_finalize, args=(m.id,))
            else:
                st.html(f'<span class="read-note">{esc(model.read_note(m))}</span>', width="content")

    _people_row(m)
    if editing:
        notice("편집 중이에요. 요약·안건을 고친 뒤 <b>확정하기</b>를 누르면 담당자 추천 → 최종 회의록 확인을 거쳐 참석자에게 결과가 전달돼요.")
    _shared_notice(s)
    _export_notices(s, m)


def _export_notices(s: AppState, m: Meeting) -> None:
    """외부 도구(Slack · Jira 등)로 보내다 실패한 회의록에서만.

    - 단순 이슈(일시적인 네트워크 오류 등): 사용자가 「다시 보내기」로 직접 해결
    - 기술 이슈(API · 인증 문제): 안내 문구만 (「관리자에게 문의」 모달은 결과 전달 화면에서 전송이 끝날 때 뜸)
    """
    for f in repo.export_failures(m.id):
        tool = repo.tool(f.tool)
        n = len(sel.todo_items(s, m.id))
        if not sel.failure_open(s, f.id):
            notice(f"액션 아이템 {n}건을 {esc(tool.name)}(으)로 다시 보냈어요."
                   + (f" ({esc(tool.target)})" if tool.target else ""), "success")  # 보낼 곳이 비어 있으면 괄호 없이
            continue
        with st.container(key=f"export-{f.id}", horizontal=True, vertical_alignment="center", gap="small"):
            if f.kind == "simple":
                notice(f"{esc(f.reason)} 액션 아이템 {n}건을 {esc(tool.name)}(으)로 보내지 못했어요. "
                       + ("다시 보내 보세요." if model.is_host(m) else "주관자가 다시 보낼 수 있어요."), "danger")
                if model.is_host(m):
                    button("다시 보내기", key=f"export_resend_{f.id}", variant="secondary", icon=":material/refresh:",
                           on_click=actions.resend_export, args=(f.id,))
            else:
                notice(f"{esc(tool.name)} 연동 문제로 액션 아이템 {n}건을 보내지 못했어요. "
                       "연동 설정은 관리자만 고칠 수 있어요.", "danger")