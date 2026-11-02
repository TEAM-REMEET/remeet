# """회의 상세 화면에서만 쓰는 파생 데이터."""

# from __future__ import annotations

# from dataclasses import dataclass

# from config import CURRENT_USER_ID as ME
# from data import repository as repo
# from data.models import Discussion, Meeting
# from state import selectors as sel
# from state.store import AppState
# from ui.theme import SEGMENT_COLORS
# from utils.text import find_date_token


# def current(s: AppState) -> Meeting:
#     return repo.meetings().get(s.meeting_id) or repo.meeting("m1")


# def is_host(m: Meeting) -> bool:
#     return sel.my_role(m) == "host"


# def raw_locked(m: Meeting) -> bool:
#     return bool(m.shared and m.shared.lock_raw)


# def speaker_name(short: str) -> str:
#     pid = sel.speaker_id(short)
#     return sel.name_of(pid) if pid else short


# def speaker_initial(short: str) -> str:
#     pid = sel.speaker_id(short)
#     return sel.person(pid).initial if pid else (short or " ")[:1]


# def read_note(m: Meeting) -> str:
#     role = sel.my_role(m)
#     if role == "shared":
#         return f"공유받음 · {sel.scope_label(m.shared.scope)} · 보낸 사람 {m.shared.sender}"
#     if role == "member":
#         return "미참석 · 폴더 멤버로 열람 · 읽기 전용"
#     return f"읽기 전용 · 주관 {sel.name_of(m.host)}"


# def discussion(m: Meeting) -> tuple[Discussion, ...]:
#     """주요 논의 사항. 데이터에 없으면 결정·쟁점·안건으로 대신 만듭니다."""
#     if m.discussion:
#         return m.discussion
#     return (
#         Discussion("의사결정", ", ".join(d.text for d in m.decisions) or "아직 결정된 것이 없어요",
#                    m.decisions[0].src if m.decisions else ()),
#         Discussion("남은 쟁점", ", ".join(d.text for d in m.open_issues), m.open_issues[0].src if m.open_issues else ()),
#         Discussion("다룬 안건", ", ".join(f"{a.title}({a.status})" for a in m.agendas), m.agendas[0].src if m.agendas else ()),
#     )


# def speaking_ratio(m: Meeting) -> list[tuple[str, int]]:
#     counts: dict[str, int] = {}
#     for p in m.paragraphs:
#         if p.speaker:
#             counts[p.speaker] = counts.get(p.speaker, 0) + len(p.text)
#     total = sum(counts.values()) or 1
#     return [(name, round(n * 100 / total)) for name, n in sorted(counts.items(), key=lambda x: -x[1])]


# @dataclass(frozen=True)
# class AgendaWeight:
#     index: int
#     title: str
#     pct: int
#     lines: int
#     long_note: str
#     status: str
#     tone: str
#     color: str
#     src: tuple[str, ...]


# def agenda_status(s: AppState, m: Meeting, i: int) -> tuple[str, str, bool]:
#     """(상태 라벨, tone, 분리됨 여부)"""
#     a = m.agendas[i]
#     branched = a.deep and s.branched and m.id == "m1"
#     return ("분리됨", "split", True) if branched else (a.status, a.tone, False)


# def agenda_weights(s: AppState, m: Meeting) -> list[AgendaWeight]:
#     sums = [[0, 0] for _ in m.agendas]
#     idx, total = -1, 0
#     for p in m.paragraphs:
#         if p.is_heading:
#             idx += 1
#             continue
#         k = max(0, min(idx, len(sums) - 1))
#         sums[k][0] += len(p.text)
#         sums[k][1] += 1
#         total += len(p.text)
#     if not total:
#         return []
#     avg = 100 / len(m.agendas)
#     out = []
#     for i, a in enumerate(m.agendas):
#         pct = round(sums[i][0] * 100 / total)
#         status, tone, _ = agenda_status(s, m, i)
#         out.append(AgendaWeight(
#             i + 1, a.title, pct, sums[i][1],
#             " · 평균보다 길게 논의됨" if len(m.agendas) > 1 and pct > avg * 1.4 else "",
#             status, tone, SEGMENT_COLORS[i % len(SEGMENT_COLORS)], a.src,
#         ))
#     return out


# @dataclass(frozen=True)
# class DateLine:
#     pid: str
#     token: str
#     who: str
#     text: str


# def date_lines(m: Meeting) -> list[DateLine]:
#     out = []
#     for p in m.paragraphs:
#         token = find_date_token(p.text) if p.speaker else None
#         if token:
#             out.append(DateLine(p.id, token, speaker_name(p.speaker), p.text))
#     return out


