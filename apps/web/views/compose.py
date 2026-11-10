"""새 회의록 작성 (로그인 후 첫 화면) — 2단계.

  1) 회의 내용 입력: 기본 정보(제목 · 날짜 · 주관자 · 참석자 · 회의 종류·폴더) + 회의 내용(직접 쓰기 / TXT · MD · DOCX · HWP · HWPX 첨부)
     · 지원하는 입력 형식: 발화자 없는 텍스트(기본) · [발화자] 형식 · 발화자: 형식 중 하나를 고름
       고른 형식의 규칙은 회색 안내글로, 예시는 입력칸의 회색 글씨(placeholder)로 보여 줌
     · 주관자는 참석자에 늘 들어가요 (참석자 고르기에서 체크된 채로 · 뺄 수 없음)
     · 고른 형식에서 벗어나거나 발화자가 참석자 안에 없으면 「다음 단계」에서 경고 창이 뜨고 넘어가지 못해요
  2) 분석 및 검토: 회의 상세와 같은 화면(원문 · 요약 · 안건 · 액션 아이템 …)으로 미리 보고 (담당자 지정은 확정 뒤)
     · 내가 주관자: 「확정하기」 → 확인 창 → 로딩 → 회의록 확정 화면(담당자 추천 → 최종 회의록 → 결과 전달 → 외부 도구 전송)
     · 주관자가 다른 사람: 분석이 끝나면 바로 주관자에게 넘어가고(actions.compose_handoff) 나는 새 회의록 화면으로.
       주관자는 홈 「회의록 생성 검토 대기」→「검토하기」로 이 2단계부터 이어서 해요 (actions.open_handoff).
  주관자는 시니어 · 미드만 (신입이 쓰면 주관자 칸이 비어 있고 윗사람을 골라요).
분석 규칙과 회의록 만들기는 state/compose_ai.py 에 있어요 (실제 서비스에서는 AI 서버가 대신함).
"""

from __future__ import annotations


import streamlit as st

from components.member_pills import member_pills
from config import MAX_TRANSCRIPT_CHARS, SUMMARY_POLL_SECONDS
from state.store import me_id
from data import repository as repo
from state import actions
from state import compose_ai as ai
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.layout import page, step_header
from ui.loading import loading_cover, loading_overlay
from ui.tags import tag
from utils.dates import date_key, today
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
            _label("회의 날짜")  # 선택: 고르지 않으면 「다음 단계」 때 오늘 날짜로 (actions.compose_next)
            st.date_input("회의 날짜 (선택)", value=s.nn_date or today(), key=f"cp_date_{n}", format="YYYY/MM/DD",
                          label_visibility="collapsed")
            if st.session_state.get(f"cp_date_{n}") is None:
                markup(f'<span class="cp-hint">날짜를 고르지 않으면 오늘({today():%Y/%m/%d})로 저장돼요.</span>')
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            _field("주관자 *", "cp_host_field", _person_label(s.nn_host) if s.nn_host else "", "주관자 선택",
                   ":material/person:", "cp_host")
        with c2:
            _field("참석자", "cp_people_field", _people_text(s.nn_people), "참석자 선택",
                   ":material/group:", "cp_people")
        if s.nn_host and not sel.can_host(s.nn_host):
            markup('<span class="cp-hint">주관자는 시니어 · 미드만 될 수 있어요. 주관자를 다시 골라 주세요.</span>')
        elif not s.nn_host:
            markup('<span class="cp-hint">주관자를 시니어 · 미드 중에서 골라 주세요. 다른 사람을 고르면 분석이 끝나는 대로 '
                   '그 주관자에게 넘어가고, 이후 검토 · 확정은 주관자가 해요.</span>')
        elif s.nn_host != me_id():
            markup(f'<span class="cp-hint">분석이 끝나면 주관자 {esc(sel.name_of(s.nn_host))}님에게 바로 넘어가요. '
                   '이후 검토 · 확정 · 결과 전달은 주관자가 해요.</span>')
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
        length = len(_current_text(s, n))
        markup(f'<span class="cp-either">{length:,} / {MAX_TRANSCRIPT_CHARS:,}자</span>')
    if length > MAX_TRANSCRIPT_CHARS:  # 「다음 단계」에서도 막음 (actions.compose_next)
        _info(f"회의 내용이 최대 {MAX_TRANSCRIPT_CHARS:,}자를 넘었어요. 회의를 나눠서 올려 주세요.", "warn")
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
    """분석 진행 중 화면: 방금 쓴 「회의 내용 입력」 화면을 뒤에 흐리게 두고 확정 때와 같은 스피너를 덮어요.
    입력은 고칠 수 없고(덮개가 막음), 사이드바로 다른 화면에는 자유롭게 이동해요."""
    _step1(s, s.compose_nonce)  # 뒤에 흐리게 비치는 화면 (덮개 아래라 누를 수 없음)
    handoff = (f"분석이 끝나면 주관자 {sel.name_of(s.nn_host)}님에게 바로 넘어가요." if s.nn_host != me_id() else "")
    loading_cover("회의를 분석하고 있어요",
                  f"「{s.nn_title}」의 요약과 액션 아이템을 정리하는 중이에요.",
                  "몇 분 걸릴 수 있어요. 다른 화면으로 이동해도 분석은 계속돼요.",
                  handoff)
    _poll_analysis()


