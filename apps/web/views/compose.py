"""새 회의록 작성 (로그인 후 첫 화면) — 2단계.

  1) 회의 내용 입력: 기본 정보(제목 · 날짜 · 주관자 · 참석자 · 회의 종류·폴더) + 회의 내용(직접 쓰기 / TXT · MD · DOCX · HWPX 첨부)
     · 지원하는 입력 형식: 발화자 없는 텍스트(기본) · [발화자] 형식 · 발화자: 형식 중 하나를 고름
       고른 형식의 규칙은 회색 안내글로, 예시는 입력칸의 회색 글씨(placeholder)로 보여 줌
     · 주관자는 참석자에 늘 들어가요 (참석자 고르기에서 체크된 채로 · 뺄 수 없음)
     · 고른 형식에서 벗어나거나 발화자가 참석자 안에 없으면 「다음 단계」에서 경고 창이 뜨고 넘어가지 못해요
  2) 분석 및 검토: 회의 상세와 같은 화면(원문 · 요약 · 안건 · 액션 아이템 …)으로 미리 보고 (담당자 지정은 확정 뒤)
     · 주관자: 「확정하기」 → 확인 창 → 로딩 → 회의록 확정 화면(담당자 추천 → 최종 회의록 → 결과 전달 → 외부 도구 전송)
     · 주관자가 아닌 사람: 「검토 요청하기」 → 확인 창 → 로딩 → 주관자에게 검토 요청 알림 · 회의 상세로
분석 규칙과 회의록 만들기는 state/compose_ai.py 에 있어요 (실제 서비스에서는 AI 서버가 대신함).
"""

from __future__ import annotations

import json
import time

import streamlit as st

from components.member_pills import member_pills
from state.store import me_id
from data import repository as repo
from state import actions
from state import archive as arc
from state import compose_ai as ai
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import page, step_header
from ui.loading import loading_overlay
from ui.tags import tag
from utils.files import ALLOWED_LABEL, read_text
from utils.text import esc
from views.finalize import flow_stepper
from views.meeting.insight_tabs import render_insights
from views.meeting.raw_panel import render_raw_panel

# 회의 내용으로 읽을 수 있는 형식: TXT · MD · DOCX · HWPX (utils/files.py)
# 파일 고르는 창은 형식을 막지 않고, 「발화자 확인」 때 아니면 빨간 안내 + 다음 단계로 못 가게 함
_NEW = "new"


def _person_label(pid: str) -> str:
    p = sel.person(pid)
    return f"{p.name} · {p.team}" + (" (나)" if pid == me_id() else "")


def _people_text(pids: list[str]) -> str:
    if not pids:
        return ""
    names = [sel.person(p).name for p in pids]
    return ", ".join(names[:3]) + (f" 외 {len(names) - 3}명" if len(names) > 3 else "") + f"  ({len(names)}명)"


def _folder_text(s: AppState) -> str:
    """'주간 회의 › 프로젝트별 › 11월 릴리즈 준비' · '월간 회의 › 월별 › 11월 (새 폴더)'"""
    t = repo.meeting_types()[s.nn_type].name
    if s.nn_group == "period":
        if not s.nn_period:
            return ""
        new = "" if arc.period(s, s.nn_type, s.nn_period) else " (새 폴더)"
        return f"{t} › {arc.PERIOD_LABEL[s.nn_type]} › {arc.period_label(s.nn_period)}{new}"
    topics = sel.folder_ids_of_type(s, s.nn_type)
    if s.nn_topic in topics:
        return f"{t} › 프로젝트별 › {sel.folders(s)[s.nn_topic].name}"
    return f"{t} › 프로젝트별 › {s.nn_name} (새 폴더)" if s.nn_name.strip() else ""


