"""회의 목록: 전체 회의 / 내 회의 범위와 상태별 보기, 기간별 많이 논의된 주제(누르면 목록이 그 주제로 걸러짐)."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from core import auth, router
from core.state import meetings, members
from data.constants import STEPS
from data.seed import INTEGRATIONS
from services.analysis import progress_of
from services.rules import member_name, warnings_of
from ui import visuals
from ui.components import html, status_badge
from ui.layout import head_row, page_head, panel, panel_head, tap
from utils.dates import TODAY, fmt_d
from utils.text import esc

SCOPES = {"all": "전체 회의", "mine": "내 회의", "org": "내가 주관한 회의", "att": "내가 참석한 회의"}
SCOPE_HINT = {"all": "열람 권한이 있는 모든 회의", "mine": "내가 주관하거나 참석한 회의", "org": "내가 등록하고 확정·전달하는 회의", "att": "다른 사람이 주관하고 내가 참석한 회의"}
FILTERS = {"all": "전체", "PROCESSING": "분석 중", "REVIEW_PENDING": "검토 대기", "CONFIRMED": "확정 · 전달 전", "SYNCED": "전달 완료", "FAIL": "실패"}
SORTS = {"date-desc": "최신 회의순", "date-asc": "오래된 회의순", "title": "회의명순"}
PERIODS = {"week": "이번 주", "month": "이번 달", "all": "전체 기간"}


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
    st.session_state.mt_topic = None


# ---------------------------------------------------------------- 많이 논의된 주제

def _period_range(key: str) -> tuple[str, str] | None:
    if key == "week":
        monday = TODAY - timedelta(days=TODAY.weekday())
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    if key == "month":
        first = TODAY.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first.isoformat(), (nxt - timedelta(days=1)).isoformat()
    return None


def _in_period(m: dict, key: str) -> bool:
    rng = _period_range(key)
    return rng is None or rng[0] <= m["date"] <= rng[1]


def _pick_topic(word: str) -> None:
    """같은 주제를 다시 누르면 필터를 풀어요."""
    st.session_state.mt_topic = None if st.session_state.get("mt_topic") == word else word


def _clear_topic() -> None:
    st.session_state.mt_topic = None


def _toggle_topics() -> None:
    st.session_state.mt_topics_open = not st.session_state.get("mt_topics_open", True)


def _topic_panel(scoped: list[dict], period: str, topics: list[dict]) -> None:
    pool = [m for m in scoped if _in_period(m, period)]
    analyzed = [m for m in pool if m.get("detail")]
    opened = st.session_state.get("mt_topics_open", True)
    label = PERIODS[period]
    with panel("topics"):
        with st.container(horizontal=True, vertical_alignment="center"):
            html('<div class="p-h" style="margin:0"><span class="tt">많이 논의된 주제</span></div>'
                 '<div class="small muted">원이 클수록 많이 논의됐어요 · 누르면 아래 목록이 그 주제 회의로 걸러져요</div>')
            with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center", gap="small"):
                st.selectbox("기간", list(PERIODS), key="mt_period", label_visibility="collapsed", width=200,
                             format_func=lambda k: PERIODS[k] + (f" ({fmt_d(_period_range(k)[0])[:5]}–{fmt_d(_period_range(k)[1])[:5]})" if _period_range(k) else ""))
                with st.container(key="cta_topics_fold", width="content"):
                    st.button("접기 ⌃" if opened else "펼치기 ⌄", key="topics_fold", type="tertiary", on_click=_toggle_topics)
        if not opened:
            return
        left, right = st.columns([1.3, 1], gap="large", vertical_alignment="center")
        with left:
            if topics:
                visuals.topic_map(topics, f"회의 {len(analyzed)}건", _pick_topic, center_title=label, selected=st.session_state.get("mt_topic"))
            else:
                st.caption(f"{label}에 분석이 끝난 회의가 아직 없어요.")
        with right:
            total = sum(visuals.meeting_minutes(m) for m in pool)
            hours = f"{total // 60}시간" + (f" {total % 60}분" if total % 60 else "") if total >= 60 else f"{total}분"
            # 안건이 하나뿐인 회의(예: 주간 스프린트)는 회의 전체가 한 안건이라 빼고, 안건이 여러 개인 회의에서 가장 오래 논의한 안건을 골라요
            pairs = [(t, m) for m in analyzed for t in visuals.topic_times(m) if len(m["detail"]["topics"]) > 1]
            longest = max(pairs or [(t, m) for m in analyzed for t in visuals.topic_times(m)], key=lambda x: x[0]["min"], default=None)
            with st.container(key="topic_side"):
                html(f'<div class="ts-k">{label} 회의 시간</div><div class="ts-n">{hours} <small>· {len(pool)}건</small></div>')
                if longest:
                    t, m = longest
                    with tap(f"ts_longest_{m['id']}", router.go, ("review",), {"meeting_id": m["id"]}, label=f"{m['title']} 열기"):
                        html(f'<div class="ts-k">가장 오래 논의</div><div class="ts-t">{esc(t["title"])} <b>{round(t["min"])}분</b></div>'
                             f'<div class="ts-s">{esc(m["title"])} · 누르면 그 회의로</div>')


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

    st.session_state.setdefault("mt_period", "week")
    st.session_state.setdefault("mt_topic", None)
    period = st.session_state.mt_period
    topics = visuals.week_topics([m for m in scoped if _in_period(m, period)])
    by_word = {t["word"]: t for t in topics}
    if st.session_state.mt_topic and st.session_state.mt_topic not in by_word:   # 기간·범위가 바뀌어 사라진 주제는 풀어요
        st.session_state.mt_topic = None
    topic = by_word.get(st.session_state.mt_topic)

    status = st.session_state.mt_status
    with panel("status", flush=True):
        with st.container(key="cells", horizontal=True):
            for key, label in FILTERS.items():
                n = sum(1 for m in scoped if _match(m, key))
                dot = "" if key == "all" else "<i></i>"
                with tap(f"{'cellon' if status == key else 'cell'}_{key}", _set_status, (key,), label=f"{label} 보기"):
                    html(f'<div class="cell-l">{dot}{label}</div><div class="cell-n">{n}<small>건</small></div>')

    _topic_panel(scoped, period, topics)

    with panel("filters"):
        c1, c2, c3 = st.columns([4, 4, 0.9], vertical_alignment="bottom")
        q = c1.text_input("검색어", key="mt_q", placeholder="회의명, 참석자, 부서로 검색", icon=":material/search:")
        sort = c2.selectbox("정렬", list(SORTS), format_func=SORTS.get, key="mt_sort")
        c3.button("초기화", on_click=_reset_filters, use_container_width=True)
        if topic:
            with st.container(key="topic_chip", horizontal=True, vertical_alignment="center", gap="small"):
                html(f'<span class="small muted">{PERIODS[period]} 회의 중</span>', width="content")
                st.button(f"주제: {topic['word']}  ✕", key="topic_clear", on_click=_clear_topic, help="주제 필터 해제")

    rows = [m for m in scoped if _match(m, status)]
    hits = {}
    if topic:
        for m in rows:
            h = visuals.topic_hits(m, topic["base"]) if _in_period(m, period) else None
            if h:
                hits[m["id"]] = h
        rows = [m for m in rows if m["id"] in hits]
    if q.strip():
        s = q.strip()
        rows = [m for m in rows if s in m["title"] or s in m["dept"] or any(s in member_name(members(), x) for x in m["attendees"])]
    if sort == "title":
        rows.sort(key=lambda m: m["title"])
    else:
        rows.sort(key=lambda m: m["date"] + m["start"], reverse=(sort == "date-desc"))

    with panel("table"):
        if topic:
            panel_head(f'‘{esc(topic["word"])}’ 회의 <span class="c-accent">{len(rows)}</span>건 <span class="small muted" style="font-weight:400">· 전체 {len(scoped)}건 중</span>', "상태는 실시간으로 바뀌어요")
        else:
            panel_head(f'전체 <span class="c-accent">{len(rows)}</span>건', "상태는 실시간으로 바뀌어요")
        cols = " ".join(f"{w}fr" for w in GRID)
        html(f'<div class="thead" style="grid-template-columns:{cols}"><span>회의명</span><span>회의 일시</span><span>주관 · 참석자</span>'
             '<span>진행 상태</span><span>액션 아이템</span><span>관리</span></div>')
        if not rows:
            html('<div class="empty muted" style="padding:24px;text-align:center">조건에 맞는 회의가 없어요.</div>')
        for m in rows:
            _row(m, me["id"], topic["word"] if topic else "", hits.get(m["id"]))


def _hit_text(h: dict) -> str:
    parts = [f"안건 {', '.join(map(str, h['topics']))}"] if h["topics"] else []
    parts += [f"{k} {h[key]}" for key, k in (("decisions", "결정"), ("actions", "액션"), ("issues", "이슈")) if h[key]]
    if not parts and h["talk"]:
        parts.append(f"발언 {h['talk']}회")
    return " · ".join(parts)


def _row(m: dict, my_id: str, word: str = "", hit: dict | None = None) -> None:
    sub, sub_cls = _sub_text(m)
    acts = [a for a in m["detail"]["actions"] if a["review"] != "rejected"] if m.get("detail") else None
    lock = " · 🔒 " + ("주관자만" if m["access"] == "organizer" else "팀 전체") if m["access"] != "attendees" else ""
    failed = m["status"] in ("FAILED", "SYNC_PARTIAL")
    with st.container(key=f"{'trf' if failed else 'tr'}_{m['id']}"):
        cols = st.columns(GRID, vertical_alignment="center")
        with cols[0]:
            found = f'<div class="m-sub"><span class="topic-tag">{esc(word)}</span> {_hit_text(hit)}</div>' if hit else ""
            html(f'<div class="m-title">{esc(m["title"])}</div><div class="m-sub">{esc(m["dept"])} · {esc(m["file"])}{lock}</div>{found}')
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