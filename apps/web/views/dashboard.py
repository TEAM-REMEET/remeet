# """대시보드: 검색, 내가 주관한 회의 현황, 검토할 회의, 확인이 필요한 일, 나에게 배정된 업무."""
# from __future__ import annotations

# import streamlit as st

# from core import auth, router
# from core.state import meetings, members
# from data.constants import WORK_LABEL
# from services.rules import warnings_of
# from ui.components import badge, html, status_badge
# from ui.layout import head_row, page_head, panel, panel_bar, panel_head, tap
# from utils.dates import dday, dday_label, fmt_d
# from utils.text import esc

# CELLS = [("PROCESSING", "분석 중", "var(--accent)"), ("REVIEW_PENDING", "검토 대기", "var(--warn)"), ("CONFIRMED", "확정 · 전달 전", "var(--ok)"),
#          ("SYNCED", "전달 완료", "var(--fg-3)"), ("FAIL", "실패", "var(--err)")]
# ATTENTION = {
#     "CONFIRMED": ("전달 대기", "ok", "확정한 결과를 아직 전달하지 않았어요.", "전달하기", "delivery"),
#     "FAILED": ("분석 실패", "err", "회의록을 분석하지 못했어요.", "다시 시작하기", "progress"),
#     "SYNC_PARTIAL": ("전달 실패", "err", "일부 대상에 전달하지 못했어요.", "전달 현황 보기", "delivery"),
# }
# SEARCH_WORDS = ["본인인증", "요금제", "A/B 테스트", "법무", "교육"]


# def _match(m: dict, key: str) -> bool:
#     return m["status"] in ("FAILED", "SYNC_PARTIAL") if key == "FAIL" else m["status"] == key


# def my_tasks(member_id: str) -> list[dict]:
#     out = []
#     for m in meetings():
#         if m.get("detail") and m["status"] in ("CONFIRMED", "SYNCED", "SYNC_PARTIAL"):
#             out += [{**a, "meeting": m} for a in m["detail"]["actions"] if a["review"] == "approved" and a["assignee"] == member_id]
#     return sorted(out, key=lambda a: a["due"] or "9999")


# def _search() -> None:
#     q = st.session_state.get("dash_search", "").strip()
#     if q:
#         router.go("search", q=q)


# def _search_box() -> None:
#     with panel("search"):
#         with st.container(horizontal=True, vertical_alignment="center"):
#             st.text_input("검색", key="dash_search", placeholder="회의, 결정사항, 업무, 미해결 이슈를 검색하세요", label_visibility="collapsed",
#                           on_change=_search, icon=":material/search:")
#             st.button("검색", key="dash_search_btn", type="primary", on_click=_search, width=88)
#         with st.container(horizontal=True, vertical_alignment="center", gap="small"):
#             html('<span class="small muted">자주 찾는 검색어</span>', width="content")
#             for word in SEARCH_WORDS:
#                 st.button(word, key=f"chip_{word}", on_click=router.go, args=("search",), kwargs={"q": word})


# def _status_cells(mine: list[dict]) -> None:
#     with panel("status", flush=True):
#         panel_bar("내가 주관한 회의 처리 현황", "최근 30일")
#         with st.container(key="cells", horizontal=True):
#             for key, label, color in CELLS:
#                 with tap(f"cell_{key}", router.go, ("meetings",), {"filter": key, "scope": "org"}, label=f"{label} 회의 보기"):
#                     html(f'<div class="cell-l"><i style="background:{color}"></i>{label}</div>'
#                          f'<div class="cell-n">{sum(1 for m in mine if _match(m, key))}<small>건</small></div>')


