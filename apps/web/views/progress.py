"""분석 진행 현황 (실패한 경우도 이 화면에서 처리) — 프로토타입 ProgressPage와 같은 구성."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import flash, get_meeting, members, remove_meeting, tick_analysis
from data.constants import STEPS
from services.analysis import progress_of, restart
from services.rules import member_name
from ui.components import html, status_badge
from ui.layout import page_head, panel
from utils.dates import fmt_d, now_time
from utils.text import esc, srange

TRACK_DESC = {"요약": "회의 목적, 안건별 논의, 결정사항, 미해결 이슈, 다음 회의 안건",
              "액션 아이템": "업무 내용, 담당자, 기한, 관련 안건, 원문 근거"}


# ---------------------------------------------------------------- 작은 조각 (HTML)

def _run_badge(text: str) -> str:
    return f'<span class="badge b-run"><span class="dot"></span>{text}</span>'


def _info_row(m: dict) -> None:
    att = f"{member_name(members(), m['attendees'][0])} 외 {len(m['attendees']) - 1}명" if len(m["attendees"]) > 1 else member_name(members(), m["attendees"][0])
    with panel("pg_info"):
        html(f'<div class="info-row"><div class="grow"><div>{status_badge(m["status"])} <span class="muted small">{esc(m["dept"])}</span></div>'
             f'<div class="h2">{esc(m["title"])}</div></div><dl class="meta">'
             f'<div><dt>회의 일시</dt><dd>{fmt_d(m["date"], True)} {m["start"]} – {m["end"]}</dd></div>'
             f'<div><dt>참석자</dt><dd>{esc(att)}</dd></div><div><dt>회의록</dt><dd>{esc(m["file"])}</dd></div>'
             f'<div><dt>주관자</dt><dd>{esc(member_name(members(), m["organizer"]))}</dd></div></dl></div>')


def _stepper(step: int, fail_at: int | None) -> str:
    n = len(STEPS)
    cells = []
    for i, name in enumerate(STEPS):
        st_ = "fail" if fail_at == i else "done" if i < step else "now" if i == step and fail_at is None else "wait"
        icon = "✓" if st_ == "done" else "✕" if st_ == "fail" else str(i + 1)
        label = {"done": "완료", "now": "진행 중", "fail": "실패", "wait": "대기"}[st_]
        cells.append(f'<li class="st st-{st_}"><span class="st-c">{icon}</span><span class="st-l">{esc(name)}</span><span class="st-s">{label}</span></li>')
    width = min(step, n - 1) / (n - 1) * 100
    return f'<ol class="stepper" style="--n:{n}"><li class="st-line"><span style="width:{width}%"></span></li>{"".join(cells)}</ol>'


def _tracks(step: int) -> str:
    out = []
    for name in ("요약", "액션 아이템"):
        state = '<span class="badge b-ok">검증 통과</span>' if step >= 5 else _run_badge("검증 중") if step >= 4 else _run_badge("추출 중")
        note = ""
        if name == "요약" and step >= 5:
            note = '<p class="small c-wait fw5">경고 1건과 함께 통과 · 결론이 나지 않은 논의 1건을 결정사항에서 제외했어요</p>'
        if name == "액션 아이템" and step == 4:
            note = '<p class="small c-accent fw5">근거 위치가 맞지 않은 주제 1개만 다시 추출하고 있어요 (1/2회)</p>'
        out.append(f'<div class="track"><div class="track-h"><strong>{name}</strong>{state}</div><p class="small muted">{TRACK_DESC[name]}</p>{note}</div>')
    return '<div class="tracks">' + "".join(out) + "</div>"


def _topic_table(det: dict, step: int) -> str:
    def cell(kind: str, i: int) -> str:
        if step >= 5:
            return '<span class="c-ok fw6 small">검증 완료</span>'
        if step == 4:
            return _run_badge("재추출 중") if kind == "a" and i == 1 else '<span class="c-ok fw6 small">검증 완료</span>'
        return _run_badge("추출 중")
    rows = "".join(f'<tr><td class="muted">{i + 1}</td><td class="fw5">{esc(t["title"])}</td><td class="muted nw">{srange(t["from"], t["to"])}</td>'
                   f'<td>{cell("s", i)}</td><td>{cell("a", i)}</td></tr>' for i, t in enumerate(det["topics"]))
    return ('<table class="pv-tbl pg-tbl"><thead><tr><th>No</th><th>주제</th><th>원문 범위</th><th>요약</th><th>액션 아이템</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>")


def _logs(m: dict, step: int, live: bool) -> str:
    out = []
    if live:
        out.append(f'<div class="log now"><span class="muted">{now_time()}</span><span class="spin"></span>'
                   f'<div class="fw6 c-accent">{esc(STEPS[min(step, len(STEPS) - 1)])} 단계를 처리하고 있어요</div></div>')
    for log in m["logs"]:
        icon = {"ok": "✓", "warn": "⚠", "err": "✕"}[log["kind"]]
        extra = f'<div class="small c-wait">{esc(log["extra"])}</div>' if log.get("extra") else ""
        out.append(f'<div class="log"><span class="muted">{esc(log["t"])}</span><span class="log-ic {log["kind"]}">{icon}</span>'
                   f'<div class="{"c-err" if log["kind"] == "err" else ""}">{esc(log["text"])}{extra}</div></div>')
    return "".join(out)


# ---------------------------------------------------------------- 동작

@st.dialog("회의를 삭제할까요?")
def _confirm_delete(meeting_id: str, cancel: bool) -> None:
    st.write("등록한 회의 정보와 지금까지 처리한 결과가 삭제돼요.")
    with st.container(key="pg_del_foot", horizontal=True, horizontal_alignment="right"):
        if st.button("돌아가기", key="pg_del_back"):
            st.rerun()
        with st.container(key="danger_del", width="content"):
            if st.button("삭제", key="pg_del_ok"):
                remove_meeting(meeting_id)
                flash("회의를 삭제했어요." if not cancel else "분석을 취소하고 회의를 삭제했어요.")
                router.go_now("meetings")


def _retry(meeting: dict, why: str, file_name: str | None = None) -> None:
    restart(meeting, why, file_name)
    flash("분석을 다시 시작했어요.")


# ---------------------------------------------------------------- 실시간 영역 (1.5초마다 갱신)

@st.fragment(run_every=1.5)
def _live(meeting_id: str) -> None:
    for done_m in tick_analysis():
        st.toast(f"‘{done_m['title']}’ 분석이 끝났어요.", icon="✅")
    m = get_meeting(meeting_id)
    if not m:
        return
    _info_row(m)
    p = progress_of(m)
    failed, done = p["failed"], p["done"]
    step = len(STEPS) if done else (m["fail"]["step"] if failed else p["step"])
    det = m.get("detail") or m.get("pending_detail")

    # ① 진행률 패널
    with panel("pg_main"):
        if failed:
            title, desc = "분석이 중단됐어요", f"{STEPS[m['fail']['step']]} 단계에서 멈췄어요. 아래에서 다시 시작할 수 있어요."
        elif done:
            title, desc = "분석이 끝났어요. 결과를 검토해 주세요", "검토와 확정 전까지 결과는 참석자에게 공유되지 않아요."
        else:
            title = ("추출한 결과를 원문과 대조해 검증하고 있어요" if step >= 4 else
                     "주제별로 요약과 액션 아이템을 추출하고 있어요" if step >= 3 else "회의록을 읽고 주제를 나누고 있어요")
            desc = f"약 <strong>{max(1, round(p.get('left_sec', 0) / 60 * 10))}분</strong> 후 검토할 수 있어요. 이 화면을 벗어나도 분석은 계속되고, 끝나면 알림으로 알려드려요."
        pct = 100 if done else p["pct"]
        num = "" if failed else f'<div class="prog-num"><strong>{pct}%</strong><span>{min(step + (0 if done else 1), len(STEPS))} / {len(STEPS)}단계</span></div>'
        note = f'<p class="small c-wait">{esc(m["demo_note"])}</p>' if m.get("demo_note") and not failed else ""
        html(f'<div class="prog-head"><div><div class="h2 {"c-err" if failed else ""}">{title}</div><p class="page-desc">{desc}</p>{note}</div>{num}</div>'
             f'<div class="bar-lg {"fail" if failed else ""}"><span style="width:{max(pct, 8) if failed else pct}%"></span></div>'
             + _stepper(step, m["fail"]["step"] if failed else None)
             + (_tracks(step) if not failed and step >= 3 else ""))
        if failed:
            with st.container(key="pg_fail"):
                html(f'<div class="fail-msg"><span class="fail-ic">⚠</span><div><strong>{esc(m["fail"]["title"])}</strong><p>{esc(m["fail"]["text"])}</p>'
                     f'<p class="small muted">오류 코드 {m["fail"]["code"]} · {esc(m.get("failed_at", ""))} · 입력한 회의 정보와 원본 파일은 그대로 보관돼요.</p></div></div>')
                with st.container(key="pg_fail_actions", horizontal=True, vertical_alignment="center", gap="small"):
                    with st.container(key="refile_box", width="content"):
                        new_file = st.file_uploader("파일 다시 올리기", key=f"refile_{meeting_id}", label_visibility="collapsed")
                    if new_file is not None:
                        _retry(m, "file", new_file.name)
                        st.rerun()
                    if st.button(f"같은 파일로 다시 시도 (남은 {m['retries']}회)", disabled=m["retries"] <= 0, key="pg_retry"):
                        _retry(m, "retry")
                        st.rerun()
                    with st.container(key="cta_paste", width="content"):
                        if st.button("텍스트로 붙여넣기 ›", type="tertiary", key="pg_paste"):
                            router.go_now("register")
        if done:
            with st.container(horizontal=True, horizontal_alignment="right", key="pg_done"):
                if st.button("결과 검토하기", type="primary", key="pg_go_review"):
                    router.go_now("review", meeting_id=meeting_id)

    # ② 아래 2단: 주제별 상태 · 진행 내역 | 확인된 내용 · 안내 · 버튼
    main, side = st.columns([2.75, 1], gap="medium")
    with main:
        if not failed and det and step >= 3:
            with panel("pg_topics"):
                html(f'<div class="p-h"><span class="tt">주제별 진행 상태</span><span class="side">주제 {len(det["topics"])}개 · 문단 {len(det["segments"])}개</span></div>'
                     + _topic_table(det, step))
        with panel("pg_logs"):
            live = not done and not failed
            html('<div class="p-h"><span class="tt">진행 내역</span>' + ('<span class="live"><span class="dot"></span>실시간 업데이트 중</span>' if live else "") + "</div>"
                 + _logs(m, step, live))
    with side:
        if not failed:
            with panel("pg_found"):
                rows = [("주제", f"{len(det['topics'])}개" if det and step >= 3 else "확인 중"),
                        ("결정사항", f"{len(det['decisions'])}건" if det and step >= 4 else "확인 중"),
                        ("미해결 이슈", f"{len(det['issues'])}건" if det and step >= 4 else "확인 중"),
                        ("액션 아이템 후보", f"{len(det['actions'])}건" if det and step >= 4 else "확인 중")]
                html('<div class="p-h"><span class="tt">지금까지 확인된 내용</span></div>'
                     + "".join(f'<div class="kv-row"><span class="muted">{k}</span><strong>{v}</strong></div>' for k, v in rows))
        with panel("pg_help"):
            html('<div class="p-h"><span class="tt">문제가 생기면</span></div><p class="small muted" style="margin:0;line-height:1.6">'
                 "해당 단계만 자동으로 한 번 더 시도해요. 그래도 안 되면 이 화면에 원인과 다시 시작하는 방법이 표시되고, 완료된 단계까지의 결과는 보관돼요.</p>")
        with st.container(horizontal=True, key="pg_side_btns"):
            if st.button("목록으로", key="pg_list", use_container_width=True):
                router.go_now("meetings")
            if (not done or failed) and auth.is_owner(m):
                if st.button("회의 삭제" if failed else "분석 취소", key="pg_delete", use_container_width=True):
                    _confirm_delete(meeting_id, not failed)


def render() -> None:
    m = get_meeting(router.arg("meeting_id"))
    if not m or not auth.can_view(m):
        st.warning("회의를 찾을 수 없어요. 삭제되었거나 열람 권한이 없는 회의예요.")
        st.button("회의 목록으로", on_click=router.go, args=("meetings",))
        return
    page_head("분석 진행 현황", crumb=["홈", "회의", "분석 진행"])
    _live(m["id"])