def _label(label: str) -> None:
    """입력칸 이름 · 끝이 「 *」면 필수 표시(*)만 주황색."""
    text, req = (label[:-2], True) if label.endswith(" *") else (label, False)
    markup(f'<span class="cp-flabel">{esc(text)}' + (' <span class="req">*</span>' if req else "") + "</span>")


def _field(label: str, key: str, value: str, placeholder: str, icon_name: str, modal: str) -> None:
    """입력칸처럼 생긴 버튼: 누르면 선택 모달(폴더 · 주관자 · 참석자)이 떠요."""
    _label(label)
    button(value or placeholder, key=key, variant="field" if value else "fieldempty", icon=icon_name,
           on_click=actions.open_picker, args=(modal,), width="stretch")


def _stepper(step: int) -> None:
    """새 회의록 작성부터 외부 도구 전송까지 이어지는 6단계 표시 (이 화면은 1 · 2단계)."""
    flow_stepper(step)


def _card_head(title: str, badge: str = "") -> None:
    markup(f'<div class="wz-head"><h2>{esc(title)}</h2>' + (f'<span class="wz-badge">{esc(badge)}</span>' if badge else "")
           + "</div>")


def _info(html: str, tone: str = "info") -> None:
    """안내 상자. tone: info(회색) | ok(초록) | warn(주황) | error(빨강)"""
    markup(f'<div class="wz-box wz-{tone}">{html}</div>')


def _current_text(s: AppState, n: int) -> str:
    """지금 입력칸에 있는 글 + 첨부 파일 내용 (형식 검사용)."""
    typed = st.session_state.get(f"cp_text_{n}_{s.cp_text_rev}", s.nn_text) or ""
    files = st.session_state.get(f"cp_files_{n}") or []
    bodies = [read_text(f.name, f.getvalue()) for f in files] if files else [b for _, b in s.nn_files]
    bodies = [b for b in bodies if b]  # 지원하지 않는 형식 · 읽을 수 없는 파일은 빼고
    return "\n".join(x.strip() for x in [typed, *bodies] if x and x.strip())


def _basic_info(s: AppState, n: int) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup('<h2 class="cp-section">기본 정보</h2>')
        markup('<span class="cp-required"><span class="req">*</span> 필수 입력</span>')

    types = repo.meeting_types()
    folders = sel.folders(s)
    topics = sel.folder_ids_of_type(s, s.nn_type)
    topic = s.nn_topic if s.nn_topic in topics else _NEW

    with st.container(key="cp-grid", gap="small"):
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            # 기본 라벨은 * 만 색을 바꿀 수 없어서 숨기고(화면 낭독기용으로는 남음) 주관자 칸과 같은 라벨로 그림
            _label("회의 제목 *")
            st.text_input("회의 제목 (필수)", key=f"cp_title_{n}", value=s.nn_title, placeholder="예: 4분기 계획 회의",
                          label_visibility="collapsed")
        with c2:
            _label("회의 날짜 *")
            st.date_input("회의 날짜 (필수)", value=s.nn_date, key=f"cp_date_{n}", format="YYYY/MM/DD",
                          label_visibility="collapsed")
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            _field("주관자 *", "cp_host_field", _person_label(s.nn_host) if s.nn_host else "", "주관자 선택",
                   ":material/person:", "cp_host")
        with c2:
            _field("참석자", "cp_people_field", _people_text(s.nn_people), "참석자 선택",
                   ":material/group:", "cp_people")
        _field("회의 종류 · 폴더 *", "cp_folder_field", _folder_text(s), "회의 종류와 폴더 선택",
               ":material/folder:", "cp_folder")
    t = types[s.nn_type].name
    if s.nn_group == "period":
        if not s.nn_period:
            hint = "회의 종류와 폴더를 골라 주세요."
        elif arc.period(s, s.nn_type, s.nn_period):
            hint = f"{t} 「{arc.period_label(s.nn_period)}」 폴더에 쌓여요 · 참석자만 볼 수 있어요."
        else:
            hint = f"{t} 안에 「{arc.period_label(s.nn_period)}」 폴더가 새로 만들어져요."
    elif topic == _NEW:
        hint = (f"{t} 안에 「{s.nn_name}」 폴더가 만들어지고, 내가 폴더 주관자가 돼요." if s.nn_name.strip()
                else "회의 종류와 폴더를 골라 주세요.")
    else:
        hint = f"「{folders[topic].name}」 폴더에 쌓여요 · 참석자만 볼 수 있고, 빠진 멤버에게는 회의 상세에서 공유할 수 있어요."
    markup(f'<span class="cp-hint">{esc(hint)}</span>')