# def _review_list(reviews: list[dict]) -> None:
#     with panel("reviews"):
#         with st.container(horizontal=True, vertical_alignment="center"):
#             panel_head(f'내가 검토할 회의 <span class="c-wait">{len(reviews)}</span>')
#             st.button("전체 보기 ›", key="rv_all", type="tertiary", on_click=router.go, args=("meetings",), kwargs={"filter": "REVIEW_PENDING", "scope": "org"})
#         if not reviews:
#             st.caption("검토할 회의가 없어요.")
#         box = st.container(key="scrollsm_reviews")
#         for m in reviews:
#             warns: dict[str, int] = {}
#             for a in m["detail"]["actions"]:
#                 for w in warnings_of(a, members()):
#                     warns[w["t"]] = warns.get(w["t"], 0) + 1
#             chips = "".join(f'<span class="warn-chip">{esc(k)} {v}</span>' for k, v in warns.items()) or '<span class="small c-ok fw6">경고 없음 · 바로 확정할 수 있어요</span>'
#             with box, st.container(horizontal=True, vertical_alignment="center"):
#                 html(f'<div class="m-title">{esc(m["title"])}</div><div class="m-sub">{fmt_d(m["date"], True)} {m["start"]} · 액션 {len(m["detail"]["actions"])}건 · '
#                      f'분석 완료 {m.get("analyzed_at", "")}</div><div style="margin-top:4px">{chips}</div>')
#                 with st.container(key=f"line_rv_{m['id']}", width="content"):
#                     st.button("검토하기", key=f"rv_{m['id']}", on_click=router.go, args=("review",), kwargs={"meeting_id": m["id"]})


# def _recent() -> None:
#     rows = [x for x in meetings() if auth.can_view(x)][:10]
#     with panel("recent"):
#         with st.container(horizontal=True, vertical_alignment="center"):
#             panel_head("최근 회의")
#             st.button("회의 목록 ›", key="recent_all", type="tertiary", on_click=router.go, args=("meetings",))
#         grid = "grid-template-columns:minmax(0,5fr) 3fr 2fr"
#         html(f'<div class="thead" style="{grid}"><span>회의명</span><span>회의 일시</span><span>상태</span></div>')
#         box = st.container(key="scroll_recent")
#         for m in rows:
#             with box, tap(f"li_{m['id']}", router.open_meeting, (m,), label=m["title"]):
#                 html(f'<div style="display:grid;{grid};gap:12px;padding:0 12px;font-size:14px;align-items:center">'
#                      f'<span class="fw5">{esc(m["title"])}</span><span class="muted">{fmt_d(m["date"])} {m["start"]}</span><span>{status_badge(m["status"])}</span></div>')


# def _attention(mine: list[dict]) -> None:
#     items = [m for m in mine if m["status"] in ATTENTION]
#     items.sort(key=lambda m: list(ATTENTION).index(m["status"]))
#     with panel("attention"):
#         panel_head("확인이 필요한 일")
#         if not items:
#             st.caption("처리할 일이 없어요.")
#         box = st.container(key="scrollsm_attention")
#         for m in items:
#             label, kind, text, cta, target = ATTENTION[m["status"]]
#             with box, st.container(key=f"li_att_{m['id']}", horizontal=True, gap="small"):
#                 html(badge(label, kind), width="content")
#                 with st.container(gap=None):
#                     html(f'<div style="font-size:14px;font-weight:500">{esc(text)}</div><div class="m-sub">{esc(m["title"])}</div>')
#                     with st.container(key=f"cta_att_{m['id']}"):
#                         st.button(cta + " ›", key=f"att_{m['id']}", type="tertiary", on_click=router.go, args=(target,), kwargs={"meeting_id": m["id"]})


# def _tasks(member_id: str) -> None:
#     tasks = [a for a in my_tasks(member_id) if a["work"] != "done"]
#     with panel("tasks"):
#         panel_head("나에게 배정된 업무", "확정된 회의 기준")
#         if not tasks:
#             st.caption("배정된 업무가 없어요.")
#         box = st.container(key="scroll_tasks")
#         for a in tasks:
#             n = dday(a["due"])
#             kind = "neutral" if n is None else "err" if n < 0 else "wait" if n <= 2 else "neutral"
#             with box, tap(f"li_task_{a['meeting']['id']}_{a['id']}", router.go, ("review",), {"meeting_id": a["meeting"]["id"], "focus": a["id"]}, label=a["title"]):
#                 html(f'<div style="display:grid;grid-template-columns:52px minmax(0,1fr) 16px;gap:8px;align-items:center">{badge(dday_label(n), kind)}'
#                      f'<div><div style="font-size:14px">{esc(a["title"])}</div><div class="m-sub">{esc(a["meeting"]["title"])} · {WORK_LABEL[a["work"]]}</div></div>'
#                      '<span class="muted">›</span></div>')


