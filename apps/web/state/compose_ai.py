"""새 회의록 작성: 입력 형식 검사 · 발화자 매칭 · 분석(데모 규칙) · 완성된 회의록 만들기.

실제 서비스에서는 AI 서버가 하는 일을, 데모에서는 간단한 규칙으로 흉내 냅니다.

지원하는 입력 형식 (한 회의록 안에서는 한 가지만)
  1) 발화자 없는 텍스트      이번 주에는 로그인 오류를 수정하겠습니다.
  2) [발화자] 형식           [참석자1]
                             이번 주에는 로그인 오류를 수정하겠습니다.   (발화자 줄 바로 아랫줄이 발언 · 한 줄에 같이 쓰면 오류)
  3) 발화자: 형식            A: 이번 주에는 로그인 오류를 수정하겠습니다.
세 형식 모두 발언마다 한 줄씩 띄워야 해요 (빈 줄이 없으면 오류).
이 밖의 경우(형식을 섞어 씀 · 발화자 표기가 빠진 줄 · [발화자] 내용을 한 줄에 씀 · [발화자] 다음에 발언이 없음 · 닫히지 않은 [ ] 등)는 오류로 알려 줍니다.
AI 가 붙인 「참석자1」, 「A」 같은 임시 발화자는 2단계에서 실제 사람과 연결합니다.
"""

from __future__ import annotations

from config import UI_TEST_TRANSCRIPT

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from data import repository as repo
from data.models import Agenda, Discussion, Evidence, Meeting, Paragraph, Todo
from utils.text import find_date_token

FORMAT_LABEL = {"plain": "발화자 없는 텍스트", "bracket": "[발화자] 형식", "colon": "발화자: 형식"}

# 형식 버튼 아래 회색 안내글: 고른 형식에서 벗어나면 안 된다는 규칙
FORMAT_RULE = {
    "plain": "발화자 표기 없이 문장만 적고, 발언마다 한 줄씩 띄워 주세요. [김철수] · 김철수: 같은 발화자 표기를 넣으면 안 돼요.",
    "bracket": "[발화자]만 한 줄에 쓰고 바로 아랫줄에 발언을 적은 뒤, 한 줄 띄우고 다음 [발화자]를 적어 주세요. "
               "[발화자]와 발언을 한 줄에 쓰면 안 돼요. 발화자는 참석자 이름으로 적어 주세요.",
    "colon": "모든 줄을 「발화자: 발언」으로 적고, 발언마다 한 줄씩 띄워 주세요. 발화자 표기가 빠진 줄이 있으면 안 되고, "
             "발화자는 참석자 이름으로 적어 주세요.",
}

EXAMPLES = {
    "plain": (  # 발언마다 한 줄씩 띄움 ([발화자] 형식과 같은 모양)
        "이번 주에는 로그인 오류를 먼저 수정하기로 했습니다.\n\n"
        "디자인 시안은 금요일에 공유할 예정입니다.\n\n"
        "수정이 끝나면 QA팀에 테스트를 요청하겠습니다."
    ),
    "bracket": (
        "[김철수]\n이번 주에는 로그인 오류를 수정하겠습니다.\n\n"
        "[김영희]\n디자인 시안은 금요일에 공유할게요.\n\n"
        "[김철수]\n수정 후 테스트를 요청하겠습니다."
    ),
    "colon": (  # 발언마다 한 줄씩 띄움
        "김철수: 결제 오류 안내 문구는 이번 주 안에 확정하기로 해요.\n\n"
        "김영희: 그럼 초안은 제가 수요일까지 공유할게요.\n\n"
        "김철수: 좋아요. 고객 문의가 늘었는지도 확인해 주세요?"
    ),
}

_BRACKET_ONLY = re.compile(r"^\[([^\[\]]{1,20})\]$")  # [참석자1] 만 있는 줄 (다음 줄이 발언)
_BRACKET = re.compile(r"^\[([^\[\]]{1,20})\]\s*(\S.*)$")
_COLON = re.compile(r"^([^\s:\[\]][^:\[\]]{0,19}?)\s*:\s*(\S.*)$")
_TODO = re.compile(r"(하겠습니다|할게요|할께요|하겠어요|해 주세요|해주세요|공유|요청|까지|예정|맡)")
_DECISION = re.compile(r"(하기로|결정|확정|합의)")
_OPEN = re.compile(r"(\?|검토|논의가 필요|확인이 필요|미정)")
_STOP = {"이번", "그럼", "제가", "저는", "우리", "그리고", "그래서", "좋아요", "수정", "있는지도", "다음", "먼저"}


