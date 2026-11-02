# """URL 라우팅.

# 화면마다 고유한 URL 경로를 둡니다. (Streamlit 은 경로에 '/' 를 못 쓰고, 쿼리 파라미터는 브라우저
# 뒤로가기 기록과 잘 맞지 않아서 회의·폴더 id 를 경로 안에 넣습니다.)

#   /                       내 회의
#   /meetings-weekly        회의 종류 (weekly | monthly | normal)
#   /folder-rel             폴더
#   /meeting-m1             회의 상세
#   /deliver-m1             결과 전달
#   /wiki · /shared-sent · /shared-received · /integrations

# 흐름
#   1) 버튼 콜백(actions.go)이 request(screen) 로 '이동 예약'
#   2) app.py 가 실행 초반에 apply_pending() → st.switch_page() 로 URL 변경 (브라우저 기록에 남음)
#   3) 매 실행마다 sync_from_url() 로 URL → 상태 반영 (브라우저 뒤로가기·앞으로가기도 여기서 처리)
# """

# from __future__ import annotations

# from collections.abc import Callable
# from dataclasses import dataclass

# import streamlit as st

# from data import repository as repo
# from state import selectors as sel
# from state.store import AppState, Delivery, get_state

# # 고정 경로 화면: screen → (url_path, 탭 제목)
# STATIC = {
#     "home": ("", "내 회의"),
#     "wiki": ("wiki", "내 위키"),
#     "sent": ("shared-sent", "공유한 회의"),
#     "recv": ("shared-received", "공유 받은 회의"),
#     "mcp": ("integrations", "외부 연동"),
# }

# # 화면이 바뀌면 지우는 일시적인 값
# _TRANSIENT = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="")


# @dataclass(frozen=True)
# class Route:
#     screen: str
#     path: str
#     title: str
#     param: str = ""  # 회의 id / 폴더 id / 회의 종류


# def all_routes(state: AppState) -> list[Route]:
#     routes = [Route(screen, path, title) for screen, (path, title) in STATIC.items()]
#     types = repo.meeting_types()
#     routes += [Route("folder", f"meetings-{t}", types[t].name, t) for t in sel.TYPE_ORDER]
#     routes += [Route("folder", f"folder-{fid}", f.name, fid) for fid, f in sel.folders(state).items()]
#     for mid, m in repo.meetings().items():
#         routes.append(Route("note", f"meeting-{mid}", m.title, mid))
#         routes.append(Route("deliver", f"deliver-{mid}", f"결과 전달 · {m.title}", mid))
#     return routes


# def path_for(state: AppState, screen: str) -> str:
#     """현재 상태 기준으로 screen 의 URL 경로."""
#     if screen == "folder":
#         return f"folder-{state.folder_id}" if state.folder_id != "all" else f"meetings-{state.folder_type}"
#     if screen == "note":
#         return f"meeting-{state.meeting_id}"
#     if screen == "deliver":
#         return f"deliver-{state.delivery.meeting_id}"
#     return STATIC.get(screen, STATIC["home"])[0]


# def build_pages(renderers: dict[str, Callable[[], None]]) -> dict[str, st.Page]:
#     """url_path → st.Page. renderers: screen → 렌더 함수"""
#     pages = {}
#     for r in all_routes(get_state()):
#         pages[r.path] = st.Page(renderers[r.screen], title=r.title, url_path=r.path or None,
#                                 default=(r.screen == "home"))
#     return pages


# def request(screen: str) -> None:
#     """콜백 안에서 호출: 다음 실행 때 해당 화면으로 URL 이동."""
#     s = get_state()
#     s.pending_route = screen
#     s.nav_by_action = True


# def apply_pending(pages: dict[str, st.Page]) -> None:
#     """예약된 이동이 있으면 URL 을 바꾸며 이동 (이 함수 이후 코드는 실행되지 않음)."""
#     s = get_state()
#     screen = s.pending_route
#     if not screen:
#         return
#     s.pending_route = None
#     path = path_for(s, screen)
#     if path in pages:
#         st.switch_page(pages[path])


# def sync_from_url(current_path: str) -> str:
#     """현재 URL → 상태. 화면 이름을 돌려줍니다."""
#     s = get_state()
#     route = next((r for r in all_routes(s) if r.path == current_path), Route("home", "", "내 회의"))

#     if route.screen == "folder":
#         if route.param in repo.meeting_types():
#             s.folder_type, s.folder_id = route.param, "all"
#         else:
#             s.folder_type, s.folder_id = sel.folders(s)[route.param].type, route.param
#     elif route.screen == "note":
#         s.meeting_id = route.param
#     elif route.screen == "deliver" and route.param != s.delivery.meeting_id:
#         s.delivery = Delivery(meeting_id=route.param)  # 지난 전달 결과 보기

#     if route.path != s.last_route:
#         # 브라우저 뒤로가기/앞으로가기로 바뀐 경우에만 일시적인 값을 정리
#         # (버튼으로 이동한 경우는 actions 에서 이미 필요한 값을 정해 둠)
#         if s.last_route is not None and not s.nav_by_action:
#             for k, v in _TRANSIENT.items():
#                 setattr(s, k, list(v) if isinstance(v, list) else v)
#         s.last_route = route.path
#     s.nav_by_action = False
#     s.screen = route.screen
#     return route.screen