# @dataclass(frozen=True)
# class TodoView:
#     index: int
#     key: str
#     text: str
#     done: bool
#     mine: bool
#     who: str
#     who_tone: str
#     due: str
#     raw_due: str
#     urgent: bool
#     speaker: str
#     quote: str
#     src: tuple[str, ...]

#     @property
#     def plain(self) -> str:
#         return f"- {self.text} ({self.who}, {self.raw_due}까지)"


# def todos(s: AppState, m: Meeting) -> list[TodoView]:
#     out = []
#     for i, t in enumerate(m.todos):
#         key = sel.todo_key(m.id, i)
#         done = s.done.get(key, False)
#         mine = t.owner == ME
#         who = sel.name_of(t.owner) if t.owner else s.assigned.get(key, "담당 미정")
#         para = m.paragraph(t.src[0]) if t.src else None
#         out.append(TodoView(
#             index=i, key=key, text=t.text, done=done, mine=mine,
#             who="나" if mine else who,
#             who_tone="mine" if mine else ("att" if t.owner else "warn"),
#             due=f"{t.due}까지" + (" · 곧 마감" if t.urgent and not done else ""),
#             raw_due=t.due,
#             urgent=t.urgent and not done,
#             speaker=speaker_name(para.speaker) if para else "원문 비공개",
#             quote=para.text if para else "원문이 공유되지 않았어요",
#             src=t.src,
#         ))
#     return out


# def assignable(s: AppState, m: Meeting) -> list[tuple[str, str, str, str]]:
#     """회의록에서 담당자가 정해지지 않았던 일. (todo key, 할 일, 마감, 지정한 사람 이름 또는 '')"""
#     return [
#         (sel.todo_key(m.id, i), t.text, f"{t.due}까지", s.assigned.get(sel.todo_key(m.id, i), ""))
#         for i, t in enumerate(m.todos)
#         if not t.owner
#     ]


# def assignee_editable(s: AppState, m: Meeting) -> bool:
#     """회의를 확정(결과 전달)하기 전까지만 담당자를 바꿀 수 있어요."""
#     return sel.status_of(s, m.id) == "review"


# @dataclass(frozen=True)
# class RelatedView:
#     id: str
#     title: str
#     date: str
#     relation: str
#     role: str
#     tone: str


# def related(s: AppState, m: Meeting) -> list[RelatedView]:
#     folders = sel.folders(s)
#     if m.topic not in folders:
#         return []
#     out = []
#     for mid in sel.by_date_desc([i for i in folders[m.topic].notes if i != m.id]):
#         other = repo.meeting(mid)
#         if m.id == "m1" and mid == "m9":
#             rel = "이 회의 안건 2에서 분리됨"
#         elif m.id == "m9" and mid == "m1":
#             rel = "분리되기 전 원래 회의"
#         else:
#             rel = "같은 폴더 · 심화 회의" if other.deep else "같은 폴더 · 정기 회의"
#         label, tone = sel.ROLE_TAGS[sel.my_role(other)]
#         out.append(RelatedView(mid, other.title, other.date, rel, label, tone))
#     return out


# def speaker_name_of_host(m: Meeting) -> str:
#     return sel.name_of(m.host)


"""회의 상세 화면에서만 쓰는 파생 데이터."""

from __future__ import annotations

from dataclasses import dataclass

from config import CURRENT_USER_ID as ME
from data import repository as repo
from data.models import Discussion, Meeting
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


def discussion(m: Meeting) -> tuple[Discussion, ...]:
    """주요 논의 사항. 데이터에 없으면 결정·쟁점·안건으로 대신 만듭니다."""
    if m.discussion:
        return m.discussion
    return (
        Discussion("의사결정", ", ".join(d.text for d in m.decisions) or "아직 결정된 것이 없어요",
                   m.decisions[0].src if m.decisions else ()),
        Discussion("남은 쟁점", ", ".join(d.text for d in m.open_issues), m.open_issues[0].src if m.open_issues else ()),
        Discussion("다룬 안건", ", ".join(f"{a.title}({a.status})" for a in m.agendas), m.agendas[0].src if m.agendas else ()),
    )


def speaking_ratio(m: Meeting) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for p in m.paragraphs:
        if p.speaker:
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
        who = sel.name_of(t.owner) if t.owner else s.assigned.get(key, "담당 미정")
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


def assignable(s: AppState, m: Meeting) -> list[tuple[str, str, str, str]]:
    """회의록에서 담당자가 정해지지 않았던 일. (todo key, 할 일, 마감, 지정한 사람 이름 또는 '')"""
    return [
        (sel.todo_key(m.id, i), t.text, f"{t.due}까지", s.assigned.get(sel.todo_key(m.id, i), ""))
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