@dataclass(frozen=True)
class Line:
    no: int  # 몇 번째 줄 (1부터)
    label: str | None  # 원문 발화자 표기 (없으면 None)
    text: str


@dataclass(frozen=True)
class Parsed:
    format: str | None  # plain | bracket | colon (오류면 None)
    lines: tuple[Line, ...] = ()
    labels: tuple[str, ...] = ()  # 나온 순서대로 발화자 표기
    error: str = ""  # 오류 안내 (없으면 빈 값)
    error_lines: tuple[int, ...] = ()


def _kind(line: str) -> tuple[str, str | None, str]:
    """B: [발화자]만 있는 줄 · b: [발화자] 내용 (한 줄) · c: 발화자: 내용 · p: 그냥 글"""
    m = _BRACKET_ONLY.match(line)
    if m:
        return "B", m.group(1).strip(), ""
    m = _BRACKET.match(line)
    if m:
        return "b", m.group(1).strip(), m.group(2).strip()
    m = _COLON.match(line)
    if m and not m.group(1).strip().isdigit():  # 10:30 같은 시간은 발화자가 아님
        return "c", m.group(1).strip(), m.group(2).strip()
    return "p", None, line


def _parse_bracket(kinds: list) -> Parsed:
    """[발화자] 형식: [발화자]만 있는 줄 바로 다음 줄이 그 사람의 발언 (빈 줄 규칙은 _spacing).

    「[발화자] 내용」처럼 한 줄에 같이 쓰면 오류.
    """
    inline = tuple(no for no, k, _, _ in kinds if k == "b")
    if inline:
        return Parsed(None, error="[발화자]와 발언을 한 줄에 쓸 수 없어요. [발화자] 줄 다음 줄에 발언을 적어 주세요.",
                      error_lines=inline)
    lines: list[Line] = []
    cur: str | None = None
    cur_no = 0
    has_text = True
    for no, k, label, body in kinds:
        if k in ("B", "b"):
            if cur is not None and not has_text:
                return Parsed(None, error=f"[{cur}] 다음 줄에 발언 내용이 없어요. 발화자 아래 줄에 내용을 적어 주세요.",
                              error_lines=(cur_no,))
            cur, cur_no, has_text = label, no, False
        elif cur is None:  # 첫 [발화자] 보다 앞에 있는 글
            bad = tuple(n for n, kk, _, _ in kinds if kk == "p" and n < next(x for x, y, _, _ in kinds if y in ("B", "b")))
            return Parsed(None, error="첫 줄에 [발화자]가 없어요. [발화자] 줄로 시작하고, 그 아래 줄에 발언을 적어 주세요.",
                          error_lines=bad)
        else:
            lines.append(Line(no, cur, body))
            has_text = True
    if cur is not None and not has_text:
        return Parsed(None, error=f"[{cur}] 다음 줄에 발언 내용이 없어요. 발화자 아래 줄에 내용을 적어 주세요.",
                      error_lines=(cur_no,))
    return Parsed("bracket", tuple(lines), tuple(dict.fromkeys(ln.label for ln in lines)))


_GAP_ERROR = "발언 사이에 빈 줄이 없어요. 발언마다 한 줄씩 띄워 주세요."


def _spacing(parsed: Parsed, kinds: list) -> Parsed:
    """빈 줄 규칙 (발언마다 한 줄씩 띄우기).

    - 발화자 없는 텍스트 · 발화자: 형식: 발언과 발언 사이에 빈 줄이 있어야 함
    - [발화자] 형식: [발화자] 바로 아랫줄에 발언 (사이에 빈 줄 X), 발언 다음엔 빈 줄을 띄우고 다음 [발화자]
    """
    if parsed.error:
        return parsed
    nos = [no for no, *_ in kinds]  # 글이 있는 줄 번호 (빈 줄은 번호가 비어 있음)
    if parsed.format == "bracket":
        for i, (no, k, label, _) in enumerate(kinds):
            nxt = nos[i + 1] if i + 1 < len(nos) else None
            if k == "B" and nxt is not None and nxt != no + 1:
                return Parsed(None, error=f"[{label}] 바로 아랫줄에 발언을 적어 주세요. 사이에 빈 줄을 넣으면 안 돼요.",
                              error_lines=(no,))
            if k != "B" and nxt == no + 1:  # 발언 바로 다음 줄에 또 글이 있음
                return Parsed(None, error=_GAP_ERROR, error_lines=(nxt,))
        return parsed
    bad = tuple(b for a, b in zip(nos, nos[1:]) if b == a + 1)
    if bad:
        return Parsed(None, error=_GAP_ERROR, error_lines=bad)
    return parsed