"""URL 라우팅.

화면마다 고유한 URL 경로를 둡니다. (Streamlit 은 경로에 '/' 를 못 쓰고, 쿼리 파라미터는 브라우저
뒤로가기 기록과 잘 맞지 않아서 회의·폴더 id 를 경로 안에 넣습니다.)

  /                              새 회의록 (로그인 후 첫 화면)
  /meetings                      회의록 (월간·주간·일반 고르기)
  /meetings-weekly               회의 종류 (weekly | monthly | normal) → 주차별 / 프로젝트별 고르기
  /meetings-weekly-period        주차별(월별·일별) 묶음 목록
  /meetings-weekly-period-w1027  한 주(달·날)의 회의 목록
  /meetings-weekly-project       프로젝트(폴더) 목록
  /folder-rel                    폴더
  /meeting-m1                    회의 상세
  /deliver-m1                    결과 전달
  /shared-sent · /shared-received · /integrations

흐름
  1) 버튼 콜백(actions.go)이 request(screen) 로 '이동 예약'
  2) app.py 가 실행 초반에 apply_pending() → st.switch_page() 로 URL 변경 (브라우저 기록에 남음)
  3) 매 실행마다 sync_from_url() 로 URL → 상태 반영 (브라우저 뒤로가기·앞으로가기도 여기서 처리)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from data import repository as repo
from state import archive
from state import selectors as sel
from state.store import AppState, Delivery, get_state

# 고정 경로 화면: screen → (url_path, 탭 제목)
STATIC = {
    "compose": ("", "새 회의록"),
    "sent": ("shared-sent", "공유한 회의"),
    "recv": ("shared-received", "공유 받은 회의"),
    "mcp": ("integrations", "외부 연동"),
}

# 화면이 바뀌면 지우는 일시적인 값
_TRANSIENT = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="")


@dataclass(frozen=True)
class Route:
    screen: str
    path: str
    title: str
    param: tuple[str, ...] = ()  # 회의 id / 폴더 id / (회의 종류, 보기 방식, 기간)


def _archive_path(t: str = "", g: str = "", p: str = "") -> str:
    return "-".join(x for x in ("meetings", t, g, p) if x)


def _archive_routes(state: AppState) -> list[Route]:
    types = repo.meeting_types()
    routes = [Route("archive", _archive_path(), "회의록")]
    for t in sel.TYPE_ORDER:
        name = types[t].name
        routes.append(Route("archive", _archive_path(t), name, (t,)))
        for g in archive.GROUPS:
            routes.append(Route("archive", _archive_path(t, g), f"{name} · {archive.group_label(t, g)}", (t, g)))
        for pr in archive.periods(state, t):
            routes.append(Route("archive", _archive_path(t, "period", pr.key), f"{name} · {pr.label}", (t, "period", pr.key)))
    return routes


def all_routes(state: AppState) -> list[Route]:
    routes = [Route(screen, path, title) for screen, (path, title) in STATIC.items()]
    routes += _archive_routes(state)
    routes += [Route("folder", f"folder-{fid}", f.name, (fid,)) for fid, f in sel.folders(state).items()]
    for mid, m in repo.meetings().items():
        routes.append(Route("note", f"meeting-{mid}", m.title, (mid,)))
        routes.append(Route("deliver", f"deliver-{mid}", f"결과 전달 · {m.title}", (mid,)))
    return routes


def path_for(state: AppState, screen: str) -> str:
    """현재 상태 기준으로 screen 의 URL 경로."""
    if screen == "archive":
        t = state.archive_type
        g = state.archive_group if t else ""
        p = state.archive_period if g == "period" else ""
        return _archive_path(t, g, p)
    if screen == "folder":
        return f"folder-{state.folder_id}"
    if screen == "note":
        return f"meeting-{state.meeting_id}"
    if screen == "deliver":
        return f"deliver-{state.delivery.meeting_id}"
    return STATIC.get(screen, STATIC["compose"])[0]


def build_pages(renderers: dict[str, Callable[[], None]]) -> dict[str, st.Page]:
    """url_path → st.Page. renderers: screen → 렌더 함수"""
    pages = {}
    for r in all_routes(get_state()):
        pages[r.path] = st.Page(renderers[r.screen], title=r.title, url_path=r.path or None,
                                default=(r.screen == "compose"))
    return pages


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
        t, g, p = (*route.param, "", "", "")[:3]
        s.archive_type, s.archive_group, s.archive_period = t, g, p
    elif route.screen == "folder":
        s.folder_id = route.param[0]
        s.folder_type = sel.folders(s)[s.folder_id].type
    elif route.screen == "note":
        s.meeting_id = route.param[0]
    elif route.screen == "deliver" and route.param[0] != s.delivery.meeting_id:
        s.delivery = Delivery(meeting_id=route.param[0])  # 지난 전달 결과 보기

    s.screen = route.screen
    if route.path != s.last_route:
        # 브라우저 뒤로가기/앞으로가기로 바뀐 경우에만 일시적인 값을 정리
        # (버튼으로 이동한 경우는 actions 에서 이미 필요한 값을 정해 둠)
        if s.last_route is not None and not s.nav_by_action:
            for k, v in _TRANSIENT.items():
                setattr(s, k, list(v) if isinstance(v, list) else v)
        s.last_route = route.path
        # 사이드바 트리: 지금 화면까지 가는 폴더들을 펼침 (사용자가 접은 건 다음 이동 전까지 그대로)
        node, ancestors = archive.node_of_location(s)
        s.tree_open |= set(ancestors) | ({node} if node else set())
    s.nav_by_action = False
    return route.screen
