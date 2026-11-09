# """목업 데이터 모델 (읽기 전용 도메인 객체).

# data/mock/*.json 을 이 데이터클래스로 변환해서 씁니다. 화면에서 바뀌는 값(완료 체크, 담당자 지정 등)은
# 여기 넣지 않고 state/store.py 의 AppState 에 둡니다.
# """

# from __future__ import annotations

# from dataclasses import dataclass, field


# @dataclass(frozen=True)
# class Person:
#     id: str
#     short: str  # 회의록 원문에 찍히는 이름 (예: 서윤)
#     name: str  # 전체 이름 (예: 이서윤)
#     team: str

#     @property
#     def initial(self) -> str:
#         return self.name[:1]


# @dataclass(frozen=True)
# class Paragraph:
#     id: str
#     speaker: str  # 빈 문자열이면 안건 제목 줄
#     text: str

#     @property
#     def is_heading(self) -> bool:
#         return self.speaker == ""


# @dataclass(frozen=True)
# class Evidence:
#     """요약·결정 등 한 문장과, 그 근거가 된 원문 문단 id 목록."""

#     text: str
#     src: tuple[str, ...] = ()


# @dataclass(frozen=True)
# class Discussion:
#     label: str
#     text: str
#     src: tuple[str, ...] = ()


# @dataclass(frozen=True)
# class Agenda:
#     title: str
#     status: str
#     tone: str  # ok | warn | hold | info
#     deep: bool
#     src: tuple[str, ...]
#     points: tuple[Evidence, ...]


# @dataclass(frozen=True)
# class Todo:
#     text: str
#     owner: str | None  # 사람 id, 없으면 담당자 미정
#     due: str
#     src: tuple[str, ...]
#     urgent: bool = False


# @dataclass(frozen=True)
# class ShareInfo:
#     sender: str
#     scope: str  # summary | full
#     received: str
#     lock_raw: bool


# @dataclass(frozen=True)
# class Meeting:
#     id: str
#     title: str
#     when: str
#     date: str  # M/D
#     topic: str | None
#     host: str
#     people: tuple[str, ...]
#     deep: bool
#     time: str
#     file: str
#     initial_status: str
#     paragraphs: tuple[Paragraph, ...]
#     summary: Evidence
#     discussion: tuple[Discussion, ...] | None
#     keywords: tuple[str, ...]
#     decisions: tuple[Evidence, ...]
#     open_issues: tuple[Evidence, ...]
#     agendas: tuple[Agenda, ...]
#     todos: tuple[Todo, ...]
#     shared: ShareInfo | None = None
#     branch_of: str | None = None  # 안건 분리로 생긴 회의라면 원래 회의 id

#     @property
#     def weekday(self) -> str:
#         if "(" in self.when and ")" in self.when:
#             return self.when.split("(")[1][:1]
#         return "월"

#     def paragraph(self, pid: str) -> Paragraph | None:
#         return next((p for p in self.paragraphs if p.id == pid), None)


# @dataclass(frozen=True)
# class MeetingType:
#     id: str
#     name: str
#     meta: str
#     hidden_count: int  # 멤버가 아니라서 안 보이는 폴더 수


# @dataclass(frozen=True)
# class BriefItem:
#     label: str
#     text: str


# @dataclass(frozen=True)
# class Folder:
#     id: str
#     name: str
#     type: str
#     host: str
#     members: tuple[str, ...]
#     notes: tuple[str, ...]
#     brief: tuple[BriefItem, ...] = ()
#     is_new: bool = False


# @dataclass(frozen=True)
# class Candidate:
#     id: str
#     name: str
#     team: str
#     role: str
#     score: int
#     skill: int
#     dept: int
#     free: int
#     tasks: int
#     att: bool
#     skills: tuple[str, ...]
#     match: str = ""


# @dataclass(frozen=True)
# class GraphNode:
#     id: str
#     label: str
#     kind: str  # me | project | topic | decision | person
#     x: float
#     y: float
#     desc: str
#     initial: str = ""
#     person: str | None = None


