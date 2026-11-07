"""새 회의록 작성 (로그인 후 첫 화면) — 2단계.

  1) 회의 내용 입력: 기본 정보(제목 · 날짜 · 주관자 · 참석자 · 회의 종류·폴더) + 회의 내용(직접 쓰기 / TXT · MD · DOCX · HWP · HWPX 첨부)
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


import streamlit as st

from components.member_pills import member_pills
from config import SUMMARY_POLL_SECONDS
from state.store import me_id
from data import repository as repo
from state import actions
from state import compose_ai as ai
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.layout import page, step_header
from ui.loading import loading_overlay
from ui.tags import tag
from utils.dates import date_key
from utils.files import ALLOWED_LABEL, read_text
from utils.text import esc
from views.finalize import flow_stepper
from views.meeting.insight_tabs import render_insights
from views.meeting.raw_panel import render_raw_panel

# 회의 내용으로 읽을 수 있는 형식: TXT · MD · DOCX · HWP · HWPX (utils/files.py)
# 올리면 내용을 회의 내용 칸에 채움 · 그 밖의 형식은 받지 않고 경고 (state/actions.compose_attach)
_NEW = "new"


def _person_label(pid: str) -> str:
    p = sel.person(pid)
    return f"{p.name} · {p.team}" + (" (나)" if pid == me_id() else "")


def _people_text(pids: list[str]) -> str:
    if not pids:
        return ""
    names = [sel.person(p).name for p in pids]
    return ", ".join(names[:3]) + (f" 외 {len(names) - 3}명" if len(names) > 3 else "") + f"  ({len(names)}명)"


def _project_ids(s: AppState) -> list[str]:
    """내 프로젝트 폴더 전체: 최근 회의가 있었던 순 (새로 만든 폴더 먼저)."""
    def recent(fid: str) -> tuple:
        notes = sel.folder_note_ids(s, fid)
        return (sel.folders(s)[fid].is_new, date_key(repo.meeting(notes[0]).date) if notes else 0)

    return sorted(sel.all_my_folder_ids(s), key=recent, reverse=True)


def _project_field(s: AppState, n: int) -> None:
    """프로젝트 드롭다운 → 이 회의의 종류.

    프로젝트 하나에 월간 · 주간 · 일반 회의가 함께 있어요. 프로젝트는 이름으로 검색해 고르거나,
    목록에 없는 이름을 적으면 새 프로젝트로 만들어요.
    """
    types = repo.meeting_types()
    folders = sel.folders(s)
    fids = _project_ids(s)
    by_name = {folders[k].name: k for k in fids}  # shortcut: 이름이 같은 프로젝트가 둘 이상이면 하나만 보임
    pending = s.nn_name.strip() if s.nn_topic not in fids else ""
    options = list(by_name) + ([pending] if pending and pending not in by_name else [])
    current = folders[s.nn_topic].name if s.nn_topic in fids else pending
    _label("프로젝트 *")
    value = st.selectbox(
        "프로젝트 (필수)", options, index=options.index(current) if current in options else None,
        placeholder="프로젝트 검색 또는 새 이름 입력", accept_new_options=True, label_visibility="collapsed",
        key=f"cp_project_{n}",
    )
    value = (value or "").strip()
    if value:  # 고른 값을 바로 작성 상태에 반영 (기존 이름을 그대로 적으면 새로 만들지 않고 그 프로젝트로)
        fid = by_name.get(value, "")
        s.nn_topic, s.nn_name = (fid, "") if fid else (_NEW, value)
    _label("회의 종류 *")
    button_group([(k, types[k].name) for k in ("monthly", "weekly", "normal")], s.nn_type, actions.set_compose_type,
                 key="cp_type", style="seg", gap=None)


_POSITION = {"senior": "시니어", "mid": "미드"}


def _reviewer_field(s: AppState, n: int) -> None:
    """신입이 주관한 회의록은 윗사람이 검토 · 확정해요 (백엔드 규칙). 후보: 신입이 아닌 구성원 (같은 팀이 먼저)."""
    cands = sel.reviewer_candidates(s)
    _label("검토자 (윗사람) *")
    key = f"cp_reviewer_{n}"

    def label(pid: str) -> str:
        p = sel.person(pid)
        return " · ".join(x for x in (p.name, p.team, _POSITION.get(sel.position_of(pid), "")) if x)

    st.selectbox("검토자 (필수)", cands, index=cands.index(s.nn_reviewer) if s.nn_reviewer in cands else None,
                 key=key, format_func=label, placeholder="검토할 윗사람을 골라 주세요", label_visibility="collapsed",
                 on_change=actions.set_compose_reviewer, args=(key,))
    markup('<span class="cp-hint">신입이 주관한 회의록은 윗사람이 검토 · 확정해요. '
           '「검토 요청하기」를 누르면 고른 사람에게 알림이 가요.</span>')


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
    file = st.session_state.get(f"cp_files_{n}_{s.cp_file_rev}")  # 파일은 한 개만 고를 수 있어요
    files = [file] if file else []
    bodies = [read_text(f.name, f.getvalue()) for f in files] if files else [b for _, b in s.nn_files]
    bodies = [b for b in bodies if b]  # 지원하지 않는 형식 · 읽을 수 없는 파일은 빼고
    return "\n".join(x.strip() for x in [typed, *bodies] if x and x.strip())


def _basic_info(s: AppState, n: int) -> None:
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup('<h2 class="cp-section">기본 정보</h2>')
        markup('<span class="cp-required"><span class="req">*</span> 필수 입력</span>')

    types = repo.meeting_types()
    folders = sel.folders(s)

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
        if sel.needs_reviewer(s):  # 내가 신입이고 내가 주관자 → 검토할 윗사람 (꼭)
            _reviewer_field(s, n)
        _project_field(s, n)
    t = types[s.nn_type].name
    topic = s.nn_topic if s.nn_topic in sel.all_my_folder_ids(s) else _NEW
    if topic == _NEW:
        hint = (f"「{s.nn_name}」 프로젝트가 새로 만들어지고(내가 주관자) 이 회의는 {t}로 쌓여요." if s.nn_name.strip()
                else "프로젝트를 고르거나 새 이름을 적어 주세요.")
    else:
        hint = f"「{folders[topic].name}」 프로젝트의 {t}로 쌓여요 · 참석자만 볼 수 있고, 빠진 멤버에게는 회의 상세에서 공유할 수 있어요."
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
            on_change=actions.compose_check,  # 칸을 벗어나면 형식을 자동으로 고름
            args=(n,),
            # 예시는 값이 아니라 회색 안내글(placeholder)로만 보여 줌 → 수정 불가, 입력칸을 누르면 사라짐
            placeholder=ai.EXAMPLES[s.cp_example],
        )
    with st.container(key="cp-attach-row", horizontal=True, vertical_alignment="center", gap="small"):
        with st.container(key="cp-attach", width="content"):
            # 올리면 compose_attach가 형식을 확인: TXT · MD · DOCX · HWP · HWPX는 내용을 회의 내용 칸에 채우고,
            # 그 밖의 형식은 받지 않고(첨부 칸 비움) 경고. type= 을 쓰면 Streamlit이 영어 오류만 띄우고 경고를 못 띄워서 안 씀
            st.file_uploader("파일 첨부", accept_multiple_files=False, key=f"cp_files_{n}_{s.cp_file_rev}",
                             label_visibility="collapsed", on_change=actions.compose_attach, args=(n,))
        markup(f'<span class="cp-types">{ALLOWED_LABEL}</span>')
        markup(f'<span class="cp-either">{len(_current_text(s, n)):,}자</span>')
    if s.cp_file_warn:  # 지원하지 않는 파일을 올림 (받지 않음)
        _info(esc(s.cp_file_warn), "warn")
    if s.cp_format_warn:  # 자동 판별 결과: 경고(주황)가 있으면 경고만, 없으면 고른 형식 안내(초록)
        _info(esc(s.cp_format_warn), "warn")
    elif s.cp_format_note:
        _info(esc(s.cp_format_note), "ok")
    if s.nn_files and not st.session_state.get(f"cp_files_{n}_{s.cp_file_rev}"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            markup('<span class="cp-hint">앞에서 첨부한 파일: ' + esc(", ".join(nm for nm, _ in s.nn_files)) + "</span>")
            button("파일 빼기", key="cp_clear_files", variant="mini", on_click=actions.compose_clear_files)


def _step1(s: AppState, n: int) -> None:
    with step_header("회의 내용 입력", "기본 정보와 회의 내용을 적은 뒤 「다음 단계」를 눌러 주세요"):
        pass
    with st.container(key="wz-card", gap="medium"):
        _basic_info(s, n)
        markup('<hr class="cp-divider">')
        _content(s, n)
        if s.nn_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')
        # 회의 내용을 적은 뒤 바로 누를 수 있게 카드 오른쪽 아래: 초기화 · 다음 단계
        with st.container(key="cp-step1-btns", horizontal=True, horizontal_alignment="right", gap="small"):
            button("초기화", key="cp_reset", variant="secondary", icon=":material/restart_alt:",
                   on_click=actions.compose_reset)
            button("다음 단계 →", key="cp_next1", variant="primary", on_click=actions.compose_next, args=(n,))


# ================================================================ 2단계: 분석 및 검토 (회의 상세와 같은 화면)
@st.fragment(run_every=SUMMARY_POLL_SECONDS)
def _poll_analysis() -> None:
    """이 칸만 주기적으로 다시 그려 분석이 끝났는지 확인 (다른 화면에 있으면 돌지 않음)."""
    s = get_state()
    actions.compose_poll()
    if not s.cp_job_running:
        st.rerun(scope="app")


def _analyzing(s: AppState) -> None:
    """분석 진행 중 화면: 입력을 다시 고치거나 새로 등록할 수 없고, 다른 화면으로는 자유롭게 이동해요."""
    with st.container(key="wz-card-full", gap="medium"):
        _card_head("회의를 분석하고 있어요", "진행 중")
        _info(f"<b><i class=\"wz-spin\"></i>「{esc(s.nn_title)}」의 요약과 액션 아이템을 정리하고 있어요</b>"
              "<span>몇 분 걸릴 수 있어요. 다른 화면으로 이동해도 분석은 계속되고, 「새 회의록」으로 돌아오면 "
              "이어서 확인할 수 있어요. 끝나기 전에는 새 회의록을 등록할 수 없어요.</span>")
        _poll_analysis()


def _step2(s: AppState) -> None:
    if s.cp_job_running:  # 뒤에서 분석 중: 다른 화면에 다녀와도 이 화면으로 돌아옴
        _analyzing(s)
        return
    if s.preview_meeting is None:
        actions.compose_goto(1)
        st.rerun()
    m = s.preview_meeting
    s.meeting_id = m.id  # 근거 강조 · 담당자 지정 등이 이 미리보기 회의를 보도록
    direct = s.nn_host == me_id() and not sel.needs_reviewer(s)  # 바로 확정 (아니면 검토 요청)
    reviewer = s.nn_reviewer if sel.needs_reviewer(s) else s.nn_host
    who = f"{'검토자' if sel.needs_reviewer(s) else '주관자'} {sel.name_of(reviewer)}님"
    # 머리말: 회의 상세와 같은 자리 · 오른쪽 위 버튼(이전화면으로 · 확정하기)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
               f'<h1 class="md-title">{esc(m.title)}</h1>{tag("미리보기", "host")}</div>')
        with st.container(horizontal=True, width="content", gap="small"):
            button("← 이전화면으로", key="cp_prev2", variant="secondary", on_click=actions.compose_goto, args=(1,))
            # 주관자는 확정 · 주관자가 아니거나 신입이면 검토 요청 (둘 다 한 번 더 묻는 창 → 로딩)
            button("확정하기" if direct else "검토 요청하기", key="cp_finish", variant="primary",
                   icon=":material/task_alt:" if direct else ":material/rate_review:",
                   on_click=actions.open_compose_confirm)
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        note = ("AI 분석 결과 — 「확정하기」를 눌러 회의록을 확정해 주세요" if direct
                else f"AI 분석 결과 — 「검토 요청하기」를 누르면 {who}에게 알림이 가요")
        st.html(f'<span class="read-note">{esc(note)}</span>', width="content")

    # 본문: 회의 상세와 같은 원문 · 인사이트 탭
    with st.container(key="md-split"):
        left, right = st.columns([1, 1.15], gap=None)
        with left, st.container(key="md-left", gap="small"):
            render_raw_panel(s, m)
        with right, st.container(key="md-right", gap=None):
            render_insights(s, m, mode="preview")  # 담당자 지정은 확정 뒤 담당자 추천에서

    if s.cp_finishing:  # 확인 창에서 확정 · 검토 요청: 화면을 다 그린 뒤 로딩 화면을 덮고 회의록을 만듦
        if direct:
            loading_overlay("회의록을 확정하고 있어요", "분석 결과로 회의록을 만들고 담당자 추천을 준비하는 중이에요")
        else:
            loading_overlay("검토를 요청하고 있어요", f"회의록을 만들어 {who}에게 보내는 중이에요")
        if actions.compose_review():  # 수정 반영 · 근거 연결 · 검증 (실패하면 안내 후 이 화면에 머무름)
            actions.compose_finish()
        st.rerun()


# ================================================================ 진입점
def render() -> None:
    actions.compose_resume()  # 서버에 분석 작업이 있으면 입력값 · 진행 상태 복원
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
