"""검색 결과: 회의 결과(결정사항·업무·이슈·논의)를 찾고 회의 흐름을 보여줍니다."""
from __future__ import annotations

import streamlit as st

from core import auth, router
from core.state import meetings, members
from services.search import TYPE_LABEL, build_index, meeting_flow, search
from ui.components import badge, html, status_badge
from ui.layout import page_head, panel
from utils.dates import fmt_d
from utils.text import esc

TYPE_KIND = {"decision": "ok", "action": "run", "issue": "wait", "topic": "neutral"}


def _mark(text: str, q: str) -> str:
    return esc(text).replace(esc(q), f"<mark>{esc(q)}</mark>") if q else esc(text)


def _sync_query() -> None:
    q = st.session_state.get("sr_q", "").strip()
    router.go("search", q=q)


def render() -> None:
    q = router.arg("q", "")
    if st.session_state.get("_sr_route") != id(router.current()):
        st.session_state._sr_route = id(router.current())
        st.session_state.sr_q = q
        st.session_state.sr_kind = "all"

    page_head("검색 결과", "열람 권한이 있는 회의의 결정사항, 액션 아이템, 미해결 이슈, 논의 내용을 찾아요.", ["홈", "검색"])
    st.text_input("검색어", key="sr_q", placeholder="예) 본인인증, 요금제, A/B 테스트", label_visibility="collapsed", on_change=_sync_query)

    index = build_index([m for m in meetings() if auth.can_view(m)], members())
    all_hits = search(index, q)
    kinds = ["all"] + list(TYPE_LABEL)
    kind = st.segmented_control("유형", kinds, key="sr_kind", label_visibility="collapsed",
                                format_func=lambda k: f"{'전체' if k == 'all' else TYPE_LABEL[k]} {len(all_hits) if k == 'all' else sum(1 for h in all_hits if h['type'] == k)}") or "all"
    hits = [h for h in all_hits if kind == "all" or h["type"] == kind]

    if not q:
        st.info("검색어를 입력해 주세요.")
        return

    left, right = st.columns([3, 1.3], gap="medium")
    with left:
        with panel("search1"):
            st.markdown(f"**‘{esc(q)}’ 검색 결과 <span class='c-accent'>{len(hits)}</span>건**", unsafe_allow_html=True)
            if not hits:
                st.caption("검색 결과가 없어요. 다른 단어로 찾아보세요.")
            for i, h in enumerate(hits):
                m = h["meeting"]
                c1, c2 = st.container(key=f"tr_sr_{i}").columns([5, 1], vertical_alignment="center")
                with c1:
                    sub = f'<div class="small muted">{_mark(h["sub"], q)}</div>' if h["sub"] else ""
                    html(f'{badge(TYPE_LABEL[h["type"]], TYPE_KIND[h["type"]])} <span class="fw6">{_mark(h["text"], q)}</span>{sub}'
                         f'<div class="m-sub">{esc(m["title"])} · {fmt_d(m["date"], True)}</div>')
                c2.button("열기", key=f"sr_open_{i}", type="tertiary", on_click=router.go, args=("review",), kwargs={"meeting_id": m["id"]})
    with right:
        with panel("search2"):
            st.markdown("**회의 흐름**")
            st.caption("이 주제가 어떤 회의에서 이어졌는지 날짜순으로 보여줘요.")
            for m in meeting_flow(all_hits):
                html(f'<div class="row" style="align-items:flex-start"><span class="small muted" style="width:62px">{fmt_d(m["date"])}</span>'
                     f'<div class="grow"><div class="small fw6">{esc(m["title"])}</div>{status_badge(m["status"])}</div></div>')