def parse(text: str, fmt: str | None = None) -> Parsed:
    """입력 형식 검사. 지원하지 않는 형식이면 error 에 이유를 적어 돌려 줌 (빈 줄 규칙 포함).

    fmt: 사용자가 고른 형식. plain이면 콜론 · 대괄호가 있어도 발화자로 보지 않고 한 줄을 문장 하나로 읽어요.
    회의 내용이 시험용 값(docs/ui-test-mode.md)이면 형식 검사를 건너뛰어요. 시험 모드가 꺼져 있으면 백엔드가 거절해요.
    """
    if (text or "").strip().lower() == UI_TEST_TRANSCRIPT:
        return Parsed(fmt or "plain", (Line(1, None, text.strip()),))
    kinds_all = [(i + 1, *_kind(ln.strip())) for i, ln in enumerate((text or "").splitlines()) if ln.strip()]
    return _spacing(_parse_plain(text) if fmt == "plain" else _parse(text), kinds_all)


def _parse_plain(text: str) -> Parsed:
    raw = [(i + 1, ln.strip()) for i, ln in enumerate((text or "").splitlines())]
    raw = [(no, ln) for no, ln in raw if ln]
    if not raw or sum(len(ln) for _, ln in raw) < 10:  # 비었거나 너무 짧으면 같은 안내
        return _parse(text)
    return Parsed("plain", tuple(Line(no, None, ln) for no, ln in raw))


def _parse(text: str) -> Parsed:
    """형식 판별 (발화자 표기 · 섞어 쓰기 등). 빈 줄 규칙은 parse() 에서."""
    raw = [(i + 1, ln.strip()) for i, ln in enumerate((text or "").splitlines())]
    raw = [(no, ln) for no, ln in raw if ln]
    if not raw:
        return Parsed(None, error="회의 내용을 적거나 TXT · MD · DOCX · HWPX 파일을 첨부해 주세요.")
    if sum(len(ln) for _, ln in raw) < 10:
        return Parsed(None, error="내용이 너무 짧아요. 회의에서 나눈 말을 한 문장 이상 적어 주세요.")

    kinds = [(no, *_kind(ln)) for no, ln in raw]
    used = {k for _, k, _, _ in kinds}
    bracket = used & {"B", "b"}
    if bracket and "c" in used:
        bad = tuple(no for no, k, _, _ in kinds if k == "c")
        return Parsed(None, error="[발화자] 형식과 발화자: 형식을 한 회의록에 섞어 쓸 수 없어요. 한 가지로 맞춰 주세요.",
                      error_lines=bad)
    if bracket:
        return _parse_bracket(kinds)
    if "c" in used:
        if "p" in used:
            bad = tuple(no for no, k, _, _ in kinds if k == "p")
            return Parsed(None, error="발화자 표기가 빠진 줄이 있어요. 모든 줄을 「발화자: 내용」으로 적거나, 발화자 없이 적어 주세요.",
                          error_lines=bad)
        lines = tuple(Line(no, label, body) for no, _, label, body in kinds)
        return Parsed("colon", lines, tuple(dict.fromkeys(ln.label for ln in lines)))
    bad = tuple(no for no, ln in raw if ln.startswith("[") and "]" not in ln)
    if bad:
        return Parsed(None, error="[ ] 가 닫히지 않은 줄이 있어요. [발화자] 처럼 괄호를 닫아 주세요.", error_lines=bad)
    return Parsed("plain", tuple(Line(no, None, ln) for no, ln in raw))


