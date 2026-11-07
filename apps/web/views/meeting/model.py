"""회의 상세 화면에서만 쓰는 파생 데이터."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from state.store import me_id
from data import repository as repo
from data.models import AgendaTopic, Evidence, Meeting, MeetingResult, TaggedPoint
from state import selectors as sel
from state.store import AppState
from ui.theme import SEGMENT_COLORS


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
            pid = sel.paragraph_speaker(p)
            if pid:
                counts[pid] = counts.get(pid, 0) + 1
    return list(counts.items())


def speaker_labels(m: Meeting) -> dict[str, str]:
    """사람 id → 원문 표시 이름 (AI 요약 결과만 · 다른 직무의 동명이인은 홍길동(직무))."""
    return {p.speaker_id: p.speaker for p in m.paragraphs if p.speaker_id}


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
        if p.speaker and sel.paragraph_speaker(p):
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


def _has_headings(m: Meeting) -> bool:
    return any(p.is_heading for p in m.paragraphs)


def weight_basis(m: Meeting) -> str:
    return "원문 분량 기준" if _has_headings(m) else "근거 발언 분량 기준"


def _sums_by_heading(m: Meeting) -> list[list[float]]:
    """원문에 안건 제목 줄이 있는 회의(목업): 제목 줄 사이의 발언을 그 안건으로 셈. [글자 수, 발언 수]"""
    sums: list[list[float]] = [[0, 0] for _ in m.agendas]
    idx = -1
    for p in m.paragraphs:
        if p.is_heading:
            idx += 1
            continue
        k = max(0, min(idx, len(sums) - 1))
        sums[k][0] += len(p.text)
        sums[k][1] += 1
    return sums


def _sums_by_evidence(m: Meeting) -> list[list[float]]:
    """AI 요약 결과: 안건 항목의 근거 발언을 셈. 여러 안건의 근거인 발언은 글자 수를 나눠 줌. [글자 수, 발언 수]"""
    length = {p.id: len(p.text) for p in m.paragraphs}
    per_agenda = [[pid for pid in dict.fromkeys(a.src) if pid in length] for a in m.agendas]
    shared = Counter(pid for pids in per_agenda for pid in pids)
    return [[sum(length[pid] / shared[pid] for pid in pids), len(pids)] for pids in per_agenda]


def _percents(values: list[float]) -> list[int]:
    """합이 정확히 100이 되도록 반올림 (최대 나머지 방식)."""
    total = sum(values)
    exact = [v * 100 / total for v in values]
    out = [int(x) for x in exact]
    for i in sorted(range(len(exact)), key=lambda i: out[i] - exact[i])[:100 - sum(out)]:
        out[i] += 1
    return out


def agenda_weights(s: AppState, m: Meeting) -> list[AgendaWeight]:
    sums = _sums_by_heading(m) if _has_headings(m) else _sums_by_evidence(m)
    if not sum(chars for chars, _ in sums):
        return []
    percents = _percents([chars for chars, _ in sums])
    avg = 100 / len(m.agendas)
    out = []
    for i, a in enumerate(m.agendas):
        pct = percents[i]
        status, tone, _ = agenda_status(s, m, i)
        out.append(AgendaWeight(
            i + 1, a.title, pct, sums[i][1],
            " · 평균보다 길게 논의됨" if len(m.agendas) > 1 and pct > avg * 1.4 else "",
            status, tone, SEGMENT_COLORS[i % len(SEGMENT_COLORS)], a.src,
        ))
    return out


@dataclass(frozen=True)
class TodoView:
    index: int
    key: str
    text: str
    mine: bool
    who: str
    who_tone: str
    who_tags: tuple[tuple[str, str], ...]  # 담당자 한 명당 태그 하나 (이름, tone) · 나는 「나」 주황, 다른 사람은 회색
    unassigned: bool  # 담당자가 아직 없음 (회의록에도 없고 지정도 안 함) → 「담당 미정」
    due: str
    raw_due: str
    urgent: bool
    speaker: str
    quote: str
    src: tuple[str, ...]

    @property
    def plain(self) -> str:
        return f"- {self.text} ({self.who}, {self.raw_due}까지)" if self.raw_due else f"- {self.text} ({self.who})"


def todos(s: AppState, m: Meeting) -> list[TodoView]:
    out = []
    for i, t in sel.todo_items(s, m.id):  # 삭제한 액션 아이템은 빼고 (번호는 원래 그대로)
        key = sel.todo_key(m.id, i)
        me = me_id()
        if s.assigned.get(key):  # 주관자가 지정 · 변경한 사람들 (user_id로 저장 · 회의록 담당자보다 먼저)
            picked = s.assigned[key]
            mine = me in picked
            who = ", ".join("나" if pid == me else sel.assignee_name(pid) for pid in picked)
            # 회의록에 담당자가 없던 일은 노란색, 있던 일을 바꾼 것은 회색 (나는 주황)
            tags = tuple(("나", "mine") if pid == me else (sel.assignee_name(pid), "att" if t.owners else "warn")
                         for pid in picked)
        elif t.owners:
            mine = me in t.owners
            who = ", ".join("나" if pid == me else sel.name_of(pid) for pid in t.owners)
            tags = tuple(("나", "mine") if pid == me else (sel.name_of(pid), "att") for pid in t.owners)
        else:
            mine, who, tags = False, "담당 미정", (("담당 미정", "warn"),)
        para = m.paragraph(t.src[0]) if t.src else None
        out.append(TodoView(
            index=i, key=key, text=t.text, mine=mine,
            who=who,
            who_tone="mine" if mine else ("att" if t.owners else "warn"),  # (안 씀) 한 덩어리 태그 색
            who_tags=tags,
            unassigned=not t.owners and not s.assigned.get(key),
            due=(f"{t.due}까지" if t.due else "마감일 없음") + (" · 곧 마감" if t.urgent else ""),
            raw_due=t.due,
            urgent=t.urgent,
            speaker=speaker_name(para.speaker) if para else ("원문 비공개" if t.src else "근거 없음"),
            quote=para.text if para else ("원문이 공유되지 않았어요" if t.src else "원문에서 관련 발언을 찾지 못했어요"),
            src=t.src,
        ))
    return out


def assignable(s: AppState, m: Meeting) -> list[tuple[str, str, list[str], list[str], tuple[str, ...]]]:
    """회의록에서 담당자가 정해지지 않았던 일.

    (todo key, 할 일, 지금 담당자들, 바꾸기 전 담당자들, 원문 근거 문단 id들) · 담당자가 없으면 빈 목록
    """
    return [
        (sel.todo_key(m.id, i), t.text, [sel.assignee_name(p) for p in s.assigned.get(sel.todo_key(m.id, i), [])],
         [sel.assignee_name(p) for p in s.assign_prev.get(sel.todo_key(m.id, i), [])], t.src)
        for i, t in sel.todo_items(s, m.id)
        if not t.owners
    ]


def deleted_todos(s: AppState, m: Meeting) -> list[tuple[int, str]]:
    """삭제한 액션 아이템 [(원래 순서 번호, 할 일)] · 지운 순서대로 (되돌리기 목록)."""
    return [(i, m.todos[i].text) for i in s.deleted_todos.get(m.id, []) if i < len(m.todos)]


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
    if m.project_id not in folders:
        return []
    out = []
    for mid in [i for i in sel.folder_note_ids(s, m.project_id) if i != m.id]:  # 나에게 보이는 회의만
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
