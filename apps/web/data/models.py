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


@dataclass(frozen=True)
class Paragraph:
    id: str
    speaker: str  # 빈 문자열이면 안건 제목 줄
    text: str

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
class Agenda:
    title: str
    status: str
    tone: str  # ok | warn | hold | info
    deep: bool
    src: tuple[str, ...]
    points: tuple[Evidence, ...]


@dataclass(frozen=True)
class Todo:
    text: str
    owner: str | None  # 사람 id, 없으면 담당자 미정
    due: str
    src: tuple[str, ...]
    urgent: bool = False


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
    topic: str | None
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
    shared: ShareInfo | None = None
    branch_of: str | None = None  # 안건 분리로 생긴 회의라면 원래 회의 id

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
    id: str
    name: str
    type: str
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


@dataclass(frozen=True)
class AiToggle:
    key: str
    label: str
    desc: str
    default: bool


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