# @dataclass(frozen=True)
# class GraphEdge:
#     a: str
#     b: str
#     label: str
#     weight: float


# @dataclass(frozen=True)
# class TimelineItem:
#     when: str
#     text: str
#     meeting: str
#     src: tuple[str, ...]


# @dataclass(frozen=True)
# class KnowledgeGraph:
#     nodes: dict[str, GraphNode]
#     edges: tuple[GraphEdge, ...]
#     timeline: dict[str, tuple[TimelineItem, ...]]
#     extra: dict = field(default_factory=dict)

#     def neighbors(self, node_id: str) -> dict[str, str]:
#         """node_id 와 이어진 노드 → 관계 라벨."""
#         out: dict[str, str] = {}
#         for e in self.edges:
#             if e.a == node_id:
#                 out[e.b] = e.label
#             elif e.b == node_id:
#                 out[e.a] = e.label
#         return out


# @dataclass(frozen=True)
# class IntegrationTool:
#     id: str
#     mark: str
#     name: str
#     desc: str
#     target: str
#     phase2: bool


# @dataclass(frozen=True)
# class AiToggle:
#     key: str
#     label: str
#     desc: str
#     default: bool


# @dataclass(frozen=True)
# class Notification:
#     id: str
#     kind: str  # review | shared | integration | member
#     text: str
#     when: str
#     meeting: str | None
#     unread: bool
#     folder: str | None = None  # member: 나를 멤버로 추가한 폴더


# @dataclass(frozen=True)
# class TeamMember:
#     id: str
#     name: str
#     role: str


# @dataclass(frozen=True)
# class Team:
#     """회의 공유 대상 (조직도)."""

#     id: str
#     name: str
#     members: tuple[TeamMember, ...]


