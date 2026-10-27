"""새 회의 등록: 회의록 입력(파일·붙여넣기) + 기본 정보."""
from __future__ import annotations

from datetime import time as dtime

import streamlit as st

from core import auth, router
from core.state import add_meeting, flash, members
from data.constants import ALLOWED_EXT, MAX_FILE_MB
from data.seed import SAMPLE_TEXT
from services.analysis import new_meeting
from ui.components import html
from ui.layout import page_head, panel, panel_head
from utils.text import esc
from utils.dates import TODAY

GUIDE = [
    ("AI 분석", "주제별로 나눈 뒤 요약과 액션 아이템을 각각 추출하고, 원문 근거가 맞는지 검증해요."),
    ("담당자 추천", "담당자가 없는 업무만 구성원 정보로 후보 3명을 추천해요. 자동으로 배정하지 않아요."),
    ("검토 · 확정", "주관자가 원문 근거를 보며 수정·승인하고 결과를 확정해요."),
    ("전달", "대상을 고르고 미리보기를 확인한 뒤 승인하면 전송돼요."),
]


def _use_sample() -> None:
    st.session_state.reg_text = SAMPLE_TEXT
    st.session_state.reg_method = "텍스트 붙여넣기"
    if not st.session_state.get("reg_title"):
        st.session_state.reg_title = "회원 등급 개편 2차 논의"
    if not st.session_state.get("reg_purpose"):
        st.session_state.reg_purpose = "3단계 등급 개편의 세부 기준과 고객 안내 방식을 정한다."
    st.session_state.reg_att = list(dict.fromkeys(st.session_state.get("reg_att", []) + ["m5", "m6", "m2", "m3"]))


@st.dialog("등록 후 진행 방식")
def _guide_dialog() -> None:
    for i, (title, desc) in enumerate(GUIDE, 1):
        st.markdown(f"**{i}. {title}**  \n{desc}")
    st.caption("팁: 업무는 ‘누가, 무엇을, 언제까지’가 드러나게 적고, 참석자 이름은 조직도와 같은 표기로 적어 주세요.")


def _validate(method: str, upload, text: str, title: str, start, end, attendees: list) -> list[str]:
    errors = []
    if method == "파일 업로드":
        if upload is None:
            errors.append("회의록 파일을 올려 주세요.")
        else:
            ext = upload.name.rsplit(".", 1)[-1].lower()
            if ext not in ALLOWED_EXT:
                errors.append(f"지원하지 않는 형식이에요(.{ext}). .txt, .md, .docx, .hwpx 파일만 올릴 수 있어요.")
            elif upload.size == 0:
                errors.append("내용이 없는 빈 파일이에요.")
            elif upload.size > MAX_FILE_MB * 1024 * 1024:
                errors.append(f"파일이 {MAX_FILE_MB}MB보다 커요. 회의록을 나눠서 올려 주세요.")
    elif len(text.strip()) < 20:
        errors.append("회의록 내용을 20자 이상 붙여넣어 주세요.")
    if not title.strip():
        errors.append("회의 제목을 입력해 주세요.")
    if start >= end:
        errors.append("종료 시간이 시작 시간보다 늦어야 해요.")
    if not attendees:
        errors.append("참석자를 한 명 이상 선택해 주세요.")
    return errors


TIMES = [f"{h:02d}:{m:02d}" for h in range(7, 23) for m in (0, 30)]
STEP_NAMES = ["회의 등록", "AI 분석", "검토 · 확정", "전달"]


def _label(text: str, req: bool = False, opt: bool = False) -> None:
    mark = ' <span class="req">*</span>' if req else ' <span class="opt">(선택)</span>' if opt else ""
    html(f'<div class="flabel">{text}{mark}</div>')


def _form_row(text: str, req: bool = False, opt: bool = False):
    c1, c2 = st.columns([1, 4.2], gap=None)
    with c1:
        _label(text, req, opt)
    return c2.container(key=f"fcell_{text}")


