# """새 회의록 작성: 입력 형식 검사 · 발화자 매칭 · 분석(데모 규칙) · 완성된 회의록 만들기.

# 실제 서비스에서는 AI 서버가 하는 일을, 데모에서는 간단한 규칙으로 흉내 냅니다.

# 지원하는 입력 형식 (한 회의록 안에서는 한 가지만)
#   1) 발화자 없는 텍스트      이번 주에는 로그인 오류를 수정하겠습니다.
#   2) [발화자] 형식           [참석자1] 이번 주에는 로그인 오류를 수정하겠습니다.
#   3) 발화자: 형식            A: 이번 주에는 로그인 오류를 수정하겠습니다.
# 이 밖의 경우(형식을 섞어 씀 · 발화자 표기가 빠진 줄 · 닫히지 않은 [ ] 등)는 오류로 알려 줍니다.
# AI 가 붙인 「참석자1」, 「A」 같은 임시 발화자는 2단계에서 실제 사람과 연결합니다.
# """

# from __future__ import annotations

# import re
# from collections import Counter
# from dataclasses import dataclass, field
# from datetime import date

# from data import repository as repo
# from data.models import Agenda, Discussion, Evidence, Meeting, Paragraph, Todo
# from utils.text import find_date_token

# FORMAT_LABEL = {"plain": "발화자 없는 텍스트", "bracket": "[발화자] 형식", "colon": "발화자: 형식"}

# EXAMPLES = {
#     "plain": (
#         "이번 주에는 로그인 오류를 먼저 수정하기로 했습니다.\n"
#         "디자인 시안은 금요일에 공유할 예정입니다.\n"
#         "수정이 끝나면 QA팀에 테스트를 요청하겠습니다."
#     ),
#     "bracket": (
#         "[참석자1] 이번 주에는 로그인 오류를 수정하겠습니다.\n"
#         "[참석자2] 디자인 시안은 금요일에 공유할게요.\n"
#         "[참석자1] 수정 후 테스트를 요청하겠습니다."
#     ),
#     "colon": (
#         "A: 결제 오류 안내 문구는 이번 주 안에 확정하기로 해요.\n"
#         "B: 그럼 초안은 제가 수요일까지 공유할게요.\n"
#         "A: 좋아요. 고객 문의가 늘었는지도 확인해 주세요?"
#     ),
# }

# _BRACKET = re.compile(r"^\[([^\[\]]{1,20})\]\s*(\S.*)$")
# _COLON = re.compile(r"^([^\s:\[\]][^:\[\]]{0,19}?)\s*:\s*(\S.*)$")
# _TODO = re.compile(r"(하겠습니다|할게요|할께요|하겠어요|해 주세요|해주세요|공유|요청|까지|예정|맡)")
# _DECISION = re.compile(r"(하기로|결정|확정|합의)")
# _OPEN = re.compile(r"(\?|검토|논의가 필요|확인이 필요|미정)")
# _STOP = {"이번", "그럼", "제가", "저는", "우리", "그리고", "그래서", "좋아요", "수정", "있는지도", "다음", "먼저"}


# @dataclass(frozen=True)
# class Line:
#     no: int  # 몇 번째 줄 (1부터)
#     label: str | None  # 원문 발화자 표기 (없으면 None)
#     text: str


# @dataclass(frozen=True)
# class Parsed:
#     format: str | None  # plain | bracket | colon (오류면 None)
#     lines: tuple[Line, ...] = ()
#     labels: tuple[str, ...] = ()  # 나온 순서대로 발화자 표기
#     error: str = ""  # 오류 안내 (없으면 빈 값)
#     error_lines: tuple[int, ...] = ()


# def _kind(line: str) -> tuple[str, str | None, str]:
#     m = _BRACKET.match(line)
#     if m:
#         return "b", m.group(1).strip(), m.group(2).strip()
#     m = _COLON.match(line)
#     if m and not m.group(1).strip().isdigit():  # 10:30 같은 시간은 발화자가 아님
#         return "c", m.group(1).strip(), m.group(2).strip()
#     return "p", None, line


# def parse(text: str) -> Parsed:
#     """입력 형식 검사. 지원하지 않는 형식이면 error 에 이유를 적어 돌려 줌."""
#     raw = [(i + 1, ln.strip()) for i, ln in enumerate((text or "").splitlines())]
#     raw = [(no, ln) for no, ln in raw if ln]
#     if not raw:
#         return Parsed(None, error="회의 내용을 적거나 TXT · MD 파일을 첨부해 주세요.")
#     if sum(len(ln) for _, ln in raw) < 10:
#         return Parsed(None, error="내용이 너무 짧아요. 회의에서 나눈 말을 한 문장 이상 적어 주세요.")