ANALYZE_DELAY_SECONDS = 2.0  # 목업: 「확정하기」 때 AI 분석 · 회의록 생성에 걸리는 시간 흉내 (로딩 화면)


def match_speakers(labels: tuple[str, ...], attendees: list[str]) -> tuple[dict[str, str], list[str]]:
    """발화자 표기 → 참석자 id. 참석자의 이름(전체 · 짧은 이름) 또는 이름(직무)와 같아야 해요.

    돌려줌: (연결, 참석자에 없는 발화자 표기들) · 없는 발화자가 있으면 형식 오류 (미지정으로 두지 않음)
    """
    people = repo.people()
    jobs = {r["user_id"]: r.get("job") or "" for r in repo.people_rows()}
    mapping, unknown = {}, []

    def same(a: str, b: str) -> bool:
        return "".join(a.split()) == "".join(b.split())

    for label in labels:
        pid = next((p for p in dict.fromkeys(attendees) if p in people and any(
            same(label, x) for x in (people[p].short, people[p].name, f"{people[p].name}({jobs.get(p, '')})"))), "")
        if pid:
            mapping[label] = pid
        else:
            unknown.append(label)
    return mapping, unknown


# ---------------------------------------------------------------- 분석 (데모 규칙)
@dataclass(frozen=True)
class Analysis:
    summary: str
    agendas: tuple[tuple[str, tuple[str, ...]], ...]  # (안건 제목, 그 안건의 발언들)
    todos: tuple[tuple[str, str, str], ...]  # (할 일, 담당자 이름들(쉼표로 구분) 또는 표기, 기한)
    decisions: tuple[str, ...]
    open_issues: tuple[str, ...]
    keywords: tuple[str, ...]
    transcript: tuple[tuple[str, str], ...]  # (보이는 발화자 이름, 발언) · 매칭 반영
    mapping: dict[str, str] = field(default_factory=dict)  # 발화자 표기 → 참석자 id


_TOGETHER = re.compile(r"(같이|함께|둘이|두 분|세 분|랑|이랑|하고|와 |과 |및)")


def todo_owners(text: str, label: str | None, mapping: dict[str, str]) -> tuple[str, ...]:
    """액션 아이템 담당자들 (사람 id · 여러 명 가능) — 데모 규칙.

    말한 사람(발화자로 연결된 사람) + 「같이 · 함께 · 랑 …」처럼 함께 맡는 표현이 있으면 발언에 이름이 나온
    이 회의의 다른 사람도 담당자로 넣어요. 실제 서비스에서는 에이전트가 assignees(여러 명)로 돌려줘요.
    """
    by_id = repo.people()
    owners = [mapping[label]] if label and mapping.get(label) else []
    if _TOGETHER.search(text):
        for pid in dict.fromkeys(v for v in mapping.values() if v):
            p = by_id.get(pid)
            if p and (p.short in text or p.name in text):
                owners.append(pid)
    return tuple(dict.fromkeys(owners))


def who(label: str | None, mapping: dict[str, str]) -> str:
    """발화자 표기 → 화면에 보일 이름 (매칭 안 됐으면 원래 표기, 발화자 없는 텍스트면 '')."""
    if not label:
        return ""
    pid = mapping.get(label, "")
    return repo.people()[pid].name if pid in repo.people() else label


def _short(text: str, n: int = 26) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "…"


def analyze(parsed: Parsed, mapping: dict[str, str], title: str) -> Analysis:
    lines = parsed.lines
    texts = [ln.text for ln in lines]
    chunks = [lines[i:i + 3] for i in range(0, len(lines), 3)]  # 3문장마다 안건 하나 (데모 규칙)
    agendas = tuple((_short(c[0].text), tuple(x.text for x in c)) for c in chunks)
    def owner_names(ln) -> str:
        ids = todo_owners(ln.text, ln.label, mapping)
        if ids:
            return ", ".join(repo.people()[pid].name for pid in ids)
        return who(ln.label, mapping) or "담당 미정"

    todos = tuple(
        (ln.text, owner_names(ln), find_date_token(ln.text) or "원문 확인 필요")
        for ln in lines if _TODO.search(ln.text)
    )
    decisions = tuple(t for t in texts if _DECISION.search(t))
    open_issues = tuple(t for t in texts if _OPEN.search(t))
    words = [w for t in texts for w in re.findall(r"[가-힣A-Za-z]{2,}", t)]
    words = [re.sub(r"(은|는|이|가|을|를|에|의|도|로|으로|에서|까지|하고)$", "", w) for w in words]
    keywords = tuple(w for w, _ in Counter(w for w in words if len(w) >= 2 and w not in _STOP).most_common(5))
    lead = " ".join(texts[:2])
    summary = f"「{title}」에서 나온 발언 {len(texts)}개를 정리했어요. {lead}"
    transcript = tuple((who(ln.label, mapping), ln.text) for ln in lines)
    return Analysis(summary, agendas, todos, decisions, open_issues, keywords, transcript, dict(mapping))


