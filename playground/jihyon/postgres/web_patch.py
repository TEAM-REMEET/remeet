"""web 코드를 한 줄도 고치지 않고 PostgreSQL 을 붙이는 곳.

web 의 화면 코드는 전부 `repo.meetings()` 처럼 data.repository 모듈에서 함수를 "그때그때" 꺼내 씁니다.
그래서 앱이 시작될 때 그 모듈의 함수를 PostgreSQL 버전으로 바꿔 끼우면(setattr) 화면은 그대로 DB 데이터를 씁니다.
(이런 방식을 몽키패치라고 해요. 실험용으로 좋고, web 작업이 끝나면 web 안으로 옮겨 정식으로 붙입니다 → README 9장)
"""

from __future__ import annotations

import paths  # noqa: F401

# PostgreSQL 이 대신하는 web/data/repository.py 함수들
REPLACED = ("_base_meetings", "base_folders", "meeting_types", "integrations", "notifications")


def apply() -> None:
    from data import repository as repo  # web/data/repository.py

    if getattr(repo, "_pg_patched", False):  # Streamlit 은 화면마다 스크립트를 다시 돌리니까 한 번만
        return

    import repository_pg as pg  # 여기서 graph_source 가 목업 함수를 먼저 잡아 둠 (바꿔 끼우기 전)

    for name in REPLACED:
        setattr(repo, name, getattr(pg, name))
    repo.clear_cache = pg.clear_cache

    # 쓰기: 알림을 열면 DB 에도 읽음 표시 (원래 함수는 그대로 실행)
    from state import actions

    original = actions.open_notification

    def open_notification(nid: str) -> None:
        original(nid)
        pg.mark_notification_read(nid)

    actions.open_notification = open_notification

    # 새로 들어온 사람의 첫 화면 상태(AppState)에 DB 값을 반영
    from state import store

    original_state = store.AppState

    def get_state():
        import streamlit as st

        if store._KEY not in st.session_state:
            st.session_state[store._KEY] = original_state(done=pg.todo_done_map())
        return st.session_state[store._KEY]

    store.get_state = get_state
    repo._pg_patched = True