#     kinds = [(no, *_kind(ln)) for no, ln in raw]
#     used = {k for _, k, _, _ in kinds}
#     if used == {"b", "c"} or (len(used) == 3):
#         bad = tuple(no for no, k, _, _ in kinds if k == "c") if "b" in used else ()
#         return Parsed(None, error="[발화자] 형식과 발화자: 형식을 한 회의록에 섞어 쓸 수 없어요. 한 가지로 맞춰 주세요.",
#                       error_lines=bad)
#     if "p" in used and len(used) > 1:
#         bad = tuple(no for no, k, _, _ in kinds if k == "p")
#         return Parsed(None, error="발화자 표기가 빠진 줄이 있어요. 모든 줄에 발화자를 붙이거나, 발화자 없이 적어 주세요.",
#                       error_lines=bad)
#     if used == {"p"}:
#         bad = tuple(no for no, ln in raw if ln.startswith("[") and "]" not in ln)
#         if bad:
#             return Parsed(None, error="[ ] 가 닫히지 않은 줄이 있어요. [발화자] 내용 처럼 적어 주세요.", error_lines=bad)
#         return Parsed("plain", tuple(Line(no, None, ln) for no, ln in raw))

#     fmt = "bracket" if used == {"b"} else "colon"
#     lines = tuple(Line(no, label, body) for no, _, label, body in kinds)
#     labels = tuple(dict.fromkeys(ln.label for ln in lines if ln.label))
#     return Parsed(fmt, lines, labels)


# def auto_match(label: str) -> str:
#     """발화자 표기가 실제 사람 이름(짧은 이름·전체 이름)과 같으면 그 사람을 미리 골라 둠."""
#     people = repo.people()
#     for pid, p in people.items():
#         if label in (p.short, p.name):
#             return pid
#     return ""


# # ---------------------------------------------------------------- 분석 (데모 규칙)
# @dataclass(frozen=True)
# class Analysis:
#     summary: str
#     agendas: tuple[tuple[str, tuple[str, ...]], ...]  # (안건 제목, 그 안건의 발언들)
#     todos: tuple[tuple[str, str, str], ...]  # (할 일, 담당자 이름 또는 표기, 기한)
#     decisions: tuple[str, ...]
#     open_issues: tuple[str, ...]
#     keywords: tuple[str, ...]
#     transcript: tuple[tuple[str, str], ...]  # (보이는 발화자 이름, 발언) · 매칭 반영
#     mapping: dict[str, str] = field(default_factory=dict)  # 발화자 표기 → 사람 id ('' 면 미지정)


# def who(label: str | None, mapping: dict[str, str]) -> str:
#     """발화자 표기 → 화면에 보일 이름 (매칭 안 됐으면 원래 표기, 발화자 없는 텍스트면 '')."""
#     if not label:
#         return ""
#     pid = mapping.get(label, "")
#     return repo.people()[pid].name if pid else label


# def _short(text: str, n: int = 26) -> str:
#     return text if len(text) <= n else text[:n].rstrip() + "…"


# def analyze(parsed: Parsed, mapping: dict[str, str], title: str) -> Analysis:
#     lines = parsed.lines
#     texts = [ln.text for ln in lines]
#     chunks = [lines[i:i + 3] for i in range(0, len(lines), 3)]  # 3문장마다 안건 하나 (데모 규칙)
#     agendas = tuple((_short(c[0].text), tuple(x.text for x in c)) for c in chunks)
#     todos = tuple(
#         (ln.text, who(ln.label, mapping) or "담당 미정", find_date_token(ln.text) or "원문 확인 필요")
#         for ln in lines if _TODO.search(ln.text)
#     )
#     decisions = tuple(t for t in texts if _DECISION.search(t))
#     open_issues = tuple(t for t in texts if _OPEN.search(t))
#     words = [w for t in texts for w in re.findall(r"[가-힣A-Za-z]{2,}", t)]
#     words = [re.sub(r"(은|는|이|가|을|를|에|의|도|로|으로|에서|까지|하고)$", "", w) for w in words]
#     keywords = tuple(w for w, _ in Counter(w for w in words if len(w) >= 2 and w not in _STOP).most_common(5))
#     lead = " ".join(texts[:2])
#     summary = f"「{title}」에서 나온 발언 {len(texts)}개를 정리했어요. {lead}"
#     transcript = tuple((who(ln.label, mapping), ln.text) for ln in lines)
#     return Analysis(summary, agendas, todos, decisions, open_issues, keywords, transcript, dict(mapping))


# # ---------------------------------------------------------------- 완성된 회의록 만들기
# _WEEKDAYS = "월화수목금토일"


