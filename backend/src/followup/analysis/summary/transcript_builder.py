"""POST /api/meetings 입력 → 요약 · 액션 아이템 추출 입력 텍스트 (DB · LLM 접근 없음)

참석자는 웹이 GET /api/people에서 받은 값을 그대로 보내므로 Neo4j를 조회하지 않음.
meeting_info.participants는 attendees로 채움 — 주관자와 연결된 발화자도 attendees에 있어야 함.

결과 구조
    회의 제목: …
    참여자: 이름_직무_userid, … (N명)
    발화자: 이름_userid, …        ← 발화자 있는 형식만
    일시: YYYY-MM-DD
    (빈 줄)
    이름_userid: 발화문           ← 발화자 없는 형식은 발화문만

입력 형식 (apps/web/state/compose_ai.py의 규칙과 같음)
    plain    빈 줄 제외 한 줄 = 발화 하나
    bracket  '[표기]'만 있는 줄 + 다음 줄 발화문 하나
    colon    '표기: 발화문'
발화자 표기는 attendees의 name 또는 '이름(직무)'와 같아야 하며, 그 사람의 user_id · job을 사용함.
    같은 직무의 동명이인  이름 자체가 다름 (홍길동A · 홍길동B) → '홍길동A: 발화문'
    다른 직무의 동명이인  이름이 같음 → '홍길동(마케팅 담당자): 발화문' (LLM 입력의 A · B는 파이프라인이 부여)
참석자에 없는 표기 · 한 사람으로 정해지지 않는 표기는 허용하지 않음.
오류는 모두 모아 TranscriptFormatError로 알림 (LLM 호출 전에 중단).
"""

import hashlib
import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from followup import settings as cfg
from followup.analysis.summary.preprocessing import TranscriptFormatError, normalize_label, parse_transcript
from followup.domain.ulid import new_ulid

_BRACKET_HEADER = re.compile(r"^\[\s*([^\[\]]+?)\s*\]$")        # [참석자1]
_BRACKET_INLINE = re.compile(r"^\[[^\[\]]+\]\s*\S")             # [참석자1] 발화문 (한 줄에 쓰면 오류)
_COLON = re.compile(r"^([^\s:\[\]][^:\[\]]*?)\s*:\s*(\S.*)$")   # 표기: 발화문 (첫 콜론 기준)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Attendee(BaseModel):
    """GET /api/people의 한 사람 (team 등 다른 값이 함께 와도 무시)"""
    model_config = ConfigDict(frozen=True)
    user_id: NonEmpty
    name: NonEmpty
    job: NonEmpty


class MeetingSubmission(BaseModel):
    """POST /api/meetings 요청"""
    model_config = ConfigDict(extra="forbid")
    project_id: NonEmpty | None = None          # 기존 폴더의 ID (없으면 새 폴더로 보고 백엔드가 만듦)
    directory_id: Literal["monthly", "weekly", "normal"]    # 회의 종류: 월간 · 주간 · 일반
    title: NonEmpty
    date: date
    host_user_id: NonEmpty                      # 주관자 — 입력 텍스트에는 넣지 않고 결과 JSON에만 저장
    attendees: list[Attendee] = Field(min_length=1)
    transcript_format: Literal["plain", "bracket", "colon"]
    transcript: str


def new_project_id() -> str:
    return f"P_{new_ulid()}"       # P_01JC8Z… (26자 ULID)


def new_meeting_id() -> str:
    return f"M_{new_ulid()}"       # M_01JC8Z… (26자 ULID)


def submission_key(submission: MeetingSubmission) -> str:
    # 요청 내용별 키 — 실패 후 같은 요청을 다시 보낼 때 같은 회의 · 프로젝트 ID를 찾는 데 씀
    return hashlib.sha256(submission.model_dump_json().encode("utf-8")).hexdigest()[:32]


def _lines(text: str) -> list[tuple[int, str]]:
    # (원본 줄 번호, 내용) — 빈 줄 제외 / 파일을 이어 붙일 때 중간에 남을 수 있는 BOM 제거
    stripped = ((no, line.replace("\ufeff", "").strip()) for no, line in enumerate(text.splitlines(), 1))
    return [(no, line) for no, line in stripped if line]


def _labeled_colon(lines: list[tuple[int, str]], errors: list[str]) -> list[tuple[int, str, str]]:
    labeled = []
    for no, line in lines:
        if m := _COLON.match(line):
            labeled.append((no, m.group(1), m.group(2)))
        else:
            errors.append(f"{no}행: '표기: 발화문' 형식이 아님 — {line[:30]!r}")
    return labeled


def _labeled_bracket(lines: list[tuple[int, str]], errors: list[str]) -> list[tuple[int, str, str]]:
    labeled = []
    label, label_no, has_text = None, 0, True
    for no, line in lines:
        if m := _BRACKET_HEADER.match(line):
            if not has_text:
                errors.append(f"{label_no}행: '[{label}]' 다음에 발화문이 없음")
            label, label_no, has_text = m.group(1), no, False
        elif _BRACKET_INLINE.match(line):
            errors.append(f"{no}행: '[표기]'와 발화문을 한 줄에 쓸 수 없음 — {line[:30]!r}")
        elif label is None:
            errors.append(f"{no}행: 첫 '[표기]' 줄보다 앞에 있는 발화문 — {line[:30]!r}")
        elif has_text:
            errors.append(f"{no}행: 한 발화를 여러 줄로 쓸 수 없음 — {line[:30]!r}")
        else:
            labeled.append((no, label, line))
            has_text = True
    if not has_text:
        errors.append(f"{label_no}행: '[{label}]' 다음에 발화문이 없음")
    return labeled


