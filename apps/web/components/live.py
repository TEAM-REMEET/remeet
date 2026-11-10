"""새 알림 지켜보기 (데모용 서버 공용 저장소 → 이 화면).

다른 계정이 만든 알림을 몇 초마다 확인합니다.
- 관리자: 사용자의 결과 전달이 기술 이슈로 실패함
- 사용자: 관리자가 실패한 전송을 다시 보내 「전달 완료」가 됨
- 모두: 관리자가 구성원(팀 · 직무 · 추가 · 권한 · 상태)을 바꿈 → 사람 고르기 화면에 바로 반영 (토스트 없이)
- 사용자: 새 회의록 분석(요약 · 액션 아이템)이 다른 화면에 있는 동안 끝남 → 사이드바 배지 · 토스트
새 알림이 오면 전체 화면을 한 번 다시 그려 알림 아이콘 · 진행 상태를 바로 반영하고, 토스트로 알려 줍니다.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state.store import get_state

_INTERVAL = 3  # 초


def _audience() -> str:
    return "admin" if get_state().is_admin else "user"


def _check_analysis(s) -> None:
    """새 회의록 분석이 뒤에서 도는 중이고 다른 화면에 있으면 끝났는지 확인 → 끝나면 전체를 다시 그려 배지를 바꿈."""
    if not s.cp_job_running or s.screen == "compose":
        return
    from state import actions  # actions 가 이 파일을 쓰는 화면들을 불러서 함수 안에서

    actions.compose_poll()
    if s.cp_job_running:
        return
    if not s.toast:  # 주관자에게 넘긴 경우는 넘겼다는 토스트가 이미 있음
        s.toast = ("회의 분석이 끝났어요. 「새 회의록」에서 검토한 뒤 확정해 주세요." if s.preview_meeting is not None
                   else "회의 분석에 실패했어요. 「새 회의록」에서 이유를 확인해 주세요.")
    st.rerun(scope="app")


@st.fragment(run_every=_INTERVAL)
def _watch() -> None:
    s = get_state()
    _check_analysis(s)
    if repo.org_version() != s.org_seen:  # 관리자가 구성원(팀 · 직무 · 추가 등)을 바꿈 → 바로 반영
        s.org_seen = repo.org_version()
        st.rerun(scope="app")
    live = repo.live_notifications(_audience())
    if len(live) > s.live_seen:
        s.live_seen = len(live)
        s.toast = live[0].text
        st.rerun(scope="app")


def watch_notifications() -> None:
    s = get_state()
    if s.live_seen < 0:  # 처음 들어왔을 때 이미 있던 알림은 토스트 없이 알림 목록에만
        s.live_seen = len(repo.live_notifications(_audience()))
    s.org_seen = repo.org_version()  # 이번에 그린 화면은 지금 조직 기준
    _watch()
