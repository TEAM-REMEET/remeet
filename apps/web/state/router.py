"""URL 라우팅.

화면마다 고유한 URL 경로를 둡니다. (Streamlit 은 경로에 '/' 를 못 쓰고, 쿼리 파라미터는 브라우저
뒤로가기 기록과 잘 맞지 않아서 회의·폴더 id 를 경로 안에 넣습니다.)

  /                              새 회의록 (로그인 후 첫 화면)
  /meetings                      회의록 (월간·주간·일반 고르기)
  /meetings-weekly               회의 종류 (weekly | monthly | normal)의 회의 목록
  /search                        검색 결과 (회의록 첫 화면 검색칸에서)
  /folder-rel                    프로젝트
  /meeting-m1                    회의 상세
  /finalize-m1                   회의록 확정 (담당자 추천 → 최종 회의록 → 결과 전달로)
  /deliver-m1                    결과 전달 (참석자에게 보내기)
  /export-m1                     외부 도구(MCP) 전송 (결과 전달 뒤 도구를 골라 보내기)
  /shared-sent · /shared-received
  /ask                           회의에 물어보기 (온보딩 에이전트)
  /admin-cost · /admin-members · /admin-mcp   관리자 화면 (관리자 계정만)

흐름
  1) 버튼 콜백(actions.go)이 request(screen) 로 '이동 예약'
  2) app.py 가 실행 초반에 apply_pending() → st.switch_page() 로 URL 변경 (브라우저 기록에 남음)
  3) 매 실행마다 sync_from_url() 로 URL → 상태 반영 (브라우저 뒤로가기·앞으로가기도 여기서 처리)
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse
from dataclasses import dataclass

import streamlit as st

from data import repository as repo
from state import archive
from state import selectors as sel
from state.store import AppState, Delivery, ExportRun, get_state
from utils.browser import hide_page_not_found

# 고정 경로 화면: screen → (url_path, 탭 제목)
STATIC = {
    "home": ("home", "홈"),
    "compose": ("", "새 회의록"),
    "search": ("search", "검색 결과"),
    "sent": ("shared-sent", "공유한 회의"),
    "recv": ("shared-received", "공유 받은 회의"),
    "ask": ("ask", "회의에 물어보기"),
    "adm_cost": ("admin-cost", "비용 관리"),
    "adm_members": ("admin-members", "구성원 관리"),
    "adm_mcp": ("admin-mcp", "MCP 관리"),
}

# 화면이 바뀌면 지우는 일시적인 값
_TRANSIENT = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="")


@dataclass(frozen=True)
class Route:
    screen: str
    path: str
    title: str
    param: tuple[str, ...] = ()  # 회의 id / 프로젝트 id / (회의 종류,)


def _archive_path(t: str = "") -> str:
    return f"meetings-{t}" if t else "meetings"


def _archive_routes(state: AppState) -> list[Route]:
    types = repo.meeting_types()
    routes = [Route("archive", _archive_path(), "회의록")]
    for t in sel.TYPE_ORDER:
        name = types[t].name
        routes.append(Route("archive", _archive_path(t), name, (t,)))
    return routes


def all_routes(state: AppState) -> list[Route]:
    routes = [Route(screen, path, title) for screen, (path, title) in STATIC.items()]
    routes += _archive_routes(state)
    routes += [Route("folder", f"folder-{fid}", f.name, (fid,)) for fid, f in sel.folders(state).items()]
    for mid, m in repo.meetings().items():
        routes.append(Route("note", f"meeting-{mid}", m.title, (mid,)))
        routes.append(Route("finalize", f"finalize-{mid}", f"회의록 확정 · {m.title}", (mid,)))
        routes.append(Route("deliver", f"deliver-{mid}", f"결과 전달 · {m.title}", (mid,)))
        routes.append(Route("export", f"export-{mid}", f"외부 도구 전송 · {m.title}", (mid,)))
    return routes


def path_for(state: AppState, screen: str) -> str:
    """현재 상태 기준으로 screen 의 URL 경로."""
    if screen == "archive":
        return _archive_path(state.archive_type)
    if screen == "folder":
        return f"folder-{state.folder_id}"
    if screen == "note":
        return f"meeting-{state.meeting_id}"
    if screen == "finalize":
        return f"finalize-{state.meeting_id}"
    if screen == "deliver":
        return f"deliver-{state.delivery.meeting_id}"
    if screen == "export":
        return f"export-{state.export.meeting_id}"
    return STATIC.get(screen, STATIC["compose"])[0]


def build_pages(renderers: dict[str, Callable[[], None]]) -> dict[str, st.Page]:
    """url_path → st.Page. renderers: screen → 렌더 함수"""
    pages = {}
    for r in all_routes(get_state()):
        pages[r.path] = st.Page(renderers[r.screen], title=r.title, url_path=r.path or None,
                                default=(r.screen == "compose"))
    return pages


def check_not_found(pages: dict[str, st.Page]) -> None:
    """세션을 시작할 때 한 번: 들어온 주소가 우리 화면이 아니면 not_found 에 그 주소를 적어 둠.

    Streamlit 은 모르는 주소면 첫 화면으로 바꿔 보여 주면서 「Page not found」 창을 띄우는데,
    그 창은 숨기고(utils/browser.hide_page_not_found) 우리 404 화면을 보여 줍니다.
    """
    s = get_state()
    if s.url_checked:
        return
    s.url_checked = True
    path = urlparse(st.context.url or "").path.strip("/")
    if path and path not in pages:
        s.not_found = "/" + path
        hide_page_not_found()


def request(screen: str) -> None:
    """콜백 안에서 호출: 다음 실행 때 해당 화면으로 URL 이동."""
    s = get_state()
    s.pending_route = screen
    s.nav_by_action = True


def apply_pending(pages: dict[str, st.Page]) -> None:
    """예약된 이동이 있으면 URL 을 바꾸며 이동 (이 함수 이후 코드는 실행되지 않음)."""
    s = get_state()
    screen = s.pending_route
    if not screen:
        return
    s.pending_route = None
    path = path_for(s, screen)
    if path in pages:
        st.switch_page(pages[path])


def sync_from_url(current_path: str) -> str:
    """현재 URL → 상태. 화면 이름을 돌려줍니다."""
    s = get_state()
    route = next((r for r in all_routes(s) if r.path == current_path), Route("compose", "", "새 회의록"))

    if route.screen == "archive":
        s.archive_type = route.param[0] if route.param else ""
    elif route.screen == "folder":
        s.folder_id = route.param[0]
    elif route.screen == "note":
        s.meeting_id = route.param[0]
    elif route.screen == "finalize":
        s.meeting_id = route.param[0]
        if s.fin_flow != s.meeting_id:  # 주소로 바로 들어옴 → 1단계부터
            s.fin_flow, s.fin_step = s.meeting_id, 1
    elif route.screen == "deliver" and route.param[0] != s.delivery.meeting_id:
        s.delivery = Delivery(meeting_id=route.param[0])  # 지난 전달 결과 보기
    elif route.screen == "export" and route.param[0] != s.export.meeting_id:
        from state import export

        s.export = ExportRun(meeting_id=route.param[0], picked=export.default_pick(route.param[0]))  # 도구 고르기부터

    s.screen = route.screen
    if route.path != s.last_route:
        # 브라우저 뒤로가기/앞으로가기로 바뀐 경우에만 일시적인 값을 정리
        # (버튼으로 이동한 경우는 actions 에서 이미 필요한 값을 정해 둠)
        if s.last_route is not None and not s.nav_by_action:
            for k, v in _TRANSIENT.items():
                setattr(s, k, list(v) if isinstance(v, list) else v)
        if s.last_route is not None:
            s.not_found = ""  # 다른 화면으로 옮기면 404 끝
        s.last_route = route.path
        # 사이드바 트리: 지금 화면까지 가는 폴더들을 펼침 (사용자가 접은 건 다음 이동 전까지 그대로)
        node, ancestors = archive.node_of_location(s)
        s.tree_open |= set(ancestors) | ({node} if node else set())
    s.nav_by_action = False
    return route.screen


def guard(current_path: str) -> None:
    """계정별로 볼 수 있는 화면만: 관리자는 관리자 화면만, 사용자는 관리자 화면 X(404)."""
    s = get_state()
    on_admin = s.screen in sel.ADMIN_SCREENS
    if s.is_admin and not on_admin and not s.not_found:
        request("adm_cost")
        st.rerun()
    elif not s.is_admin and on_admin:
        s.not_found = "/" + current_path
