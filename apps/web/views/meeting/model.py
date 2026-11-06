"""회의 상세 화면에서만 쓰는 파생 데이터."""

from __future__ import annotations

from dataclasses import dataclass

from config import CURRENT_USER_ID as ME
from data import repository as repo
from data.models import AgendaTopic, Evidence, Meeting, MeetingResult, TaggedPoint
from state import selectors as sel
from state.store import AppState
from ui.theme import SEGMENT_COLORS
from utils.text import find_date_token


def current(s: AppState) -> Meeting:
    return repo.meetings().get(s.meeting_id) or repo.meeting("m1")


def is_host(m: Meeting) -> bool:
    return sel.my_role(m) == "host"


def raw_locked(m: Meeting) -> bool:
    return bool(m.shared and m.shared.lock_raw)


def speaker_name(short: str) -> str:
    pid = sel.speaker_id(short)
    return sel.name_of(pid) if pid else short


def speaker_pid(s: AppState, m: Meeting, label: str) -> str | None:
    """원문 화자 → 사람 id. 사람으로 인식되지 않은 화자는 None."""
    return sel.speaker_id(label)


def speakers(s: AppState, m: Meeting) -> list[tuple[str, int]]:
    """이 회의 원문의 화자: [(사람 id, 발언 수)] · 나온 순서대로 (사람으로 인식되지 않은 화자는 넣지 않음)."""
    counts: dict[str, int] = {}
    for p in m.paragraphs:
        if not p.is_heading:
            pid = speaker_pid(s, m, p.speaker)
            if pid:
                counts[pid] = counts.get(pid, 0) + 1
    return list(counts.items())


def speaker_initial(short: str) -> str:
    pid = sel.speaker_id(short)
    return sel.person(pid).initial if pid else (short or " ")[:1]


def read_note(m: Meeting) -> str:
    role = sel.my_role(m)
    if role == "shared":
        return f"공유받음 · {sel.scope_label(m.shared.scope)} · 보낸 사람 {m.shared.sender}"
    if role == "member":
        return "미참석 · 폴더 멤버로 열람 · 읽기 전용"
    return f"읽기 전용 · 주관 {sel.name_of(m.host)}"


# ---------------------------------------------------------------- 요약 탭: 안건 목차 · 회의 결과 · 안건별 상세
def _overlaps(src: tuple[str, ...], other: tuple[str, ...]) -> bool:
    return bool(set(src) & set(other))


@dataclass(frozen=True)
class AgendaDetail:
    index: int  # 1부터
    title: str
    background: tuple[Evidence, ...]
    topics: tuple[AgendaTopic, ...]
    decisions: tuple[Evidence, ...]
    open_issues: tuple[Evidence, ...]
    next_candidates: tuple[Evidence, ...]


def agenda_details(m: Meeting) -> list[AgendaDetail]:
    """안건별 상세. 데이터에 상세가 없으면 안건 요점 · 근거가 겹치는 회의 단위 결정/미해결로 대신 채웁니다."""
    out = []
    for i, a in enumerate(m.agendas):
        if a.detailed:
            out.append(AgendaDetail(i + 1, a.title, a.background, a.topics, a.decisions, a.open_issues,
                                    a.next_candidates))
            continue
        topics = (AgendaTopic("", tuple(TaggedPoint("", p.text, p.src) for p in a.points)),) if a.points else ()
        out.append(AgendaDetail(
            i + 1, a.title, (), topics,
            tuple(d for d in m.decisions if _overlaps(d.src, a.src)),
            tuple(d for d in m.open_issues if _overlaps(d.src, a.src)),
            (),
        ))
    return out


def meeting_results(m: Meeting) -> tuple[MeetingResult, ...]:
    """회의 결과. 데이터에 없으면 결정 → 미해결 순으로 만들고, 근거가 겹치는 안건 번호를 붙입니다."""
    if m.results:
        return m.results

    def refs(src: tuple[str, ...]) -> tuple[int, ...]:
        return tuple(i + 1 for i, a in enumerate(m.agendas) if _overlaps(src, a.src))

    return tuple(MeetingResult("결정", d.text, refs(d.src), d.src) for d in m.decisions) + tuple(
        MeetingResult("미해결", d.text, refs(d.src), d.src) for d in m.open_issues
    )


def speaking_ratio(m: Meeting) -> list[tuple[str, int]]:
    """[(원문 화자, %)] · 사람으로 인식된 화자만 (비율도 그 사람들 발언 합 기준)."""
    counts: dict[str, int] = {}
    for p in m.paragraphs:
        if p.speaker and sel.speaker_id(p.speaker):
            counts[p.speaker] = counts.get(p.speaker, 0) + len(p.text)
    total = sum(counts.values()) or 1
    return [(name, round(n * 100 / total)) for name, n in sorted(counts.items(), key=lambda x: -x[1])]


@dataclass(frozen=True)
class AgendaWeight:
    index: int
    title: str
    pct: int
    lines: int
    long_note: str
    status: str
    tone: str
    color: str
    src: tuple[str, ...]