# def render() -> None:
#     me = auth.me()
#     mine = [m for m in meetings() if m["organizer"] == me["id"]]
#     reviews = [m for m in mine if m["status"] == "REVIEW_PENDING"]

#     left, actions = head_row()
#     with left:
#         page_head(f"안녕하세요, {me['name']} 님", f"검토를 기다리는 회의가 {len(reviews)}건 있어요." if reviews else "검토를 기다리는 회의가 없어요.")
#     with actions:
#         st.button("회의 목록", on_click=router.go, args=("meetings",))
#         st.button("＋ 새 회의 등록", type="primary", on_click=router.go, args=("register",))

#     _search_box()
#     _status_cells(mine)
#     left, right = st.container(key="eq_dash").columns([2.6, 1.4], gap="medium")
#     with left:
#         _review_list(reviews)
#         _recent()
#     with right:
#         _attention(mine)
#         _tasks(me["id"])


"""대시보드: 검색, 내가 주관한 회의 현황, 이번 주 키워드·회의 시간, 검토할 회의, 확인이 필요한 일, 나에게 배정된 업무."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from core import auth, router
from core.state import meetings, members
from data.constants import WORK_LABEL
from services.rules import warnings_of
from services.search import build_index
from ui import visuals
from ui.components import badge, html, status_badge
from ui.layout import head_row, page_head, panel, panel_bar, panel_head, tap
from utils.dates import TODAY, dday, dday_label, fmt_d
from utils.text import esc

CELLS = [("PROCESSING", "분석 중", "var(--accent)"), ("REVIEW_PENDING", "검토 대기", "var(--warn)"), ("CONFIRMED", "확정 · 전달 전", "var(--ok)"),
         ("SYNCED", "전달 완료", "var(--fg-3)"), ("FAIL", "실패", "var(--err)")]
ATTENTION = {
    "CONFIRMED": ("전달 대기", "ok", "확정한 결과를 아직 전달하지 않았어요.", "전달하기", "delivery"),
    "FAILED": ("분석 실패", "err", "회의록을 분석하지 못했어요.", "다시 시작하기", "progress"),
    "SYNC_PARTIAL": ("전달 실패", "err", "일부 대상에 전달하지 못했어요.", "전달 현황 보기", "delivery"),
}
SEARCH_WORDS = ["본인인증", "요금제", "A/B 테스트", "법무", "교육"]


def _match(m: dict, key: str) -> bool:
    return m["status"] in ("FAILED", "SYNC_PARTIAL") if key == "FAIL" else m["status"] == key


def my_tasks(member_id: str) -> list[dict]:
    out = []
    for m in meetings():
        if m.get("detail") and m["status"] in ("CONFIRMED", "SYNCED", "SYNC_PARTIAL"):
            out += [{**a, "meeting": m} for a in m["detail"]["actions"] if a["review"] == "approved" and a["assignee"] == member_id]
    return sorted(out, key=lambda a: a["due"] or "9999")


def _search() -> None:
    q = st.session_state.get("dash_search", "").strip()
    if q:
        router.go("search", q=q)


def _search_box() -> None:
    with panel("search"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.text_input("검색", key="dash_search", placeholder="회의, 결정사항, 업무, 미해결 이슈를 검색하세요", label_visibility="collapsed",
                          on_change=_search, icon=":material/search:")
            st.button("검색", key="dash_search_btn", type="primary", on_click=_search, width=88)
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            html('<span class="small muted">자주 찾는 검색어</span>', width="content")
            for word in SEARCH_WORDS:
                st.button(word, key=f"chip_{word}", on_click=router.go, args=("search",), kwargs={"q": word})


def _status_cells(mine: list[dict]) -> None:
    with panel("status", flush=True):
        panel_bar("내가 주관한 회의 처리 현황", "최근 30일")
        with st.container(key="cells", horizontal=True):
            for key, label, color in CELLS:
                with tap(f"cell_{key}", router.go, ("meetings",), {"filter": key, "scope": "org"}, label=f"{label} 회의 보기"):
                    html(f'<div class="cell-l"><i style="background:{color}"></i>{label}</div>'
                         f'<div class="cell-n">{sum(1 for m in mine if _match(m, key))}<small>건</small></div>')


def _review_list(reviews: list[dict]) -> None:
    with panel("reviews"):
        with st.container(horizontal=True, vertical_alignment="center"):
            panel_head(f'내가 검토할 회의 <span class="c-wait">{len(reviews)}</span>')
            st.button("전체 보기 ›", key="rv_all", type="tertiary", on_click=router.go, args=("meetings",), kwargs={"filter": "REVIEW_PENDING", "scope": "org"})
        if not reviews:
            st.caption("검토할 회의가 없어요.")
        box = st.container(key="scrollsm_reviews")
        for m in reviews:
            warns: dict[str, int] = {}
            for a in m["detail"]["actions"]:
                for w in warnings_of(a, members()):
                    warns[w["t"]] = warns.get(w["t"], 0) + 1
            chips = "".join(f'<span class="warn-chip">{esc(k)} {v}</span>' for k, v in warns.items()) or '<span class="small c-ok fw6">경고 없음 · 바로 확정할 수 있어요</span>'
            with box, st.container(horizontal=True, vertical_alignment="center"):
                html(f'<div class="m-title">{esc(m["title"])}</div><div class="m-sub">{fmt_d(m["date"], True)} {m["start"]} · 액션 {len(m["detail"]["actions"])}건 · '
                     f'분석 완료 {m.get("analyzed_at", "")}</div><div style="margin-top:4px">{chips}</div>')
                with st.container(key=f"line_rv_{m['id']}", width="content"):
                    st.button("검토하기", key=f"rv_{m['id']}", on_click=router.go, args=("review",), kwargs={"meeting_id": m["id"]})


def _recent() -> None:
    rows = [x for x in meetings() if auth.can_view(x)][:10]
    with panel("recent"):
        with st.container(horizontal=True, vertical_alignment="center"):
            panel_head("최근 회의")
            st.button("회의 목록 ›", key="recent_all", type="tertiary", on_click=router.go, args=("meetings",))
        grid = "grid-template-columns:minmax(0,5fr) 3fr 2fr"
        html(f'<div class="thead" style="{grid}"><span>회의명</span><span>회의 일시</span><span>상태</span></div>')
        box = st.container(key="scroll_recent")
        for m in rows:
            with box, tap(f"li_{m['id']}", router.open_meeting, (m,), label=m["title"]):
                html(f'<div style="display:grid;{grid};gap:12px;padding:0 12px;font-size:14px;align-items:center">'
                     f'<span class="fw5">{esc(m["title"])}</span><span class="muted">{fmt_d(m["date"])} {m["start"]}</span><span>{status_badge(m["status"])}</span></div>')


def _week_meetings() -> list[dict]:
    """이번 주(월~일) 회의 중 내가 볼 수 있는 것."""
    monday = TODAY - timedelta(days=TODAY.weekday())
    lo, hi = monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    return [m for m in meetings() if lo <= m["date"] <= hi and auth.can_view(m)]


def _insights() -> None:
    """이번 주 키워드(크기 = 언급 횟수)와 회의별 사용 시간(막대 안 구간 = 안건)."""
    week = _week_meetings()
    analyzed = [m for m in week if m.get("detail")]
    left, right = st.container(key="eq_insight").columns([1.25, 1], gap="medium")
    with left, panel("keywords"):
        panel_head("이번 주 키워드", f"회의 {len(analyzed)}건 · 많이 나온 말일수록 크게")
        visuals.keyword_cloud(visuals.keywords(build_index(analyzed, members())), lambda w: router.go("search", q=w))
        html('<div class="ins-foot">키워드를 누르면 관련 결정사항·업무와 회의 흐름을 볼 수 있어요.</div>')
    with right, panel("timebars"):
        total = sum(visuals.meeting_minutes(m) for m in week)
        panel_head("회의 시간은 어디에 쓰였나", f"이번 주 {len(week)}건 · 총 {total // 60}시간 {total % 60}분" if total % 60 else f"이번 주 {len(week)}건 · 총 {total // 60}시간")
        if not week:
            st.caption("이번 주 회의가 없어요.")
        longest = max((visuals.meeting_minutes(m) for m in week), default=1)
        for m in sorted(week, key=visuals.meeting_minutes, reverse=True):
            with tap(f"tb_{m['id']}", router.open_meeting, (m,), label=f"{m['title']} 열기"):
                html(visuals.time_bar_html(m, longest))
        html('<div class="ins-foot">막대 안 칸은 안건이에요. 누르면 그 회의의 마인드맵으로 이동해요.</div>')


def _attention(mine: list[dict]) -> None:
    items = [m for m in mine if m["status"] in ATTENTION]
    items.sort(key=lambda m: list(ATTENTION).index(m["status"]))
    with panel("attention"):
        panel_head("확인이 필요한 일")
        if not items:
            st.caption("처리할 일이 없어요.")
        box = st.container(key="scrollsm_attention")
        for m in items:
            label, kind, text, cta, target = ATTENTION[m["status"]]
            with box, st.container(key=f"li_att_{m['id']}", horizontal=True, gap="small"):
                html(badge(label, kind), width="content")
                with st.container(gap=None):
                    html(f'<div style="font-size:14px;font-weight:500">{esc(text)}</div><div class="m-sub">{esc(m["title"])}</div>')
                    with st.container(key=f"cta_att_{m['id']}"):
                        st.button(cta + " ›", key=f"att_{m['id']}", type="tertiary", on_click=router.go, args=(target,), kwargs={"meeting_id": m["id"]})


def _tasks(member_id: str) -> None:
    tasks = [a for a in my_tasks(member_id) if a["work"] != "done"]
    with panel("tasks"):
        panel_head("나에게 배정된 업무", "확정된 회의 기준")
        if not tasks:
            st.caption("배정된 업무가 없어요.")
        box = st.container(key="scroll_tasks")
        for a in tasks:
            n = dday(a["due"])
            kind = "neutral" if n is None else "err" if n < 0 else "wait" if n <= 2 else "neutral"
            with box, tap(f"li_task_{a['meeting']['id']}_{a['id']}", router.go, ("review",), {"meeting_id": a["meeting"]["id"], "focus": a["id"]}, label=a["title"]):
                html(f'<div style="display:grid;grid-template-columns:52px minmax(0,1fr) 16px;gap:8px;align-items:center">{badge(dday_label(n), kind)}'
                     f'<div><div style="font-size:14px">{esc(a["title"])}</div><div class="m-sub">{esc(a["meeting"]["title"])} · {WORK_LABEL[a["work"]]}</div></div>'
                     '<span class="muted">›</span></div>')


def render() -> None:
    me = auth.me()
    mine = [m for m in meetings() if m["organizer"] == me["id"]]
    reviews = [m for m in mine if m["status"] == "REVIEW_PENDING"]

    left, actions = head_row()
    with left:
        page_head(f"안녕하세요, {me['name']} 님", f"검토를 기다리는 회의가 {len(reviews)}건 있어요." if reviews else "검토를 기다리는 회의가 없어요.")
    with actions:
        st.button("회의 목록", on_click=router.go, args=("meetings",))
        st.button("＋ 새 회의 등록", type="primary", on_click=router.go, args=("register",))

    _search_box()
    _status_cells(mine)   # ← render 안에서 딱 한 번만
    _insights()
    left, right = st.container(key="eq_dash").columns([2.6, 1.4], gap="medium")
    with left:
        _review_list(reviews)
        _recent()
    with right:
        _attention(mine)
        _tasks(me["id"])