# def build_meeting(mid: str, *, title: str, day: date, topic: str | None, host: str, people: list[str],
#                   parsed: Parsed, mapping: dict[str, str], files: list[str]) -> Meeting:
#     """분석 결과로 회의 상세 화면에서 바로 열 수 있는 회의록(Meeting)을 만듦."""
#     by_id = repo.people()
#     paragraphs: list[Paragraph] = []
#     agendas: list[Agenda] = []
#     todos: list[Todo] = []
#     decisions: list[Evidence] = []
#     opens: list[Evidence] = []
#     lines = parsed.lines
#     for a, i in enumerate(range(0, len(lines), 3)):
#         chunk = lines[i:i + 3]
#         head = f"h{a + 1}"
#         paragraphs.append(Paragraph(head, "", f"[안건 {a + 1}] {_short(chunk[0].text)}"))
#         points = []
#         for ln in chunk:
#             pid = f"p{ln.no}"
#             person = mapping.get(ln.label or "", "")
#             # 원문 화자: 연결한 사람은 그 사람 이름(짧은 이름), 아니면 원래 표기 → 화면에서 「미지정」
#             speaker = by_id[person].short if person else (ln.label or "발화자")
#             paragraphs.append(Paragraph(pid, speaker, ln.text))
#             points.append(Evidence(ln.text, (pid,)))
#             if _TODO.search(ln.text):
#                 todos.append(Todo(ln.text, person or None, find_date_token(ln.text) or "확인 필요", (pid,)))
#             if _DECISION.search(ln.text):
#                 decisions.append(Evidence(ln.text, (pid,)))
#             if _OPEN.search(ln.text):
#                 opens.append(Evidence(ln.text, (pid,)))
#         agendas.append(Agenda(_short(chunk[0].text), "논의됨", "info", False, (head,), tuple(points)))
#     texts = [ln.text for ln in lines]
#     words = [w for t in texts for w in re.findall(r"[가-힣A-Za-z]{2,}", t)]
#     keywords = tuple(w for w, _ in Counter(w for w in words if w not in _STOP).most_common(5))
#     first = [p.id for p in paragraphs if p.speaker][:2]
#     summary = Evidence(f"「{title}」에서 나온 발언 {len(texts)}개를 정리했어요. " + " ".join(texts[:2]), tuple(first))
#     discussion = (  # 요약 탭 「주요 논의 사항」 (빈 줄이 없도록)
#         Discussion("의사결정", ", ".join(d.text for d in decisions) or "아직 결정된 것이 없어요",
#                    decisions[0].src if decisions else ()),
#         Discussion("남은 쟁점", ", ".join(d.text for d in opens) or "남은 쟁점이 없어요", opens[0].src if opens else ()),
#         Discussion("다룬 안건", ", ".join(a.title for a in agendas), agendas[0].src if agendas else ()),
#     )
#     return Meeting(
#         id=mid,
#         title=title,
#         when=f"{day.month}월 {day.day}일 ({_WEEKDAYS[day.weekday()]})",
#         date=f"{day.month}/{day.day}",
#         topic=topic,
#         host=host,
#         people=tuple(dict.fromkeys([host, *people])),
#         deep=False,
#         time="-",
#         file=", ".join(files) or "직접 입력",
#         initial_status="review",
#         paragraphs=tuple(paragraphs),
#         summary=summary,
#         discussion=discussion,
#         keywords=keywords,
#         decisions=tuple(decisions),
#         open_issues=tuple(opens),
#         agendas=tuple(agendas),
#         todos=tuple(todos),
#     )


"""새 회의록 작성: 입력 형식 검사 · 발화자 매칭 · 분석(데모 규칙) · 완성된 회의록 만들기.

실제 서비스에서는 AI 서버가 하는 일을, 데모에서는 간단한 규칙으로 흉내 냅니다.

지원하는 입력 형식 (한 회의록 안에서는 한 가지만)
  1) 발화자 없는 텍스트      이번 주에는 로그인 오류를 수정하겠습니다.
  2) [발화자] 형식           [참석자1]
                             이번 주에는 로그인 오류를 수정하겠습니다.   (발화자 줄 다음 줄이 발언 · 한 줄에 같이 쓰면 오류)
  3) 발화자: 형식            A: 이번 주에는 로그인 오류를 수정하겠습니다.
이 밖의 경우(형식을 섞어 씀 · 발화자 표기가 빠진 줄 · [발화자] 내용을 한 줄에 씀 · [발화자] 다음에 발언이 없음 · 닫히지 않은 [ ] 등)는 오류로 알려 줍니다.
AI 가 붙인 「참석자1」, 「A」 같은 임시 발화자는 2단계에서 실제 사람과 연결합니다.
"""