def render() -> None:
    me = auth.me()
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        page_head("새 회의 등록", "회의록과 기본 정보를 등록하면 AI가 요약, 결정사항, 미해결 이슈, 액션 아이템 초안을 만들어요.", ["홈", "회의", "새 회의 등록"])
    with right:
        html('<div class="steps">' + "".join(f'<span class="{"on" if i == 0 else ""}"><b>{i + 1}</b>{n}</span>' for i, n in enumerate(STEP_NAMES)) + "</div>")

    main, side = st.columns([5, 1.9], gap="medium")
    with main:
        with panel("regsrc"):
            st.session_state.setdefault("reg_method", "파일 업로드")
            with st.container(horizontal=True, vertical_alignment="center"):
                html('<span class="tt" style="font-size:16px;font-weight:700">회의록 입력 <span class="c-err">*</span></span>')
                method = st.segmented_control("입력 방식", ["파일 업로드", "텍스트 붙여넣기"], key="reg_method", label_visibility="collapsed") or "파일 업로드"
            upload, text = None, st.session_state.get("reg_text", "")
            if method == "파일 업로드":
                upload = st.file_uploader("회의록 파일", key="reg_file", label_visibility="collapsed")
                st.caption(f"지원 형식 .txt · .md · .docx · .hwpx · 파일 1개, 최대 {MAX_FILE_MB}MB")
                if upload is not None and upload.name.rsplit(".", 1)[-1].lower() in ALLOWED_EXT and upload.size:
                    st.success(f"{upload.name} · {upload.size / 1024:.1f}KB — 지원 형식 ✓ 파일 정상 ✓ 내용 있음 ✓ 최대 길이 이내 ✓")
            else:
                text = st.text_area("회의록 내용", key="reg_text", height=240, label_visibility="collapsed",
                                    placeholder="예)\n이서연: 오늘은 회원 등급 개편을 논의하겠습니다.\n정하은: 안내 문구는 제가 10월 29일까지 정리하겠습니다.")
                st.caption(f"{len(text):,}자 · ‘이름: 발언’ 형식이면 담당자를 더 정확히 찾아요.")
            st.button("샘플 회의록으로 채우기", type="tertiary", on_click=_use_sample)

        with panel("reginfo"):
            panel_head("회의 기본 정보", '<span class="c-err">*</span> 필수 입력')
            html('<div style="border-top:2px solid var(--line-strong)"></div>')
            with _form_row("회의 제목", req=True):
                title = st.text_input("회의 제목", key="reg_title", placeholder="예) 4분기 신규 회원 온보딩 개선 회의", label_visibility="collapsed")
            with _form_row("회의 일시", req=True):
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    day = st.date_input("회의 날짜", value=TODAY, key="reg_date", label_visibility="collapsed", width=170, format="YYYY.MM.DD")
                    start = st.selectbox("시작", TIMES, index=TIMES.index("14:00"), key="reg_start", label_visibility="collapsed", width=120)
                    html('<span class="muted">~</span>', width="content")
                    end = st.selectbox("종료", TIMES, index=TIMES.index("15:00"), key="reg_end", label_visibility="collapsed", width=120)
                st.caption("‘다음 주 수요일’ 같은 상대 날짜는 이 일시를 기준으로 기한을 계산해요.")
            with _form_row("회의 주관자"):
                html(f'<div style="font-size:14px"><strong>{esc(me["name"])}</strong> <span class="muted">{esc(me["dept"])} · 등록자</span></div>')
                st.caption("결과 수정과 확정은 주관자만 할 수 있어요.")
            with _form_row("참석자", req=True):
                options = [m["id"] for m in members() if m["id"] != me["id"]]
                attendees = st.multiselect("참석자", options, key="reg_att", label_visibility="collapsed", placeholder="이름 또는 부서로 구성원 검색",
                                           format_func=lambda mid: next(f"{m['name']} · {m['dept']}" for m in members() if m["id"] == mid))
                st.caption(f"회의록에 나온 이름을 참석자·구성원 목록과 대조해요. {len(attendees)}명 선택됨")
            with _form_row("회의 목적", opt=True):
                purpose = st.text_input("회의 목적", key="reg_purpose", placeholder="예) 온보딩 이탈 구간 점검 및 4분기 개선 과제 선정", label_visibility="collapsed")
            with _form_row("참고 자료", opt=True):
                with st.container(key="ref_box"):
                    ref = st.file_uploader("참고 자료", key="reg_ref", label_visibility="collapsed")
                st.caption("참고 자료는 회의와 함께 보관돼요. PDF·이미지는 아직 분석에 반영되지 않아요.")

        with panel("regact"):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.checkbox("분석이 끝나면 알림으로 알려주세요.", value=True, key="reg_notify")
                with st.container(horizontal=True, horizontal_alignment="right"):
                    st.button("취소", on_click=router.go, args=("meetings",))
                    submit = st.button("분석 시작", type="primary")
            if submit:
                start_t, end_t = dtime.fromisoformat(start), dtime.fromisoformat(end)
                errors = _validate(method, upload, text, title, start_t, end_t, attendees)
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    source, note = text, None
                    if method == "파일 업로드":
                        ext = upload.name.rsplit(".", 1)[-1].lower()
                        if ext in ("txt", "md"):
                            source = upload.getvalue().decode("utf-8", errors="ignore")
                        else:
                            source, note = SAMPLE_TEXT, "데모에서는 .docx·.hwpx 파일 대신 샘플 원문으로 분석해요."
                    form = {"title": title.strip(), "date": day.isoformat(), "start": start, "end": end,
                            "attendees": attendees, "purpose": purpose.strip(), "ref_file": ref.name if ref else None,
                            "file_name": upload.name if method == "파일 업로드" else "붙여넣은 텍스트"}
                    meeting = new_meeting(form, me, source, members(), note)
                    add_meeting(meeting)
                    for k in ("reg_text", "reg_title", "reg_purpose", "reg_att"):
                        st.session_state.pop(k, None)
                    flash("분석을 시작했어요.")
                    router.go_now("progress", meeting_id=meeting["id"])

    with side:
        html('<div class="info-box"><strong>AI 결과는 모두 초안이에요.</strong> 주관자가 확정하기 전에는 업무가 배정되거나 외부로 전송되지 않아요. '
             '30~40분 분량 회의록은 보통 10분 안에 검토할 수 있어요.</div>')
        if st.button("등록 후 진행 방식 · 작성 팁 ›", type="tertiary"):
            _guide_dialog()
