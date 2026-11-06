"""앱 상태 저장소.

화면에서 바뀌는 모든 값은 AppState 한 객체에 모아 st.session_state 에 보관합니다.
- 읽기: `state = get_state()` 후 속성 접근
- 쓰기: state/actions.py 의 함수만 사용 (화면 코드에서 직접 고치지 않기)
- 파생 값 계산: state/selectors.py
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields

import streamlit as st

from config import CURRENT_USER_ID
from data import repository as repo
from data.models import Meeting, Todo

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
    """결과 전달(SSE) 시뮬레이션 진행 정보 — 참석자에게 보내기까지 (외부 도구 전송은 ExportRun)."""

    meeting_id: str = "m1"
    attempt: int = 1  # 1: 첫 전송, 2: 실패 건 재시도
    started_at: float | None = None  # time.time()
    finished: bool = True


@dataclass
class ExportRun:
    """외부 도구(MCP) 전송: 결과 전달이 끝난 뒤 주관자가 도구를 골라 보냄."""

    meeting_id: str = "m1"
    picked: tuple[str, ...] = ()  # 고른 도구 id (보내기 전에는 고르는 중인 값)
    attempt: int = 1  # 1: 첫 전송, 2: 실패한 도구만 다시
    started_at: float | None = None  # None 이면 아직 도구를 고르는 중
    finished: bool = False
    fids: tuple[str, ...] = ()  # 보내기 시작할 때 남아 있던 전송 실패 id (결과 고정용)
    expired: tuple[str, ...] = ()  # 보내기 시작할 때 인증이 만료돼 있던 도구 (기술 이슈로 실패)
    contact_shown: bool = False  # 기술 이슈로 끝나 「관리자에게 문의」 모달을 띄웠는지


@dataclass
class AppState:
    # --- 인증
    logged_in: bool = False
    logged_out: bool = False  # 직접 로그아웃함 (쿠키 자동 로그인 막기)
    cookie_written: bool = False
    login_error: bool = False
    is_admin: bool = False  # 관리자 계정으로 로그인함 → 관리자 화면(비용 · 구성원 · MCP)만 보임

    # --- 라우팅
    screen: str = "compose"  # compose | archive | search | folder | note | deliver | sent | recv | adm_cost | adm_members | adm_mcp
    not_found: str = ""  # 없는 주소로 들어왔으면 그 주소 → 404 화면 (views/not_found.py)
    url_checked: bool = False  # 처음 들어온 주소를 확인했는지
    pending_route: str | None = None  # 콜백이 예약한 이동 (state/router.py)
    nav_by_action: bool = False
    last_route: str | None = None
    toast: str = ""  # 오른쪽 위에 잠깐 떴다 사라지는 알림 (st.toast) · 한 번 띄우면 비움
    read_notifications: set[str] = field(default_factory=set)
    bell_seen: set[str] = field(default_factory=set)  # 알림 아이콘을 눌러 확인한 알림 (빨간 점 끄기용)
    # 사람 고르기 (조직도): 모달마다 펼친 본부 · 부 · 팀, 고른 탭 / 최근 고른 사람 · 즐겨찾기 (모든 모달 공용)
    pp_open: dict[str, set[str]] = field(default_factory=dict)
    pp_tab: dict[str, str] = field(default_factory=dict)
    pp_recent: list[str] = field(default_factory=list)
    pp_fav: set[str] = field(default_factory=set)
    pp_nonce: int = 0  # 모달을 닫을 때마다 바뀌어 검색칸을 비움
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
    added_agendas: dict[str, list[str]] = field(default_factory=dict)  # 편집 중 「+ 안건 추가」로 넣은 안건 (회의 id → 제목들)
    agenda_backup: list[str] = field(default_factory=list)  # 편집 시작 때의 추가 안건 (취소하면 되돌림)
    branch_requests: set[str] = field(default_factory=set)  # 주관자에게 안건 분리를 요청한 회의
    agenda_open: list[str] = field(default_factory=list)  # 요약 탭 「안건별 상세」에서 펼친 안건 ("mid:index")
    agenda_jump: str = ""  # 안건 목차를 눌러 스크롤해 갈 안건 앵커 id (한 번 스크롤하면 비움)
    agenda_jump_n: int = 0
    done: dict[str, bool] = field(default_factory=dict)  # "mid:index"
    assigned: dict[str, list[str]] = field(default_factory=dict)  # "mid:index" → 담당자 이름들 (여러 명)
    status_override: dict[str, str] = field(default_factory=dict)
    added_todos: dict[str, list[Todo]] = field(default_factory=dict)  # 주관자가 직접 추가한 액션 아이템 (회의 id → 할 일들)
    deleted_todos: dict[str, list[int]] = field(default_factory=dict)  # 삭제한 액션 아이템 (회의 id → 원래 순서 번호 · 되돌리기 가능)
    todo_pending: dict = field(default_factory=dict)  # 추가 직후 원문을 다시 훑는 중인 액션 아이템 (로딩 화면)
    todo_nonce: int = 0  # 「액션 아이템 추가」 모달을 열 때마다 바뀌어 입력칸을 비움
    todo_draft_text: str = ""  # 추가 모달에 적은 내용 (담당자 고르기 창에 다녀와도 남도록)
    todo_draft_owners: list[str] = field(default_factory=list)  # 추가 모달에서 고른 담당자들 (사람 id · 비어 있으면 담당 미정)
    todo_draft_due: object = None  # 추가 모달에서 고른 마감일 (datetime.date · 없으면 None)
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
    assign_prev: dict[str, list[str]] = field(default_factory=dict)  # 마지막으로 바꾸기 전 담당자 ("누구 → 누구" 안내용)
    assign_draft: list[str] = field(default_factory=list)  # 담당자 모달에서 고르는 중인 이름들 (「맡기기」를 눌러야 반영)
    assign_team: str = ""  # 담당자 지정 · 팀에서 직접 고르기 (빈 값이면 추천 1순위의 팀)

    # --- 폴더 멤버 추가 (팀 → 팀원)
    member_team: str = ""
    member_nonce: int = 0  # 추가할 때마다 바뀌어 선택값을 비움
    member_just_added: bool = False  # 방금 추가함 → 멤버 목록을 맨 아래로 스크롤
    last_assign: str = ""

    # --- 결과 전달
    delivery: Delivery = field(default_factory=Delivery)
    export: ExportRun = field(default_factory=ExportRun)

    # --- 위키
    folder_wiki_node: str = "c"

    # --- 외부 전송 (연결은 관리자 · MCP 관리에서)
    export_resent: set[str] = field(default_factory=set)  # 다시 보낸 전송 실패 기록 id (f1 · f2)
    contact_failure: str = ""  # 「관리자에게 문의」 모달에서 보여 주는 전송 실패 기록 id

    # --- 관리자
    live_seen: int = -1  # 이미 본 새 알림 수 (늘어나면 화면을 새로 그려 알림 · 상태를 반영)
    adm_month: str = field(  # 비용 관리에서 보는 달 (처음엔 이번 달)
        default_factory=lambda: next(m["key"] for m in repo.admin()["months"] if m.get("running")))
    adm_query: str = ""  # 구성원 검색어
    adm_team: str = ""  # 구성원 팀 필터 (빈 값이면 전체)
    adm_adding: bool = False  # 구성원 관리 · 「구성원 추가」 입력 줄이 열려 있는지
    adm_invite_error: str = ""
    adm_recent_jobs: list[str] = field(default_factory=lambda: ["개발자", "디자이너", "PM"])  # 「최근 사용」 직무
    # 조직 선택 모달 (구성원 추가 · 팀)
    adm_org_target: str = "add"  # 고른 팀을 넣을 곳: add(구성원 추가 줄) | 사람 id
    adm_org_pick: str = ""  # 고른 팀
    adm_org_query: str = ""  # 조직명 검색어
    adm_org_tab: str = "all"  # recent | fav | all
    adm_org_open: set[str] = field(default_factory=set)  # 펼친 본부 · 부
    adm_org_recent: list[str] = field(default_factory=lambda: ["UX디자인팀", "결제개발팀"])  # 최근 고른 팀
    adm_org_fav: set[str] = field(default_factory=lambda: {"서비스기획팀"})  # 즐겨찾기 팀
    adm_org_nonce: int = 0
    adm_team_pending: str = ""  # 조직 선택 모달에서 고른 팀 → 구성원 추가 줄 팀 칸에 넣을 값
    org_seen: int = -1  # 마지막으로 반영한 조직 버전 (관리자가 바꾸면 화면을 새로 그림)
    adm_nonce: int = 0  # 구성원 추가 줄을 열 때마다 바뀌어 입력칸을 비움

    # --- 새 회의록 작성 화면
    nn_type: str = "weekly"
    nn_topic: str = "rel"
    nn_name: str = ""
    nn_group: str = "project"  # 폴더를 고른 보기: project(프로젝트별) | period(주차별·월별·일별)
    nn_period: str = ""  # 기간 폴더 키 (w1026 · m10 · d1023) · 아직 없는 기간이면 새로 만들어질 폴더
    nn_error: str = ""  # 작성 화면 오류 안내 (빈 값이면 없음)
    # 3단계 작성: 1 회의 내용 입력 → 2 발화자 확인 → 3 분석 및 결과
    cp_step: int = 1
    cp_text_rev: int = 0  # 예시를 넣을 때마다 바뀌어 입력칸을 새로 그림
    nn_title: str = ""
    nn_date: object = None  # datetime.date
    nn_text: str = ""  # 직접 쓴 회의 내용
    nn_files: list[tuple[str, str | None]] = field(default_factory=list)  # 첨부 파일 (이름, 내용 · 못 읽으면 None)
    nn_speaker_map: dict[str, str] = field(default_factory=dict)  # 발화자 표기(참석자1 · A) → 사람 id ('' 미지정)
    cp_example: str = "plain"  # 고른 입력 형식 (버튼 주황 테두리 · 입력칸 회색 예시) — 처음엔 발화자 없는 텍스트
    preview_meeting: Meeting | None = None  # 3단계 「분석 및 결과」에서 보여 주는 미리보기 회의록
    pick_label: str = ""  # 발화자 연결 모달에서 다루는 발화자 표기 (참석자1 · A)
    new_meetings: dict[str, Meeting] = field(default_factory=dict)  # 「회의록 생성 완료」로 만든 회의
    created_notes: list[tuple[str, str]] = field(default_factory=list)  # (폴더 id, 회의 id)
    nn_host: str = CURRENT_USER_ID  # 주관자 (기본: 나)
    nn_people: list[str] = field(default_factory=list)  # 참석자
    # 작성 화면의 선택 모달(폴더 · 주관자 · 참석자): 「선택」을 누르기 전까지는 여기 임시로 담아 둠
    pick_team: str = ""  # 보고 있는 팀 이름
    pick_draft: list[str] = field(default_factory=list)  # 고른 사람 (주관자는 1명)
    pick_folder: str = ""  # 고른 폴더 id 또는 "new"
    pick_name: str = ""  # 새 폴더 이름
    pick_type: str = "weekly"  # 폴더 모달에서 고른 회의 종류
    pick_group: str = "project"  # 폴더 모달에서 고른 보기
    pick_period: str = ""  # 폴더 모달에서 고른 기간 폴더 키 또는 "new"
    pick_query: str = ""  # 폴더 모달 검색어
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
    archive_query: str = ""  # (예전) 보관함 검색어
    search_query: str = ""  # 검색 결과 페이지의 검색어
    search_tab: str = ""  # 회의명 | 폴더명 | 구성원 | 회의 내용 (빈 값이면 결과가 있는 첫 버튼)
    search_nonce: int = 0  # 검색 페이지로 새로 들어올 때마다 바뀜 (입력칸 새로 · 커서 맞추기)
    archive_qnonce: int = 0  # 화면을 옮길 때마다 바뀌어 검색칸을 비움
    archive_owner: str = "all"  # 기간별 목록의 전체 / 내가 만든 폴더 / 멤버 (기간 폴더 화면까지 이어짐)
    period_wiki_folder: str = ""  # 기간 폴더 화면 · 위키 탭에서 보고 있는 프로젝트 폴더
    todo_group: str = ""  # 회의 목록 판넬: left(할 일 남음) | done(다 끝냄) | none(할당 없음) · 빈 값이면 회의가 있는 첫 판넬
    archive_role: str = "all"  # 기간별 목록의 전체 / 내가 주관 / 참석
    tree_open: set[str] = field(default_factory=lambda: {"root"})  # 사이드바에서 펼친 노드


def get_state() -> AppState:
    if _KEY not in st.session_state:
        st.session_state[_KEY] = AppState()
    state = st.session_state[_KEY]
    _fill_missing(state)
    return state


def _fill_missing(state: AppState) -> None:
    """코드에 새 필드가 생기기 전에 만든 세션 상태(앱을 띄운 채 코드를 고친 경우)에 빠진 필드를 기본값으로 채움."""
    for f in fields(AppState):
        if not hasattr(state, f.name):
            default = f.default_factory() if f.default_factory is not MISSING else f.default
            setattr(state, f.name, default)


def reset_state(**overrides) -> AppState:
    st.session_state[_KEY] = AppState(**overrides)
    return st.session_state[_KEY]