def _handoff_failed(s: AppState) -> None:
    """(작성자) 분석은 끝났는데 주관자에게 넘기지 못함 → 이유와 「다시 넘기기」."""
    with st.container(key="wz-card-full", gap="medium"):
        _card_head("주관자에게 넘기지 못했어요", "다시 시도")
        _info(f"<b>「{esc(s.nn_title)}」 분석은 끝났어요</b><span>주관자 {esc(sel.name_of(s.nn_host))}님에게 넘기는 중에 "
              f"문제가 생겼어요: {esc(s.cp_handoff_error or '알 수 없는 오류')}</span>", "warn")
        with st.container(horizontal=True, horizontal_alignment="right"):
            button("다시 넘기기", key="cp_handoff_retry", variant="primary", icon=":material/send:",
                   on_click=actions.compose_handoff)


def _step2(s: AppState) -> None:
    if s.cp_job_running:  # 뒤에서 분석 중: 다른 화면에 다녀와도 이 화면으로 돌아옴
        _analyzing(s)
        return
    if s.preview_meeting is None:
        actions.compose_goto(1)
        st.rerun()
    if s.nn_host != me_id() and not s.cp_handoff:  # 작성자 ≠ 주관자: 2단계 수정은 주관자가 (넘기기에 실패했을 때만 여기)
        _handoff_failed(s)
        return
    m = s.preview_meeting
    s.meeting_id = m.id  # 근거 강조 · 담당자 지정 등이 이 미리보기 회의를 보도록
    # 머리말: 회의 상세와 같은 자리 · 오른쪽 위 버튼(이전화면으로 · 확정하기)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
               f'<h1 class="md-title">{esc(m.title)}</h1>{tag("미리보기", "host")}</div>')
        with st.container(key="cp-step2-btns", horizontal=True, width="content", gap="small"):
            if not s.cp_handoff:  # 넘겨받은 회의록은 2단계부터 (입력값을 고치면 새로 분석하게 되므로 1단계로 못 감)
                # 삭제 = 1단계 초기화와 같음: 분석 작업 캐시 · 분석 직후 저장한 draft(Postgres)를 지우고 빈 1단계로
                button("삭제", key="cp_delete2", variant="dangerline", icon=":material/delete:", on_click=actions.compose_reset)
                button("← 이전화면으로", key="cp_prev2", variant="secondary", on_click=actions.compose_goto, args=(1,))
            button("확정하기", key="cp_finish", variant="primary", icon=":material/task_alt:",
                   on_click=actions.open_compose_confirm)
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        note = (f"{sel.name_of(s.cp_handoff_author)}님이 넘긴 AI 분석 결과 — 검토 · 수정한 뒤 「확정하기」를 눌러 주세요"
                if s.cp_handoff else "AI 분석 결과 — 「확정하기」를 눌러 회의록을 확정해 주세요")
        st.html(f'<span class="read-note">{esc(note)}</span>', width="content")

    # 본문: 회의 상세와 같은 원문 · 인사이트 탭
    with st.container(key="md-split"):
        left, right = st.columns([1, 1.15], gap=None)
        with left, st.container(key="md-left", gap="small"):
            render_raw_panel(s, m)
        with right, st.container(key="md-right", gap=None):
            render_insights(s, m, mode="preview")  # 담당자 지정은 확정 뒤 담당자 추천에서

    if s.cp_finishing:  # 확인 창에서 확정 · 검토 요청: 화면을 다 그린 뒤 로딩 화면을 덮고 회의록을 만듦
        loading_overlay("회의록을 확정하고 있어요", "분석 결과로 회의록을 만들고 담당자 추천을 준비하는 중이에요")
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
