"""회의 목록: 전체 회의 / 내 회의 범위와 상태별 보기."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import meetings, members
from data.constants import STEPS
from data.seed import INTEGRATIONS
from services.analysis import progress_of
from services.rules import member_name, warnings_of
from ui.components import html, status_badge
from ui.layout import head_row, page_head, panel, panel_head, tap
from utils.dates import fmt_d
from utils.text import esc

SCOPES = {"all": "전체 회의", "mine": "내 회의", "org": "내가 주관한 회의", "att": "내가 참석한 회의"}
SCOPE_HINT = {"all": "열람 권한이 있는 모든 회의", "mine": "내가 주관하거나 참석한 회의", "org": "내가 등록하고 확정·전달하는 회의", "att": "다른 사람이 주관하고 내가 참석한 회의"}
FILTERS = {"all": "전체", "PROCESSING": "분석 중", "REVIEW_PENDING": "검토 대기", "CONFIRMED": "확정 · 전달 전", "SYNCED": "전달 완료", "FAIL": "실패"}
SORTS = {"date-desc": "최신 회의순", "date-asc": "오래된 회의순", "title": "회의명순"}


def _in_scope(m: dict, scope: str, my_id: str) -> bool:
    if scope == "all":
        return True
    if scope == "mine":
        return m["organizer"] == my_id or my_id in m["attendees"]
    if scope == "org":
        return m["organizer"] == my_id
    return my_id in m["attendees"] and m["organizer"] != my_id


def _match(m: dict, f: str) -> bool:
    if f == "all":
        return True
    if f == "FAIL":
        return m["status"] in ("FAILED", "SYNC_PARTIAL")
    return m["status"] == f


def _sub_text(m: dict) -> tuple[str, str]:
    s = m["status"]
    if s == "PROCESSING":
        p = progress_of(m)
        return f"{STEPS[min(p['step'], len(STEPS) - 1)]} 단계 · {p['pct']}%", ""
    if s == "REVIEW_PENDING":
        n = sum(len(warnings_of(a, members())) for a in m["detail"]["actions"])
        return (f"검토 경고 {n}건" if n else "경고 없음") + (" · 다시 확정 필요" if m.get("reconfirm") else ""), "c-wait" if n else ""
    if s == "FAILED":
        return m["fail"]["title"], "c-err"
    if s == "CONFIRMED":
        return f"{m['confirmed_at']} 확정 · 버전 {m['version']}", ""
    if s == "SYNC_PARTIAL":
        return ", ".join(INTEGRATIONS[d["target"]]["name"] for d in m["deliveries"] if d["status"] == "FAILED") + " 실패", "c-err"
    return " · ".join(INTEGRATIONS[d["target"]]["name"] for d in m["deliveries"]), ""


def _action(m: dict, my_id: str) -> tuple[str, str, dict, bool]:
    s, own = m["status"], m["organizer"] == my_id
    if s == "PROCESSING":
        return "진행 보기", "progress", {"meeting_id": m["id"]}, False
    if s == "FAILED":
        return "다시 시작", "progress", {"meeting_id": m["id"]}, False
    if s == "REVIEW_PENDING":
        return ("검토하기" if own else "결과 보기"), "review", {"meeting_id": m["id"]}, own
    if s == "CONFIRMED" and own:
        return "전달하기", "delivery", {"meeting_id": m["id"]}, True
    if s == "SYNC_PARTIAL":
        return "전달 현황", "delivery", {"meeting_id": m["id"]}, False
    return "결과 보기", "review", {"meeting_id": m["id"]}, False


def _set_status(key: str) -> None:
    st.session_state.mt_status = key


def _reset_filters() -> None:
    st.session_state.mt_q = ""
    st.session_state.mt_sort = "date-desc"
    st.session_state.mt_status = "all"


GRID = [3.6, 1.6, 1.3, 2.1, 1.3, 1.2]


def render() -> None:
    me = auth.me()
    left, actions = head_row()
    with left:
        page_head("회의 목록", "구성원은 누구나 열람할 수 있고, 결과 수정과 확정은 회의 주관자만 할 수 있어요.", ["홈", "회의 목록"])
    actions.button("＋ 새 회의 등록", type="primary", on_click=router.go, args=("register",))

    # 다른 화면에서 범위·상태를 지정해 들어온 경우 한 번만 반영합니다.
    if st.session_state.get("_mt_route") != id(router.current()):
        st.session_state._mt_route = id(router.current())
        st.session_state.mt_scope = router.arg("scope", "all")
        st.session_state.mt_status = router.arg("filter", "all")
    st.session_state.setdefault("mt_status", "all")

    visible = [m for m in meetings() if auth.can_view(m)]
    with st.container(horizontal=True, vertical_alignment="bottom"):
        with st.container(key="ultab_scope", width="content"):
            scope = st.segmented_control("보기 범위", list(SCOPES), key="mt_scope", label_visibility="collapsed",
                                         format_func=lambda k: f"{SCOPES[k]} {sum(1 for m in visible if _in_scope(m, k, me['id']))}") or "all"
        with st.container(horizontal=True, horizontal_alignment="right"):
            html(f'<span class="small muted">{SCOPE_HINT[scope]}</span>', width="content")
    scoped = [m for m in visible if _in_scope(m, scope, me["id"])]

    status = st.session_state.mt_status
    with panel("status", flush=True):
        with st.container(key="cells", horizontal=True):
            for key, label in FILTERS.items():
                n = sum(1 for m in scoped if _match(m, key))
                dot = "" if key == "all" else "<i></i>"
                with tap(f"{'cellon' if status == key else 'cell'}_{key}", _set_status, (key,), label=f"{label} 보기"):
                    html(f'<div class="cell-l">{dot}{label}</div><div class="cell-n">{n}<small>건</small></div>')

    with panel("filters"):
        c1, c2, c3 = st.columns([4, 4, 0.9], vertical_alignment="bottom")
        q = c1.text_input("검색어", key="mt_q", placeholder="회의명, 참석자, 부서로 검색", icon=":material/search:")
        sort = c2.selectbox("정렬", list(SORTS), format_func=SORTS.get, key="mt_sort")
        c3.button("초기화", on_click=_reset_filters, use_container_width=True)

    rows = [m for m in scoped if _match(m, status)]
    if q.strip():
        s = q.strip()
        rows = [m for m in rows if s in m["title"] or s in m["dept"] or any(s in member_name(members(), x) for x in m["attendees"])]
    if sort == "title":
        rows.sort(key=lambda m: m["title"])
    else:
        rows.sort(key=lambda m: m["date"] + m["start"], reverse=(sort == "date-desc"))

    with panel("table"):
        panel_head(f'전체 <span class="c-accent">{len(rows)}</span>건', "상태는 실시간으로 바뀌어요")
        cols = " ".join(f"{w}fr" for w in GRID)
        html(f'<div class="thead" style="grid-template-columns:{cols}"><span>회의명</span><span>회의 일시</span><span>주관 · 참석자</span>'
             '<span>진행 상태</span><span>액션 아이템</span><span>관리</span></div>')
        if not rows:
            html('<div class="empty muted" style="padding:24px;text-align:center">조건에 맞는 회의가 없어요.</div>')
        for m in rows:
            _row(m, me["id"])


def _row(m: dict, my_id: str) -> None:
    sub, sub_cls = _sub_text(m)
    acts = [a for a in m["detail"]["actions"] if a["review"] != "rejected"] if m.get("detail") else None
    lock = " · 🔒 " + ("주관자만" if m["access"] == "organizer" else "팀 전체") if m["access"] != "attendees" else ""
    failed = m["status"] in ("FAILED", "SYNC_PARTIAL")
    with st.container(key=f"{'trf' if failed else 'tr'}_{m['id']}"):
        cols = st.columns(GRID, vertical_alignment="center")
        with cols[0]:
            html(f'<div class="m-title">{esc(m["title"])}</div><div class="m-sub">{esc(m["dept"])} · {esc(m["file"])}{lock}</div>')
        with cols[1]:
            html(f'<div style="font-size:14px">{fmt_d(m["date"], True)}</div><div class="m-sub">{m["start"]} – {m["end"]}</div>')
        with cols[2]:
            html(f'<div style="font-size:14px">{esc(member_name(members(), m["organizer"]))}</div><div class="m-sub">참석 {len(m["attendees"])}명</div>')
        with cols[3]:
            html(f'{status_badge(m["status"])}<div class="m-sub {sub_cls}" style="margin-top:4px">{esc(sub)}</div>')
        with cols[4]:
            if acts is None:
                html('<span class="muted">–</span>')
            else:
                html(f'<div style="font-size:14px">{len(acts)}건</div><div class="m-sub">담당자 미정 {sum(1 for a in acts if not a["assignee"])}</div>')
        with cols[5]:
            label, page_name, args, strong = _action(m, my_id)
            with st.container(key=f"{'line' if strong else 'sm'}_open_{m['id']}"):
                st.button(label, key=f"open_{m['id']}", on_click=router.go, args=(page_name,), kwargs=args)