"""목업 데이터 모델 (읽기 전용 도메인 객체).

data/mock/*.json 을 이 데이터클래스로 변환해서 씁니다. 화면에서 바뀌는 값(완료 체크, 담당자 지정 등)은
여기 넣지 않고 state/store.py 의 AppState 에 둡니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Person:
    id: str
    short: str  # 회의록 원문에 찍히는 이름 (예: 서윤)
    name: str  # 전체 이름 (예: 이서윤)
    team: str

    @property
    def initial(self) -> str:
        return self.name[:1]


MISC_AGENDA = "기타"  # 어느 안건에도 해당하지 않는 내용을 모으는 안건 (backend settings.MISC_AGENDA_TITLE과 같음)
NO_SPEAKER = "발화자"  # 발화자 없는 회의록의 원문 화자 표기 (화면에는 이름 없이 문장만 보여 줌)


@dataclass(frozen=True)
class Paragraph:
    id: str
    speaker: str  # 빈 문자열이면 안건 제목 줄
    text: str
    speaker_id: str = ""  # 화자의 user_id (AI 요약 결과) · 목업은 비어 있음 → 이름으로 찾음

    @property
    def is_heading(self) -> bool:
        return self.speaker == ""


@dataclass(frozen=True)
class Evidence:
    """요약·결정 등 한 문장과, 그 근거가 된 원문 문단 id 목록."""

    text: str
    src: tuple[str, ...] = ()


@dataclass(frozen=True)
class Discussion:
    label: str
    text: str
    src: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaggedPoint:
    """안건별 상세 「주요 논의」의 한 줄. kind: 정보 | 제안 | 반대·우려 | 질문"""

    kind: str
    text: str
    src: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgendaTopic:
    """주요 논의의 소주제 (예: 1-1. 예산 및 리모컨 단가)."""

    title: str
    points: tuple[TaggedPoint, ...]


@dataclass(frozen=True)
class Agenda:
    title: str
    status: str
    tone: str  # ok | warn | hold | info
    deep: bool
    src: tuple[str, ...]
    points: tuple[Evidence, ...]
    # --- 안건별 상세 (회의록 형식). 비어 있으면 화면에서 points · 회의 단위 결정/미해결로 대신 채움
    background: tuple[Evidence, ...] = ()  # 논의 배경
    topics: tuple[AgendaTopic, ...] = ()  # 주요 논의
    decisions: tuple[Evidence, ...] = ()  # 결정사항
    open_issues: tuple[Evidence, ...] = ()  # 미해결 이슈
    next_candidates: tuple[Evidence, ...] = ()  # 다음 회의 후보 안건
    detailed: bool = False  # True 면 위 필드가 데이터에 있음 (비어 있어도 그대로 보여 줌)
    agenda_id: str = ""  # MeetingSummary agenda_id (AI 요약 결과만 · 목업은 빈 값)


@dataclass(frozen=True)
class MeetingResult:
    """회의 결과 한 줄. kind: 결정 | 미해결 | 공유 · agendas: 관련 안건 번호(1부터)"""

    kind: str
    text: str
    agendas: tuple[int, ...] = ()
    src: tuple[str, ...] = ()


@dataclass(frozen=True)
class Todo:
    text: str
    owners: tuple[str, ...]  # 담당자 사람 id 들 (여러 명 가능 · 비어 있으면 담당자 미정)
    due: str
    src: tuple[str, ...]
    urgent: bool = False
    item_id: str = ""  # MeetingSummary 액션 item_id (= Neo4j ActionItem.action_item_id) · 목업 · 직접 추가한 일은 빈 값
    due_iso: str = ""  # 연도까지 있는 마감일 (YYYY-MM-DD[ HH:MM]) · 화면 표기 due(11/6)로는 연도를 알 수 없어요
    agenda: str = ""  # 직접 추가할 때 고른 안건 제목 (AI가 만든 할 일은 빈 값)
    assignee_check: bool = False  # AI가 적은 담당자를 참석자와 연결하지 못함 → 수정 창에서 담당자를 확인해야 확정 가능
    todo_id: int = 0  # DB(PostgreSQL)에 저장된 할 일 id · 미리보기 · 직접 추가 중인 일은 0 → 0이면 화면(세션)에서만 고침
    deleted: bool = False  # DB 에서 삭제한 할 일 (「삭제한 액션 아이템」에서 되돌릴 수 있음 · 번호는 그대로)


@dataclass(frozen=True)
class ShareInfo:
    sender: str
    scope: str  # summary | full
    received: str
    lock_raw: bool


@dataclass(frozen=True)
class Meeting:
    id: str
    title: str
    when: str
    date: str  # M/D
    project_id: str | None  # 프로젝트(폴더) id · 공유받은 회의는 None
    host: str
    people: tuple[str, ...]
    deep: bool
    time: str
    file: str
    initial_status: str
    paragraphs: tuple[Paragraph, ...]
    summary: Evidence
    discussion: tuple[Discussion, ...] | None
    keywords: tuple[str, ...]
    decisions: tuple[Evidence, ...]
    open_issues: tuple[Evidence, ...]
    agendas: tuple[Agenda, ...]
    todos: tuple[Todo, ...]
    directory_id: str = "normal"  # 회의 종류: weekly | monthly | normal
    held_on: object = None  # 회의 날짜 (datetime.date · 연도까지) · 목업은 비어 있음 → date(M/D)로 계산
    shared: ShareInfo | None = None
    branch_of: str | None = None  # 안건 분리로 생긴 회의라면 원래 회의 id
    results: tuple[MeetingResult, ...] = ()  # 회의 결과 (없으면 결정·미해결로 대신 만듦)
    author: str = ""  # 회의록을 쓴 사람 (DB · 미리보기는 빈 값)
    reviewer: str = ""  # 검토자 (DB). 있으면 이 사람이, 없으면 주관자가 고치고 확정해요 (신입이 쓴 회의록 → 윗사람)

    @property
    def weekday(self) -> str:
        if "(" in self.when and ")" in self.when:
            return self.when.split("(")[1][:1]
        return "월"

    def paragraph(self, pid: str) -> Paragraph | None:
        return next((p for p in self.paragraphs if p.id == pid), None)


@dataclass(frozen=True)
class MeetingType:
    id: str
    name: str
    meta: str
    hidden_count: int  # 멤버가 아니라서 안 보이는 폴더 수


@dataclass(frozen=True)
class BriefItem:
    label: str
    text: str


@dataclass(frozen=True)
class Folder:
    """프로젝트. 회의 종류(월간 · 주간 · 일반)는 프로젝트가 아니라 회의마다(Meeting.directory_id)."""
    id: str
    name: str
    host: str
    members: tuple[str, ...]
    notes: tuple[str, ...]
    brief: tuple[BriefItem, ...] = ()
    is_new: bool = False


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    team: str
    role: str
    score: int
    skill: int
    dept: int
    free: int
    tasks: int
    att: bool
    skills: tuple[str, ...]
    match: str = ""
    position: str = ""  # senior | mid | junior (구성원 표 · 목업은 비어 있음)


@dataclass(frozen=True)
class Predecessor:
    """담당자 추천: 이 일과 이어지는 선행 업무를 맡았던 사람 (추천 6명보다 위에 따로 보여 줌)."""

    name: str
    team: str
    role: str
    task: str  # 맡았던 선행 업무
    meeting: str  # 그 업무가 나온 회의
    date: str  # M/D
    user_id: str = ""  # 구성원 id (목업은 비어 있음 → 이름으로 찾음)


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    kind: str  # me | project | topic | decision | person
    x: float
    y: float
    desc: str
    initial: str = ""
    person: str | None = None


@dataclass(frozen=True)
class GraphEdge:
    a: str
    b: str
    label: str
    weight: float


@dataclass(frozen=True)
class TimelineItem:
    when: str
    text: str
    meeting: str
    src: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeGraph:
    nodes: dict[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    timeline: dict[str, tuple[TimelineItem, ...]]
    extra: dict = field(default_factory=dict)

    def neighbors(self, node_id: str) -> dict[str, str]:
        """node_id 와 이어진 노드 → 관계 라벨."""
        out: dict[str, str] = {}
        for e in self.edges:
            if e.a == node_id:
                out[e.b] = e.label
            elif e.b == node_id:
                out[e.a] = e.label
        return out


@dataclass(frozen=True)
class IntegrationTool:
    id: str
    mark: str
    name: str
    desc: str
    target: str
    phase2: bool
    last_sync: str = ""  # 마지막으로 보낸 시각 (관리자 · MCP 관리)


@dataclass(frozen=True)
class ExportFailure:
    """외부 도구로 회의 결과를 보내다 실패한 기록.

    kind: simple(일시적인 네트워크 오류 등 → 사용자가 다시 보내기) | tech(API · 인증 문제 → 관리자에게 문의)
    """

    id: str
    tool: str
    meeting: str
    kind: str
    reason: str
    code: str
    when: str
    title: str = ""  # 회의 제목 (DB 기록에 같이 옴 · 관리자는 회의 자체를 못 봐서 이걸로 보여 줘요)
    keys: tuple[str, ...] | None = None  # 실패한 것 (Jira 할 일 id · 메일 user_id) · None = 모름 → 다시 보낼 때 전부


@dataclass(frozen=True)
class Notification:
    id: str
    kind: str  # review | shared | integration | member
    text: str
    when: str
    meeting: str | None
    unread: bool
    folder: str | None = None  # member: 나를 멤버로 추가한 폴더


@dataclass(frozen=True)
class TeamMember:
    id: str
    name: str
    role: str


@dataclass(frozen=True)
class Team:
    """회의 공유 대상 (조직도)."""

    id: str
    name: str
    members: tuple[TeamMember, ...]
