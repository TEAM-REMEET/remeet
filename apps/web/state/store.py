"""앱 상태 저장소.

화면에서 바뀌는 모든 값은 AppState 한 객체에 모아 st.session_state 에 보관합니다.
- 읽기: `state = get_state()` 후 속성 접근
- 쓰기: state/actions.py 의 함수만 사용 (화면 코드에서 직접 고치지 않기)
- 파생 값 계산: state/selectors.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

import streamlit as st

from config import CURRENT_USER_ID
from data import repository as repo

_KEY = "_app_state"


@dataclass
class SentShare:
    meeting_id: str
    to: str
    scope: str  # summary | full
    date: str


@dataclass
class NewFolder:
    id: str
    name: str
    type: str


@dataclass
class Delivery:
    """결과 전달(SSE) 시뮬레이션 진행 정보."""

    meeting_id: str = "m1"
    attempt: int = 1  # 1: 첫 전송, 2: 실패 건 재시도
    started_at: float | None = None  # time.time()
    finished: bool = True


@dataclass
class AppState:
    # --- 인증
    logged_in: bool = False
    logged_out: bool = False  # 직접 로그아웃함 (쿠키 자동 로그인 막기)
    cookie_written: bool = False
    login_error: bool = False

    # --- 라우팅
    screen: str = "compose"  # compose | archive | folder | note | deliver | sent | recv | mcp
    pending_route: str | None = None  # 콜백이 예약한 이동 (state/router.py)
    nav_by_action: bool = False
    last_route: str | None = None
    toast: str = ""
    read_notifications: set[str] = field(default_factory=set)
    bell_seen: set[str] = field(default_factory=set)  # 알림 아이콘을 눌러 확인한 알림 (빨간 점 끄기용)
    modal: str | None = None  # members | delete_folder | delete_meeting | people | assign | share

    # --- 홈
    home_role: str = "all"

    # --- 폴더
    folder_type: str = "weekly"
    folder_id: str = "all"  # all 이면 회의 종류 화면
    folder_owner_filter: str = "all"  # all | mine | member
    folder_tab: str = "all"  # all(회의록 목록) | wiki
    folder_role: str = "all"  # all | host | att
    show_brief: bool = False
    new_folders: list[NewFolder] = field(default_factory=list)
    deleted_folders: set[str] = field(default_factory=set)
    members_added: dict[str, list[str]] = field(default_factory=dict)
    members_removed: set[str] = field(default_factory=set)  # "folder:person"

    # --- 회의 상세
    meeting_id: str = "m1"
    insight_tab: str = "sum"  # sum | ag | todo | kw | rel
    left_tab: str = "raw"  # raw | ratio
    raw_query: str = ""
    highlight: list[str] = field(default_factory=list)
    highlight_key: str = ""
    todo_scope: str = "mine"
    editing: bool = False
    just_shared: bool = False
    branched: bool = False
    done: dict[str, bool] = field(default_factory=dict)  # "mid:index"
    assigned: dict[str, str] = field(default_factory=dict)  # "mid:index" → 이름
    status_override: dict[str, str] = field(default_factory=dict)
    copied: str = ""

    # --- 공유
    share_scope: str = "summary"
    share_nonce: int = 0  # 공유 모달을 열 때마다·사람을 추가할 때마다 바뀌어 선택값을 비움
    share_team: str = ""  # 공유 모달에서 보고 있는 팀
    share_recipients: list[str] = field(default_factory=list)  # 받는 사람 (사람 id, 추가한 순서)
    last_share_to: str = ""
    sent_shares: list[SentShare] = field(
        default_factory=lambda: [SentShare("m3", "재무팀", "summary", "10/21")]
    )

    # --- 담당자 지정
    assign_key: str = ""
    assign_text: str = ""
    assign_searching: bool = False
    assign_query: str = ""
    assign_only_free: bool = False
    assign_scope_all: bool = False
    assign_sort: str = "score"
    assign_pending: str = ""
    assign_reason: str = ""
    assign_team: str = ""  # 담당자 지정 · 팀에서 직접 고르기 (빈 값이면 추천 1순위의 팀)

    # --- 폴더 멤버 추가 (팀 → 팀원)
    member_team: str = ""
    member_nonce: int = 0  # 추가할 때마다 바뀌어 선택값을 비움
    member_just_added: bool = False  # 방금 추가함 → 멤버 목록을 맨 아래로 스크롤
    last_assign: str = ""

    # --- 결과 전달
    delivery: Delivery = field(default_factory=Delivery)

    # --- 위키
    wiki_node: str = "me"
    wiki_series: str = "all"
    folder_wiki_node: str = "c"

    # --- 외부 연동
    integrations: dict[str, str] = field(default_factory=lambda: dict(repo.integrations()["initial_status"]))
    ai_folders: dict[str, bool] = field(default_factory=lambda: dict(repo.integrations()["ai_default_folders"]))
    ai_toggles: dict[str, bool] = field(
        default_factory=lambda: {t.key: t.default for t in repo.integrations()["ai_toggles"]}
    )

    # --- 새 회의록 작성 화면
    nn_type: str = "weekly"
    nn_topic: str = "rel"
    nn_name: str = ""
    nn_error: str = ""  # 작성 화면 오류 안내 (빈 값이면 없음)
    nn_host: str = CURRENT_USER_ID  # 주관자 (기본: 나)
    nn_people: list[str] = field(default_factory=list)  # 참석자
    # 작성 화면의 선택 모달(폴더 · 주관자 · 참석자): 「선택」을 누르기 전까지는 여기 임시로 담아 둠
    pick_team: str = ""  # 보고 있는 팀 이름
    pick_draft: list[str] = field(default_factory=list)  # 고른 사람 (주관자는 1명)
    pick_folder: str = ""  # 고른 폴더 id 또는 "new"
    pick_name: str = ""  # 새 폴더 이름
    pick_nonce: int = 0  # 모달을 열 때마다 바뀌어 위젯 선택값을 새로 맞춤
    compose_nonce: int = 0  # 생성하기 후 입력칸을 비우기 위해 바뀜

    # --- 회의록 보관함 (사이드바 트리 · 회의록 페이지)
    archive_type: str = ""  # "" | weekly | monthly | normal
    archive_group: str = ""  # "" | period | project
    archive_period: str = ""  # 기간 키 (w1027 · m10 · d1023)
    # --- 모달 대상
    members_folder: str = ""  # 멤버 모달에서 다루는 폴더 (빈 값이면 folder_id)
    members_scope: list[str] = field(default_factory=list)  # 기간 폴더에서 연 경우: 고를 수 있는 프로젝트 폴더들
    delete_target: tuple[str, ...] = ()  # ("folder", fid) | ("period", 회의 종류, 기간 키) | ("meeting", mid)
    deleted_meetings: set[str] = field(default_factory=set)  # 주관자가 삭제한 회의

    # --- 미참석 공유: 회의 id → 주관자가 이 회의록을 보내 준 미참석(조퇴·불참) 멤버
    #     미참석한 회의는 원래 보이지 않고, 여기 들어 있을 때만 '미참석 · 공유받음'으로 보여요.
    #     (목업: 박도윤님이 10/22 회의를 빠진 나에게 공유해 둔 상태)
    absent_shares: dict[str, list[str]] = field(default_factory=lambda: {"m5": ["yun"]})
    archive_owner: str = "all"  # 기간별 목록의 전체 / 내가 만든 폴더 / 멤버 (기간 폴더 화면까지 이어짐)
    period_wiki_folder: str = ""  # 기간 폴더 화면 · 위키 탭에서 보고 있는 프로젝트 폴더
    todo_group: str = ""  # 회의 목록 판넬: left(할 일 남음) | done(다 끝냄) | none(할당 없음) · 빈 값이면 회의가 있는 첫 판넬
    archive_role: str = "all"  # 기간별 목록의 전체 / 내가 주관 / 참석
    tree_open: set[str] = field(default_factory=lambda: {"root"})  # 사이드바에서 펼친 노드


def get_state() -> AppState:
    if _KEY not in st.session_state:
        st.session_state[_KEY] = AppState()
    return st.session_state[_KEY]


def reset_state(**overrides) -> AppState:
    st.session_state[_KEY] = AppState(**overrides)
    return st.session_state[_KEY]