def agenda_status(s: AppState, m: Meeting, i: int) -> tuple[str, str, bool]:
    """(상태 라벨, tone, 분리됨 여부)"""
    a = m.agendas[i]
    branched = a.deep and s.branched and m.id == "m1"
    return ("분리됨", "split", True) if branched else (a.status, a.tone, False)


def agenda_weights(s: AppState, m: Meeting) -> list[AgendaWeight]:
    sums = [[0, 0] for _ in m.agendas]
    idx, total = -1, 0
    for p in m.paragraphs:
        if p.is_heading:
            idx += 1
            continue
        k = max(0, min(idx, len(sums) - 1))
        sums[k][0] += len(p.text)
        sums[k][1] += 1
        total += len(p.text)
    if not total:
        return []
    avg = 100 / len(m.agendas)
    out = []
    for i, a in enumerate(m.agendas):
        pct = round(sums[i][0] * 100 / total)
        status, tone, _ = agenda_status(s, m, i)
        out.append(AgendaWeight(
            i + 1, a.title, pct, sums[i][1],
            " · 평균보다 길게 논의됨" if len(m.agendas) > 1 and pct > avg * 1.4 else "",
            status, tone, SEGMENT_COLORS[i % len(SEGMENT_COLORS)], a.src,
        ))
    return out


@dataclass(frozen=True)
class DateLine:
    pid: str
    token: str
    who: str
    text: str


def date_lines(m: Meeting) -> list[DateLine]:
    out = []
    for p in m.paragraphs:
        token = find_date_token(p.text) if p.speaker else None
        if token:
            out.append(DateLine(p.id, token, speaker_name(p.speaker), p.text))
    return out


@dataclass(frozen=True)
class TodoView:
    index: int
    key: str
    text: str
    done: bool
    mine: bool
    who: str
    who_tone: str
    due: str
    raw_due: str
    urgent: bool
    speaker: str
    quote: str
    src: tuple[str, ...]

    @property
    def plain(self) -> str:
        return f"- {self.text} ({self.who}, {self.raw_due}까지)"


def todos(s: AppState, m: Meeting) -> list[TodoView]:
    out = []
    for i, t in enumerate(m.todos):
        key = sel.todo_key(m.id, i)
        done = s.done.get(key, False)
        mine = t.owner == ME
        who = sel.name_of(t.owner) if t.owner else (", ".join(s.assigned.get(key, [])) or "담당 미정")
        para = m.paragraph(t.src[0]) if t.src else None
        out.append(TodoView(
            index=i, key=key, text=t.text, done=done, mine=mine,
            who="나" if mine else who,
            who_tone="mine" if mine else ("att" if t.owner else "warn"),
            due=f"{t.due}까지" + (" · 곧 마감" if t.urgent and not done else ""),
            raw_due=t.due,
            urgent=t.urgent and not done,
            speaker=speaker_name(para.speaker) if para else "원문 비공개",
            quote=para.text if para else "원문이 공유되지 않았어요",
            src=t.src,
        ))
    return out


def assignable(s: AppState, m: Meeting) -> list[tuple[str, str, list[str], list[str], tuple[str, ...]]]:
    """회의록에서 담당자가 정해지지 않았던 일.

    (todo key, 할 일, 지금 담당자들, 바꾸기 전 담당자들, 원문 근거 문단 id들) · 담당자가 없으면 빈 목록
    """
    return [
        (sel.todo_key(m.id, i), t.text, list(s.assigned.get(sel.todo_key(m.id, i), [])),
         list(s.assign_prev.get(sel.todo_key(m.id, i), [])), t.src)
        for i, t in enumerate(m.todos)
        if not t.owner
    ]


def assignee_editable(s: AppState, m: Meeting) -> bool:
    """회의를 확정(결과 전달)하기 전까지만 담당자를 바꿀 수 있어요."""
    return sel.status_of(s, m.id) == "review"


@dataclass(frozen=True)
class RelatedView:
    id: str
    title: str
    date: str
    relation: str
    role: str
    tone: str


def related(s: AppState, m: Meeting) -> list[RelatedView]:
    folders = sel.folders(s)
    if m.topic not in folders:
        return []
    out = []
    for mid in [i for i in sel.folder_note_ids(s, m.topic) if i != m.id]:  # 나에게 보이는 회의만
        other = repo.meeting(mid)
        if m.id == "m1" and mid == "m9":
            rel = "이 회의 안건 2에서 분리됨"
        elif m.id == "m9" and mid == "m1":
            rel = "분리되기 전 원래 회의"
        else:
            rel = "같은 폴더 · 심화 회의" if other.deep else "같은 폴더 · 정기 회의"
        label, tone = sel.ROLE_TAGS[sel.my_role(other)]
        out.append(RelatedView(mid, other.title, other.date, rel, label, tone))
    return out


def speaker_name_of_host(m: Meeting) -> str:
    return sel.name_of(m.host)