# ---------------------------------------------------------------- 완성된 회의록 만들기
_WEEKDAYS = "월화수목금토일"


def build_meeting(mid: str, *, title: str, day: date, project_id: str | None, directory_id: str, host: str, people: list[str],
                  parsed: Parsed, mapping: dict[str, str], files: list[str]) -> Meeting:
    """분석 결과로 회의 상세 화면에서 바로 열 수 있는 회의록(Meeting)을 만듦."""
    by_id = repo.people()
    paragraphs: list[Paragraph] = []
    agendas: list[Agenda] = []
    todos: list[Todo] = []
    decisions: list[Evidence] = []
    opens: list[Evidence] = []
    lines = parsed.lines
    for a, i in enumerate(range(0, len(lines), 3)):
        chunk = lines[i:i + 3]
        head = f"h{a + 1}"
        paragraphs.append(Paragraph(head, "", f"[안건 {a + 1}] {_short(chunk[0].text)}"))
        points = []
        for ln in chunk:
            pid = f"p{ln.no}"
            person = mapping.get(ln.label or "", "")
            # 원문 화자: 연결한 참석자 이름(짧은 이름) · 발화자 없는 텍스트면 「발화자」
            speaker = by_id[person].short if person else (ln.label or "발화자")
            paragraphs.append(Paragraph(pid, speaker, ln.text))
            points.append(Evidence(ln.text, (pid,)))
            if _TODO.search(ln.text):
                todos.append(Todo(ln.text, todo_owners(ln.text, ln.label, mapping),
                                  find_date_token(ln.text) or "확인 필요", (pid,)))
            if _DECISION.search(ln.text):
                decisions.append(Evidence(ln.text, (pid,)))
            if _OPEN.search(ln.text):
                opens.append(Evidence(ln.text, (pid,)))
        agendas.append(Agenda(_short(chunk[0].text), "논의됨", "info", False, (head,), tuple(points)))
    texts = [ln.text for ln in lines]
    words = [w for t in texts for w in re.findall(r"[가-힣A-Za-z]{2,}", t)]
    keywords = tuple(w for w, _ in Counter(w for w in words if w not in _STOP).most_common(5))
    first = [p.id for p in paragraphs if p.speaker][:2]
    summary = Evidence(f"「{title}」에서 나온 발언 {len(texts)}개를 정리했어요. " + " ".join(texts[:2]), tuple(first))
    discussion = (  # 요약 탭 「주요 논의 사항」 (빈 줄이 없도록)
        Discussion("의사결정", ", ".join(d.text for d in decisions) or "아직 결정된 것이 없어요",
                   decisions[0].src if decisions else ()),
        Discussion("남은 쟁점", ", ".join(d.text for d in opens) or "남은 쟁점이 없어요", opens[0].src if opens else ()),
        Discussion("다룬 안건", ", ".join(a.title for a in agendas), agendas[0].src if agendas else ()),
    )
    return Meeting(
        id=mid,
        title=title,
        when=f"{day.month}월 {day.day}일 ({_WEEKDAYS[day.weekday()]})",
        date=f"{day.month}/{day.day}",
        held_on=day,
        project_id=project_id,
        directory_id=directory_id,
        host=host,
        people=tuple(dict.fromkeys([host, *people])),
        deep=False,
        time="-",
        file=", ".join(files) or "직접 입력",
        initial_status="review",
        paragraphs=tuple(paragraphs),
        summary=summary,
        discussion=discussion,
        keywords=keywords,
        decisions=tuple(decisions),
        open_issues=tuple(opens),
        agendas=tuple(agendas),
        todos=tuple(todos),
    )