"""새 회의록 작성 (로그인 후 첫 화면) — 3단계.

  1) 회의 내용 입력: 기본 정보(제목 · 날짜 · 주관자 · 참석자 · 회의 종류·폴더) + 회의 내용(직접 쓰기 / TXT · MD · DOCX · HWPX 첨부)
     · 지원하는 입력 형식: 발화자 없는 텍스트(기본) · [발화자] 형식 · 발화자: 형식 중 하나를 고름
       고른 형식의 규칙은 회색 안내글로, 예시는 입력칸의 회색 글씨(placeholder)로 보여 줌
     · 고른 형식에서 벗어나면 「발화자 확인 →」에서 다음 단계로 못 가요
  2) 발화자 확인: AI 가 붙인 임시 발화자(참석자1 · A …)를 실제 사람과 연결 (칸을 누르면 팀 → 팀원 모달 · 모르면 미지정)
  3) 분석 및 결과: 회의 상세와 같은 화면(원문 · 요약 · 안건 · 액션 아이템 …)으로 미리 보고 「회의록 생성 완료」
     → 완성된 회의록이 폴더에 생기고 회의 상세 화면으로 이동
분석 규칙과 회의록 만들기는 state/compose_ai.py 에 있어요 (실제 서비스에서는 AI 서버가 대신함).
"""

from __future__ import annotations

import json

import streamlit as st

from components.member_pills import member_pills
from state.store import me_id
from data import repository as repo
from state import actions
from state import compose_ai as ai
from state import selectors as sel
from state.store import AppState, get_state
from ui.buttons import button, button_group
from ui.html import markup
from ui.layout import page
from ui.tags import tag
from utils.dates import date_key
from utils.files import ALLOWED_LABEL, read_text
from utils.text import esc
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


_STEPS = ("회의 내용 입력", "발화자 확인", "분석 및 결과")


# ================================================================ 공통
def _stepper(step: int) -> None:
    items = []
    for i, name in enumerate(_STEPS, 1):
        state = "done" if i < step else ("on" if i == step else "todo")
        mark = "✓" if state == "done" else str(i)
        items.append(f'<span class="wz-step {state}"><span class="dot">{mark}</span>{esc(name)}</span>')
    markup('<div class="wz-steps">' + '<span class="wz-arrow">→</span>'.join(items) + "</div>")


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
    with st.container(key="wz-card", gap="medium"):
        _basic_info(s, n)
        markup('<hr class="cp-divider">')
        _content(s, n)
        if s.nn_error:
            markup(f'<span role="alert" class="cp-error">{esc(s.nn_error)}</span>')
        with st.container(horizontal=True, vertical_alignment="center"):
            button("초기화", key="cp_reset", variant="secondary", on_click=actions.compose_reset)
            st.space("stretch")
            button("발화자 확인 →", key="cp_next1", variant="primary", on_click=actions.compose_next, args=(n,))


# ================================================================ 2단계: 발화자 확인
def _step2(s: AppState) -> None:
    parsed = ai.parse(actions.compose_text(s))
    labels = parsed.labels
    with st.container(key="wz-card-full", gap="medium"):
        _card_head("발화자 확인 및 매칭", f"{len(labels)}명 감지")
        _info("<span>AI가 읽은 발화자 표기를 실제 참석자와 연결하세요. <b>매칭은 선택 사항</b>이며 "
              "이름을 모르면 「미지정」으로 두고 진행할 수 있어요.</span>")
        if not labels:
            _info("<b>발화자 표기가 없는 회의 내용이에요</b><span>연결할 발화자가 없어서 바로 분석으로 넘어가면 돼요. "
                  "원문에서는 모든 발언이 「미지정」으로 보여요.</span>", "warn")
        with st.container(key="wz-matches", gap="small"):
            for i, label in enumerate(labels):
                with st.container(key=f"wz-match-{i}", horizontal=True, vertical_alignment="center", gap="medium"):
                    markup(f'<div class="wz-label">{esc(label)}</div>')
                    markup('<span class="wz-to">→</span>')
                    pid = s.nn_speaker_map.get(label, "")
                    value = f"{sel.name_of(pid)} · {sel.person(pid).team}" if pid else ""
                    # 누르면 「발화자 연결」 모달 (팀 → 팀원) · 비어 있으면 미지정
                    button(value or "미지정 (나중에 확인)", key=f"wz_map_{i}", variant="field" if value else "fieldempty",
                           icon=":material/person:", on_click=actions.open_speaker_picker, args=(label,),
                           width="stretch")
        if labels:
            done = sum(1 for lb in labels if s.nn_speaker_map.get(lb))
            _info(f"<b>{done}/{len(labels)}명 매칭 완료</b><span>미지정 발화자가 있어도 분석을 진행할 수 있어요. "
                  "한 사람을 여러 발화자 표기에 연결할 수도 있어요.</span>", "warn" if done < len(labels) else "ok")
        with st.container(key="wz-foot", horizontal=True, vertical_alignment="center"):  # 카드 맨 아래
            button("← 이전 단계", key="cp_prev2", variant="secondary", on_click=actions.compose_goto, args=(1,))
            st.space("stretch")
            button("분석 내용 확인 →", key="cp_next2", variant="primary", on_click=actions.compose_goto, args=(3,))