def _body(submission: MeetingSubmission, by_id: dict[str, Attendee]) -> list[tuple[str | None, str]]:
    # 전사문 → [(화자 user_id, 발화문)] (plain은 화자 None)
    lines, errors = _lines(submission.transcript), []
    if not lines:
        raise TranscriptFormatError("전사문 구조 오류", ["전사문이 비어 있음"])
    if submission.transcript_format == "plain":
        return [(None, line) for _, line in lines]

    read = _labeled_colon if submission.transcript_format == "colon" else _labeled_bracket
    speakers: dict[str, list[str]] = {}        # 표기(공백 제거) → user_id
    for p in by_id.values():
        for key in (p.name, f"{p.name}({p.job})"):
            speakers.setdefault(normalize_label(key), []).append(p.user_id)
    body = []
    for no, label, text in read(lines, errors):
        matched = speakers.get(normalize_label(label), [])
        if not matched:
            errors.append(f"{no}행: '{label}'이(가) 참석자 목록에 없음")
        elif len(matched) > 1:
            errors.append(f"{no}행: '{label}'이(가) 여러 명이라 구분할 수 없음 — '이름(직무)'로 적어 주세요")
        else:
            body.append((matched[0], text))
    if errors:
        raise TranscriptFormatError(f"전사문 구조 오류 (형식 {submission.transcript_format})", errors)
    return body


def _unsafe_field(person: Attendee) -> str | None:
    # 구분자가 값에 들어가면 입력 텍스트를 다시 읽을 때 사람이 잘못 나뉨
    sep = cfg.PARTICIPANT_SEPARATOR
    if sep in person.name or sep in person.user_id:
        return f"이름 · user_id에 '{sep}'를 쓸 수 없음"
    if any(ch in value for value in (person.name, person.job, person.user_id) for ch in ",\r\n"):
        return "이름 · 직무 · user_id에 쉼표나 줄바꿈을 쓸 수 없음"
    if any(ch in person.name + person.user_id for ch in ":：[]"):
        return "이름 · user_id에 콜론이나 대괄호를 쓸 수 없음"
    if len(f"{person.name}{sep}{person.user_id}") > cfg.MAX_SPEAKER_LEN:
        return f"'이름{sep}userid'가 {cfg.MAX_SPEAKER_LEN}자를 넘음"
    return None


def _check_people(submission: MeetingSubmission, by_id: dict[str, Attendee]) -> None:
    errors = [f"'{p.user_id}': {problem}" for p in submission.attendees if (problem := _unsafe_field(p))]
    if submission.host_user_id not in by_id:
        errors.append(f"주관자 '{submission.host_user_id}'이(가) 참석자 목록에 없음")
    if any(ch in submission.title for ch in "\r\n"):
        errors.append("회의 제목에 줄바꿈을 쓸 수 없음")
    if errors:
        raise TranscriptFormatError("입력 오류", errors)


def build_meeting_text(submission: MeetingSubmission) -> str:
    """요청 → 파이프라인 입력 텍스트 (조립 후 파이프라인 파서로 다시 검사)"""
    length = len(submission.transcript.strip())
    if length > cfg.MAX_TRANSCRIPT_CHARS:       # 너무 길면 처리 시간 · 비용이 커져서 LLM 호출 전에 막음
        raise TranscriptFormatError("입력 오류", [f"회의 내용이 너무 길어요 ({length:,}자 / 최대 {cfg.MAX_TRANSCRIPT_CHARS:,}자) — "
                                                 "회의를 나눠서 올려 주세요."])
    by_id = {p.user_id: p for p in submission.attendees}
    _check_people(submission, by_id)
    body = _body(submission, by_id)

    sep = cfg.PARTICIPANT_SEPARATOR
    label = {p.user_id: f"{p.name}{sep}{p.user_id}" for p in submission.attendees}
    speakers = list(dict.fromkeys(label[user_id] for user_id, _ in body if user_id))
    header = [f"{cfg.TITLE_KEYS[0]}: {submission.title}",
              f"{cfg.PARTICIPANT_KEYS[0]}: " + ", ".join(f"{p.name}{sep}{p.job}{sep}{p.user_id}" for p in submission.attendees)
              + f" ({len(submission.attendees)}명)"]
    if speakers:
        header.append(f"{cfg.SPEAKER_KEYS[0]}: " + ", ".join(speakers))
    header.append(f"{cfg.DATETIME_KEYS[0]}: {submission.date.isoformat()}")
    lines = [f"{label[user_id]}: {text}" if user_id else text for user_id, text in body]
    text = "\n".join(header) + "\n\n" + "\n".join(lines) + "\n"

    parsed = parse_transcript(text)
    if len(parsed["transcript"]) != len(body):      # 변환으로 발화가 사라지거나 늘어나면 중단
        raise TranscriptFormatError("입력 변환 오류", [f"발화 수가 달라짐 ({len(body)}개 → {len(parsed['transcript'])}개)"])
    return text
