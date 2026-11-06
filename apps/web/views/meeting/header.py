"""회의 상세 머리말: 경로 되돌아가기, 제목·역할, 참석자, 편집/공유/확정 버튼."""

from __future__ import annotations

import streamlit as st

from components.member_pills import member_pills
from data import repository as repo
from data.models import Meeting
from state import actions
from state import selectors as sel
from state.store import AppState
from ui.buttons import button
from ui.html import markup
from ui.layout import notice
from ui.tags import tag
from utils.text import esc
from views.meeting import model


def _title(s: AppState, m: Meeting, tag_html: str) -> None:
    folder = sel.folder_label(s, m)  # 제목을 크게 · 위에 폴더(어느 회의인지)를 작은 글씨로
    markup((f'<div class="md-eyebrow">{esc(folder)}</div>' if folder else "")
           + f'<div class="md-title-row"><h1 class="md-title">{esc(m.title)}</h1>{tag_html}</div>')


def _people_row(m: Meeting) -> None:
    # (왼쪽) 일시 · 참석자 알약  /  (오른쪽, 위 버튼과 같은 오른쪽 끝) 참석자 보기
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        button("참석자 보기", key="open_people", variant="secondary", icon=":material/group:",
               on_click=actions.open_modal, args=("people",))


def _shared_notice(s: AppState) -> None:
    if s.just_shared:
        notice(f"{esc(s.last_share_to)}에게 공유했어요. 「공유한 회의」에서 확인할 수 있어요.", "success")


def render_final_header(s: AppState, m: Meeting, *, deliver: bool) -> None:
    """최종 회의록 머리말 (편집 없음): 삭제 · 공유 · (deliver) 결과 전달 / (전달 뒤) 외부 도구 전송.

    deliver: 회의록 확정 2단계 — 아직 결과 전달 전이라 「결과 전달」 버튼
    """
    host = model.is_host(m)
    role = sel.my_role(m)
    role_label, role_tone = ("내가 주관", "host") if role == "host" else sel.ROLE_TAGS[role]
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(gap="small"):
            _title(s, m, tag("최종 회의록", "ok") + tag(role_label, role_tone))
        with st.container(horizontal=True, width="content", gap="small"):
            if host:
                button("삭제", key="md_delete", variant="dangerline", icon=":material/delete:",
                       on_click=actions.open_delete, args=("meeting", m.id))
                button("공유", key="md_share", variant="secondary", icon=":material/share:",
                       on_click=actions.open_share)
                if deliver:  # → 결과 전달 화면 (그다음 외부 도구 전송)
                    button("결과 전달", key="md_deliver", variant="primary", icon=":material/send:",
                           on_click=actions.deliver_final, args=(m.id,))
                elif sel.status_of(s, m.id) in ("done", "fail"):  # 결과 전달이 끝난 회의: 외부 도구(MCP)로 (다시) 보내기
                    button("외부 도구 전송", key="md_export", variant="primary", icon=":material/output:",
                           on_click=actions.open_export, args=(m.id,))
            else:
                st.html(f'<span class="read-note">{esc(model.read_note(m))}</span>', width="content")
    _people_row(m)
    _shared_notice(s)
    if not deliver:
        _export_notices(s, m)


def render_header(s: AppState, m: Meeting) -> None:
    """회의 상세 머리말. 확정(결과 전달)한 회의는 최종 회의록과 같은 머리말.

    편집은 다른 사람이 등록해 주관자에게 검토를 요청한 회의에서만 (그 밖의 검토는 새 회의록을 만들 때 끝냄).
    """
    if sel.status_of(s, m.id) != "review":
        render_final_header(s, m, deliver=False)
        return
    host = model.is_host(m)
    can_edit = host and m.id in s.review_requested  # 검토 요청을 받은 주관자만 편집
    editing = s.editing and can_edit
    role = sel.my_role(m)
    role_label, role_tone = ("내가 주관", "host") if role == "host" else sel.ROLE_TAGS[role]

    # 1행: (왼쪽) 제목·역할  /  (오른쪽) 삭제 · 공유 · (편집) · 확정하기 또는 읽기 전용 안내
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(gap="small"):
            _title(s, m, tag(role_label, role_tone)
                   + (tag("검토 요청 받음", "warn") if host and m.id in s.review_requested else ""))
        with st.container(horizontal=True, width="content", gap="small"):
            if host and not editing:
                button("삭제", key="md_delete", variant="dangerline", icon=":material/delete:",
                       on_click=actions.open_delete, args=("meeting", m.id))
                button("공유", key="md_share", variant="secondary", icon=":material/share:",
                       on_click=actions.open_share)
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
            notice(f"액션 아이템 {n}건을 {esc(tool.name)}(으)로 다시 보냈어요. ({esc(tool.target)})", "success")
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