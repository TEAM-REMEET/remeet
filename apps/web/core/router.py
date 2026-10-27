"""URL 기반 화면 이동.

화면마다 주소가 있어요:  /dashboard,  /review?meeting_id=mt-onb,  /delivery?meeting_id=mt-sec&mode=more
- 버튼 on_click 콜백에서는 go()만 호출하면 되고, 본문(또는 fragment)에서 이동할 때는 go_now()를 씁니다.
- 실제 이동은 app.py가 다음 실행 때 st.switch_page()로 처리해서 브라우저 기록에 남습니다.
  그래서 브라우저 ‘뒤로 가기’를 누르면 이전 화면(과 그때의 주소 인자)으로 돌아가요.
"""
from __future__ import annotations

import streamlit as st

# 로그인 후 상단 메뉴 (page 키, 라벨)
APP_NAV = [("dashboard", "대시보드"), ("meetings", "회의"), ("members", "구성원"), ("integrations", "연동 설정")]
# 메뉴에 없는 화면이 어느 메뉴에 속하는지
NAV_GROUP = {"register": "meetings", "progress": "meetings", "review": "meetings", "delivery": "meetings", "search": "dashboard"}

_PENDING = "_route_pending"   # 다음 실행 때 이동할 곳 (page, args)
_ROUTE = "_route"             # 지금 화면 {"page", "args"}  ← 같은 화면이면 같은 객체를 돌려줘요
_SIG = "_route_sig"
_LAST = "_route_last_args"    # 화면별 마지막 주소 인자 (뒤로 가기 보정용)

# 주소 인자(meeting_id 등)가 꼭 있어야 하는 화면
NEEDS_ARGS = {"review", "progress", "delivery", "search"}


# ---------------------------------------------------------------- 지금 화면 (app.py가 매 실행마다 갱신)

def sync(page_name: str) -> None:
    """현재 URL(페이지 + 쿼리)을 읽어 route를 맞춥니다. 화면이 바뀐 경우에만 새 객체를 만들어요."""
    args = {k: st.query_params[k] for k in st.query_params}
    last = st.session_state.setdefault(_LAST, {})
    if args:
        last[page_name] = args
    elif page_name in NEEDS_ARGS and page_name in last:
        # Streamlit은 화면을 바꿀 때 ‘이전 주소 + 쿼리 지움’ 기록을 한 번 더 남겨요.
        # 뒤로 가기로 그 기록에 오면 이 화면의 마지막 인자로 같은 내용을 보여줍니다.
        args = last[page_name]
    sig = (page_name, tuple(sorted(args.items())))
    if st.session_state.get(_SIG) != sig:
        st.session_state[_SIG] = sig
        st.session_state[_ROUTE] = {"page": page_name, "args": args}


def current() -> dict:
    return st.session_state.get(_ROUTE) or {"page": "main", "args": {}}


def page() -> str:
    return current()["page"]


def arg(name: str, default=None):
    return current()["args"].get(name, default)


# ---------------------------------------------------------------- 이동

def go(page_name: str, **args) -> None:
    """콜백에서 호출: 다음 실행 때 해당 주소로 이동합니다."""
    st.session_state[_PENDING] = (page_name, {k: str(v) for k, v in args.items() if v is not None})


def go_now(page_name: str, **args) -> None:
    """본문·fragment에서 호출: 바로 이동합니다."""
    go(page_name, **args)
    st.rerun()


def pending() -> tuple[str, dict] | None:
    return st.session_state.pop(_PENDING, None)


def open_meeting(meeting: dict) -> None:
    """회의 상태에 맞는 화면으로 이동."""
    if meeting["status"] in ("PROCESSING", "FAILED"):
        go("progress", meeting_id=meeting["id"])
    else:
        go("review", meeting_id=meeting["id"])