# ================================================================ 1단계: 회의 내용 입력
def _content(s: AppState, n: int) -> None:
    markup('<h2 class="cp-section">회의 내용 <span class="req">*</span></h2>')
    with st.container(key="wz-formats", gap="small"):
        markup('<div class="wz-formats-head"><b>ⓘ 지원하는 입력 형식</b><span>하나를 고르고 그 형식에 맞춰 적어 주세요.</span></div>')
        with st.container(horizontal=True, gap="small"):
            for kind in ("plain", "bracket", "colon"):
                button(ai.FORMAT_LABEL[kind], key=f"cp_ex_{kind}",
                       variant="accentline" if s.cp_example == kind else "secondary",  # 고른 형식은 주황 테두리
                       on_click=actions.compose_example, args=(kind,))
        markup(f'<p class="wz-format-rule">{esc(ai.FORMAT_RULE[s.cp_example])} '
               "이 형식에서 벗어나면 회의록을 만들 수 없어요.</p>")
    with st.container(key="cp-editor", gap=None):
        st.text_area(
            "회의 내용",
            key=f"cp_text_{n}_{s.cp_text_rev}",
            value=s.nn_text,
            height=320,
            label_visibility="collapsed",
            on_change=actions.compose_check,
            # 예시는 값이 아니라 회색 안내글(placeholder)로만 보여 줌 → 수정 불가, 입력칸을 누르면 사라짐
            placeholder=ai.EXAMPLES[s.cp_example],
        )
    with st.container(key="cp-attach-row", horizontal=True, vertical_alignment="center", gap="small"):
        with st.container(key="cp-attach", width="content"):
            st.file_uploader("파일 첨부", accept_multiple_files=True, key=f"cp_files_{n}",
                             label_visibility="collapsed")
        markup(f'<span class="cp-types">{ALLOWED_LABEL}</span>')
        markup(f'<span class="cp-either">{len(_current_text(s, n)):,}자</span>')
    if s.nn_files and not st.session_state.get(f"cp_files_{n}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            markup('<span class="cp-hint">앞에서 첨부한 파일: ' + esc(", ".join(nm for nm, _ in s.nn_files)) + "</span>")
            button("파일 빼기", key="cp_clear_files", variant="mini", on_click=actions.compose_clear_files)


def _step1(s: AppState, n: int) -> None:
    # 현재 단계 제목과 같은 줄 오른쪽: 초기화 · 다음 단계 (모든 단계 화면에서 같은 자리)
    with step_header("회의 내용 입력", "기본 정보와 회의 내용을 적은 뒤 「다음 단계」를 눌러 주세요"):
        button("초기화", key="cp_reset", variant="secondary", icon=":material/restart_alt:", on_click=actions.compose_reset)
        button("다음 단계 →", key="cp_next1", variant="primary", on_click=actions.compose_next, args=(n,))
    with st.container(key="wz-card", gap="medium"):
        _basic_info(s, n)
        markup('<hr class="cp-divider">')
        _content(s, n)
        if s.nn_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')


# ================================================================ 2단계: 분석 및 검토 (회의 상세와 같은 화면)
def _step2(s: AppState) -> None:
    m = s.preview_meeting
    if m is None:  # 새로고침 등으로 미리보기가 없으면 다시 만들기
        actions.compose_goto(2)
        m = s.preview_meeting
    s.meeting_id = m.id  # 근거 강조 · 담당자 지정 등이 이 미리보기 회의를 보도록
    parsed = ai.parse(actions.compose_text(s))
    result = ai.analyze(parsed, s.nn_speaker_map, s.nn_title)

    data = {
        "title": s.nn_title, "date": str(s.nn_date), "host": sel.name_of(s.nn_host),
        "speakers": {lb: sel.name_of(p) for lb, p in s.nn_speaker_map.items()},
        "summary": result.summary,
        "agendas": [{"title": t, "lines": list(b)} for t, b in result.agendas],
        "todos": [{"text": t, "owners": [x.strip() for x in w.split(",")] if w != "담당 미정" else [], "due": d}
                  for t, w, d in result.todos],
    }
    # 머리말: 회의 상세와 같은 자리 · 오른쪽 위 버튼(이전화면으로 · JSON 저장 · 확정하기)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
               f'<h1 class="md-title">{esc(m.title)}</h1>{tag("미리보기", "host")}</div>')
        with st.container(horizontal=True, width="content", gap="small"):
            button("← 이전화면으로", key="cp_prev2", variant="secondary", on_click=actions.compose_goto, args=(1,))
            with st.container(key="b-secondary-cp_json", width="content"):
                st.download_button("결과 JSON 저장", json.dumps(data, ensure_ascii=False, indent=2),
                                   file_name="meeting_result.json", mime="application/json", key="cp_json")
            # 주관자는 확정 · 주관자가 아니면 주관자에게 검토 요청 (둘 다 한 번 더 묻는 창 → 로딩)
            button("확정하기" if s.nn_host == me_id() else "검토 요청하기", key="cp_finish", variant="primary",
                   icon=":material/task_alt:" if s.nn_host == me_id() else ":material/rate_review:",
                   on_click=actions.open_compose_confirm)
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        note = ("프로토타입 · AI 분석 예시 — 「확정하기」를 눌러야 저장돼요" if s.nn_host == me_id()
                else f"프로토타입 · AI 분석 예시 — 「검토 요청하기」를 누르면 주관자 {sel.name_of(s.nn_host)}님에게 알림이 가요")
        st.html(f'<span class="read-note">{esc(note)}</span>', width="content")

    # 본문: 회의 상세와 같은 원문 · 인사이트 탭
    with st.container(key="md-split"):
        left, right = st.columns([1, 1.15], gap=None)
        with left, st.container(key="md-left", gap="small"):
            render_raw_panel(s, m)
        with right, st.container(key="md-right", gap=None):
            render_insights(s, m, mode="preview")  # 담당자 지정은 확정 뒤 담당자 추천에서

    if s.cp_finishing:  # 확인 창에서 확정 · 검토 요청: 화면을 다 그린 뒤 로딩 화면을 덮고 회의록을 만듦
        if s.nn_host == me_id():
            loading_overlay("회의록을 확정하고 있어요", "분석 결과로 회의록을 만들고 담당자 추천을 준비하는 중이에요")
        else:
            loading_overlay("검토를 요청하고 있어요", f"회의록을 만들어 주관자 {sel.name_of(s.nn_host)}님에게 보내는 중이에요")
        time.sleep(ai.ANALYZE_DELAY_SECONDS)  # 실제 서비스에서는 AI 서버 호출
        actions.compose_finish()
        st.rerun()


# ================================================================ 진입점
def render() -> None:
    s = get_state()
    n = s.compose_nonce
    if s.nn_host and s.nn_host not in s.nn_people:  # 주관자는 참석자에 늘 들어 있음 (예전 세션 포함)
        s.nn_people = [s.nn_host, *s.nn_people]
    with page("compose"):
        _stepper(s.cp_step)
        if s.cp_step == 2:
            _step2(s)
        else:
            _step1(s, n)