# ================================================================ 3단계: 분석 및 결과 (회의 상세와 같은 화면)
def _step3(s: AppState) -> None:
    m = s.preview_meeting
    if m is None:  # 새로고침 등으로 미리보기가 없으면 다시 만들기
        actions.compose_goto(3)
        m = s.preview_meeting
    s.meeting_id = m.id  # 근거 강조 · 담당자 지정 등이 이 미리보기 회의를 보도록
    parsed = ai.parse(actions.compose_text(s))
    result = ai.analyze(parsed, s.nn_speaker_map, s.nn_title)

    data = {
        "title": s.nn_title, "date": str(s.nn_date), "host": sel.name_of(s.nn_host),
        "speakers": {lb: (sel.name_of(p) if p else "미지정") for lb, p in s.nn_speaker_map.items()},
        "summary": result.summary,
        "agendas": [{"title": t, "lines": list(b)} for t, b in result.agendas],
        "todos": [{"text": t, "owners": [x.strip() for x in w.split(",")] if w != "담당 미정" else [], "due": d}
                  for t, w, d in result.todos],
    }
    # 머리말: 회의 상세와 같은 자리 · 오른쪽 위 버튼(편집 · 공유 대신 수정 · 저장 · 생성 완료)
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markup(f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
               f'<h1 class="md-title">{esc(m.title)}</h1>{tag("미리보기", "host")}</div>')
        with st.container(horizontal=True, width="content", gap="small"):
            button("← 발화자 매칭 수정", key="cp_prev3", variant="secondary", on_click=actions.compose_goto, args=(2,))
            with st.container(key="b-secondary-cp_json", width="content"):
                st.download_button("결과 JSON 저장", json.dumps(data, ensure_ascii=False, indent=2),
                                   file_name="meeting_result.json", mime="application/json", key="cp_json")
            button("회의록 생성 완료 ✓", key="cp_finish", variant="primary", on_click=actions.compose_finish)
    with st.container(horizontal=True, vertical_alignment="center"):
        member_pills(f"{m.when} · 참석 {len(m.people)}명", m.host, list(m.people))
        mapped = " · ".join(f"{lb} → {sel.name_of(p) if p else '미지정'}" for lb, p in s.nn_speaker_map.items())
        note = "프로토타입 · AI 분석 예시 — 「회의록 생성 완료」를 눌러야 저장돼요"
        st.html(f'<span class="read-note">{esc(note)}' + (f"<br>발화자 매칭: {esc(mapped)}" if mapped else "")
                + "</span>", width="content")

    # 본문: 회의 상세와 같은 원문 · 인사이트 탭
    with st.container(key="md-split"):
        left, right = st.columns([1, 1.15], gap=None)
        with left, st.container(key="md-left", gap="small"):
            render_raw_panel(s, m)
        with right, st.container(key="md-right", gap=None):
            render_insights(s, m)


# ================================================================ 진입점
def render() -> None:
    s = get_state()
    n = s.compose_nonce
    with page("compose"):
        _stepper(s.cp_step)
        if s.cp_step == 2:
            _step2(s)
        elif s.cp_step == 3:
            _step3(s)
        else:
            _step1(s, n)