from __future__ import annotations

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
    "plain": "발화자 표기 없이 문장만 적어 주세요. [참석자1] · A: 같은 발화자 표기를 넣으면 안 돼요.",
    "bracket": "[발화자]만 한 줄에 쓰고, 그다음 줄에 발언을 적어 주세요. [발화자]와 발언을 한 줄에 쓰면 안 돼요.",
    "colon": "모든 줄을 「발화자: 발언」으로 적어 주세요. 발화자 표기가 빠진 줄이 있으면 안 돼요.",
}

EXAMPLES = {
    "plain": (
        "이번 주에는 로그인 오류를 먼저 수정하기로 했습니다.\n"
        "디자인 시안은 금요일에 공유할 예정입니다.\n"
        "수정이 끝나면 QA팀에 테스트를 요청하겠습니다."
    ),
    "bracket": (
        "[참석자1]\n이번 주에는 로그인 오류를 수정하겠습니다.\n\n"
        "[참석자2]\n디자인 시안은 금요일에 공유할게요.\n\n"
        "[참석자1]\n수정 후 테스트를 요청하겠습니다."
    ),
    "colon": (
        "A: 결제 오류 안내 문구는 이번 주 안에 확정하기로 해요.\n"
        "B: 그럼 초안은 제가 수요일까지 공유할게요.\n"
        "A: 좋아요. 고객 문의가 늘었는지도 확인해 주세요?"
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
    """[발화자] 형식: [발화자]만 있는 줄 다음 줄(들)이 그 사람의 발언. 빈 줄은 블록 사이 구분용이라 무시.

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


def parse(text: str) -> Parsed:
    """입력 형식 검사. 지원하지 않는 형식이면 error 에 이유를 적어 돌려 줌."""
    raw = [(i + 1, ln.strip()) for i, ln in enumerate((text or "").splitlines())]
    raw = [(no, ln) for no, ln in raw if ln]
    if not raw:
        return Parsed(None, error="회의 내용을 적거나 TXT · MD 파일을 첨부해 주세요.")
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


def auto_match(label: str) -> str:
    """발화자 표기가 실제 사람 이름(짧은 이름·전체 이름)과 같으면 그 사람을 미리 골라 둠."""
    people = repo.people()
    for pid, p in people.items():
        if label in (p.short, p.name):
            return pid
    return ""


# ---------------------------------------------------------------- 분석 (데모 규칙)
@dataclass(frozen=True)
class Analysis:
    summary: str
    agendas: tuple[tuple[str, tuple[str, ...]], ...]  # (안건 제목, 그 안건의 발언들)
    todos: tuple[tuple[str, str, str], ...]  # (할 일, 담당자 이름 또는 표기, 기한)
    decisions: tuple[str, ...]
    open_issues: tuple[str, ...]
    keywords: tuple[str, ...]
    transcript: tuple[tuple[str, str], ...]  # (보이는 발화자 이름, 발언) · 매칭 반영
    mapping: dict[str, str] = field(default_factory=dict)  # 발화자 표기 → 사람 id ('' 면 미지정)


def who(label: str | None, mapping: dict[str, str]) -> str:
    """발화자 표기 → 화면에 보일 이름 (매칭 안 됐으면 원래 표기, 발화자 없는 텍스트면 '')."""
    if not label:
        return ""
    pid = mapping.get(label, "")
    return repo.people()[pid].name if pid else label


def _short(text: str, n: int = 26) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "…"


def analyze(parsed: Parsed, mapping: dict[str, str], title: str) -> Analysis:
    lines = parsed.lines
    texts = [ln.text for ln in lines]
    chunks = [lines[i:i + 3] for i in range(0, len(lines), 3)]  # 3문장마다 안건 하나 (데모 규칙)
    agendas = tuple((_short(c[0].text), tuple(x.text for x in c)) for c in chunks)
    todos = tuple(
        (ln.text, who(ln.label, mapping) or "담당 미정", find_date_token(ln.text) or "원문 확인 필요")
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


def build_meeting(mid: str, *, title: str, day: date, topic: str | None, host: str, people: list[str],
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
            # 원문 화자: 연결한 사람은 그 사람 이름(짧은 이름), 아니면 원래 표기 → 화면에서 「미지정」
            speaker = by_id[person].short if person else (ln.label or "발화자")
            paragraphs.append(Paragraph(pid, speaker, ln.text))
            points.append(Evidence(ln.text, (pid,)))
            if _TODO.search(ln.text):
                todos.append(Todo(ln.text, person or None, find_date_token(ln.text) or "확인 필요", (pid,)))
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
        topic=topic,
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
