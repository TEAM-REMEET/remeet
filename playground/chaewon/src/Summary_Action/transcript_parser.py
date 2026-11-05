"""회의 전사문 txt 파싱 · 검사 · 참여자 이름 처리

파일 구조
    회의 제목: …                                         ┐ 메타데이터 (config.METADATA_KEYS)
    참여자: 한유리_프로젝트매니저_u1, 홍길동_산업디자이너_u2 … (N명)  │ '이름_직무_userid'
    발화자: 한유리_u1, 홍길동_u2                          │ '이름_userid', 화자를 매핑한 경우에만 있음
    일시: 2005-01-19                                      ┘
    (빈 줄)
    본문

화자 모드 (메타데이터 '발화자' 유무로 결정)
    speaker  '발화자' 있음 → 본문 형식을 파일 단위로 하나로 고정 (첫 발화 줄로 판정)
               inline  홍길동_u2: 발화문     한 줄 = 발화 하나, 첫 ':'(또는 '：') 기준으로 나눔
               block   [홍길동_u2]           '[~]'만 있는 줄 = 화자, 다음 '[~]' 전까지 = 발화문 (여러 줄은 공백으로 합침)
                       발화문
             → 화자를 user_id로 참여자와 대조 (이름 · ID가 다르거나 참여자에 없으면 오류)
             → 구조 · 대조 오류는 모두 모아 TranscriptFormatError (LLM 호출 전에 중단)
    none     '발화자' 없음 → plain: 빈 줄 제외 한 줄 = 발화 하나 (화자 없음, ':'가 있어도 나누지 않음)
             → 본문의 화자 표기가 참여자의 '이름_userid'와 일치하면 '발화자' 줄 누락으로 보고 오류 (LLM 호출 전에 중단)
             → 그 외 화자 표기처럼 보이는 줄이 많으면 경고만 (매핑 누락 의심)

이름 세 가지 (사람은 언제나 user_id로 식별)
    경우                 입력 name    LLM 입력(llm_names)   사용자 표시(display_name)
    동명이인 없음         김철수       김철수                 김철수
    다른 직무의 동명이인   홍길동       홍길동A (코드 부여)     홍길동(산업디자이너)
    같은 직무의 동명이인   홍길동A      홍길동A (실제 이름)     홍길동A
    코드가 부여한 A · B는 LLM 입력 전용이며 저장하지 않음 — 참여자 순서로 항상 같게 계산하고, 저장 전에 표시 이름으로 되돌림 (name_restorer)
"""

import re
from collections.abc import Callable
from datetime import datetime

import config as cfg
from schemas import Person, TranscriptUtterance

_BLOCK_HEADER = re.compile(r"^\[\s*([^\[\]]+?)\s*\]$")     # [홍길동_u2]
_INLINE = re.compile(r"^([^:：]+?)\s*[:：]\s*(.*)$")           # 화자: 발화문 (첫 콜론)
_COUNT = re.compile(r"\(\s*(\d+)\s*명\s*\)\s*$")             # 참여자 값 끝의 (N명)
_SUFFIXED = re.compile(r"(.*[^A-Za-z\s])\s*([A-Z])")         # 홍길동A → ('홍길동', 'A')
_WEEKDAYS = "월화수목금토일"


class TranscriptFormatError(ValueError):
    """전사문 · 참여자 · 발화자 구조 오류 — 오류 목록을 errors에 담음"""

    def __init__(self, title: str, errors: list[str]):
        self.errors = errors
        shown = errors[:cfg.MAX_REPORTED_ERRORS]
        more = f"\n  … 외 {len(errors) - len(shown)}건" if len(errors) > len(shown) else ""
        super().__init__(f"{title} ({len(errors)}건)\n  - " + "\n  - ".join(shown) + more)


def normalize_label(label: str) -> str:
    # 표기 비교용: 공백 제거 ('홍길동 _u2' == '홍길동_u2')
    return re.sub(r"\s+", "", label)


# ── 참여자 ───────────────────────────────────────────────
def split_participant_count(raw: str) -> tuple[str, int | None]:
    # '한유리_PM_u1, 김철수_UI_u3 (2명)' → ('한유리_PM_u1, 김철수_UI_u3', 2)
    m = _COUNT.search(raw)
    return (raw[:m.start()].strip(), int(m.group(1))) if m else (raw.strip(), None)


def parse_participants(raw: str) -> list[Person]:
    # '홍길동_산업디자이너_u2, …' → Person 목록 (형식 · 중복 오류는 LLM 호출 전에 중단)
    body, _ = split_participant_count(raw)
    sep = cfg.PARTICIPANT_SEPARATOR
    parsed, errors = [], []
    for part in (p.strip() for p in body.split(",")):
        name, _, rest = part.partition(sep)
        job, _, user_id = rest.rpartition(sep)
        name, job, user_id = name.strip(), job.strip(), user_id.strip()
        if not (name and job and user_id):
            errors.append(f"'{part}': '이름{sep}직무{sep}userid' 형식이 아님")
            continue
        parsed.append((name, job, user_id))

    seen_ids, seen_people, counts = set(), set(), {}
    for name, job, user_id in parsed:
        person_key = (normalize_label(name), normalize_label(job))
        if user_id in seen_ids:
            errors.append(f"user_id 중복: '{user_id}'")
        if person_key in seen_people:
            errors.append(f"'{name}{sep}{job}'가 여러 명임 — 같은 직무의 동명이인은 이름을 '{name}A', '{name}B'처럼 구분해야 함")
        seen_ids.add(user_id)
        seen_people.add(person_key)
        counts[normalize_label(name)] = counts.get(normalize_label(name), 0) + 1
    if errors:
        raise TranscriptFormatError(f"메타데이터 '참여자' 값 오류: {raw!r}", errors)
    return [Person(user_id=user_id, name=name, job=job,
                   display_name=f"{name}({job})" if counts[normalize_label(name)] > 1 else name)
            for name, job, user_id in parsed]


def llm_names(participants: list[Person]) -> dict[str, str]:
    # {user_id: LLM 입력 이름} — 다른 직무의 동명이인만 코드가 A · B를 붙임 (참여자 순서, 실제 이름과 겹치는 글자는 건너뜀)
    counts: dict[str, int] = {}
    for p in participants:
        counts[normalize_label(p.name)] = counts.get(normalize_label(p.name), 0) + 1
    taken = {normalize_label(p.name) for p in participants}
    names = {}
    for p in participants:
        if counts[normalize_label(p.name)] == 1:
            names[p.user_id] = p.name
            continue
        letter = next((c for c in cfg.ALIAS_LETTERS if normalize_label(p.name + c) not in taken), None)
        if letter is None:
            raise TranscriptFormatError("참여자 구분 표기 오류", [f"'{p.name}' 동명이인이 많아 구분 글자가 부족함"])
        taken.add(normalize_label(p.name + letter))
        names[p.user_id] = p.name + letter
    return names


def format_llm_participants(participants: list[Person]) -> str:
    # LLM 회의 정보: '한유리(프로젝트매니저), 홍길동A(산업디자이너) (2명)' — user_id는 넘기지 않음
    names = llm_names(participants)
    return ", ".join(f"{names[p.user_id]}({p.job})" for p in participants) + f" ({len(participants)}명)"


def _base_name(name: str) -> str:
    m = _SUFFIXED.fullmatch(name)
    return m.group(1) if m else name


def _flexible(text: str) -> str:
    # 정규식: 글자 사이 공백 허용 ('마케팅 담당자' == '마케팅담당자')
    return r"\s*".join(re.escape(ch) for ch in normalize_label(text))


def name_restorer(participants: list[Person]) -> Callable[[str], str]:
    # LLM 출력 문자열의 이름 → 사용자 표시 이름
    #   코드가 붙인 A · B: '홍길동A', '홍길동 A', '홍길동A(산업디자이너)' → '홍길동(산업디자이너)'
    #   실제 이름의 A · B: '홍길동 A' → '홍길동A'
    names = llm_names(participants)
    rules = []
    for p in participants:
        shown = names[p.user_id]
        m = _SUFFIXED.fullmatch(shown)
        if not m:
            continue
        head = rf"(?<![A-Za-z0-9]){_flexible(m.group(1))}"
        if shown != p.name:
            rules.append((re.compile(rf"{head}\s*{m.group(2)}(?![A-Za-z0-9])(?:\s*\(\s*{_flexible(p.job)}\s*\))?"), p.display_name))
        else:
            rules.append((re.compile(rf"{head}\s+{m.group(2)}(?![A-Za-z0-9])"), p.display_name))

    def restore(text: str) -> str:
        for pattern, shown in rules:
            text = pattern.sub(lambda _: shown, text)
        return text

    return restore


def resolve_assignee(value: str | None, participants: list[Person]) -> tuple[Person | None, str | None]:
    # LLM이 쓴 담당자 → (참여자, 문제) — LLM 입력 이름 → 표시 이름 → 이름(직무) 순서로 한 사람을 찾음
    if value is None:
        return None, None
    if not participants:
        return None, None
    key = normalize_label(value)
    names = llm_names(participants)

    def matches(*keys) -> list[Person]:
        found = [p for p in participants if key in {normalize_label(k(p)) for k in keys}]
        return found

    for found in (matches(lambda p: names[p.user_id]),
                  matches(lambda p: p.display_name),
                  matches(lambda p: f"{names[p.user_id]}({p.job})", lambda p: f"{p.name}({p.job})",
                          lambda p: f"{p.name}{cfg.PARTICIPANT_SEPARATOR}{p.job}")):
        if len(found) == 1:
            return found[0], None
    if [p for p in participants if normalize_label(_base_name(p.name)) == key]:
        return None, "동명이인 중 누구인지 구분되지 않는 담당자"
    return None, "참여자 목록에 없는 담당자"


# ── 발화자 ───────────────────────────────────────────────
def parse_speakers(raw: str, participants: list[Person]) -> dict[str, Person]:
    # 메타데이터 '발화자' 값 '한유리_u1, 홍길동_u2' → {'한유리_u1'(공백 제거): 참여자} (형식 · 대조 오류는 중단)
    if not raw.strip():
        raise TranscriptFormatError("메타데이터 '발화자' 값 오류", ["'발화자:' 키는 있으나 값이 비어 있음 "
                                    "(매핑하지 않은 회의록은 '발화자' 줄 자체를 넣지 않음)"])
    if not participants:
        raise TranscriptFormatError("메타데이터 오류", ["'발화자'가 있으면 '참여자'도 있어야 함"])
    by_id = {p.user_id: p for p in participants}
    speakers, errors = {}, []
    for label in (x.strip() for x in raw.split(",")):
        person, problem = _resolve_speaker(label, by_id)
        if problem:
            errors.append(problem)
        elif normalize_label(label) in speakers:
            errors.append(f"발화자 중복: '{label}'")
        else:
            speakers[normalize_label(label)] = person
    if errors:
        raise TranscriptFormatError(f"메타데이터 '발화자' 값 오류: {raw!r}", errors)
    return speakers


def _resolve_speaker(label: str, by_id: dict[str, Person]) -> tuple[Person | None, str | None]:
    sep = cfg.PARTICIPANT_SEPARATOR
    name, found_sep, user_id = label.rpartition(sep)
    name, user_id = name.strip(), user_id.strip()
    if not (found_sep and name and user_id):
        return None, f"'{label}': '이름{sep}userid' 형식이 아님"
    person = by_id.get(user_id)
    if person is None:
        return None, f"'{label}': 참여자에 없는 user_id"
    if normalize_label(person.name) != normalize_label(name):
        return None, f"'{label}': 참여자의 이름과 다름 ({person.name}{sep}{user_id})"
    return person, None


# ── 메타데이터 · 본문 분리 ───────────────────────────────
def split_header(text: str) -> tuple[dict[str, str], list[tuple[int, str]]]:
    # 앞부분의 '키: 값' 줄 중 키가 config.METADATA_KEYS인 줄 = 메타데이터, 나머지 = 본문 (빈 줄 제외, 원본 줄 번호 유지)
    metadata, body = {}, []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not body:
            m = _INLINE.match(line)
            if m and m.group(1).strip() in cfg.METADATA_KEYS:
                metadata[m.group(1).strip()] = m.group(2).strip()
                continue
        body.append((line_no, line))
    return metadata, body


def speaker_raw(metadata: dict[str, str]) -> str | None:
    return next((metadata[k] for k in cfg.SPEAKER_KEYS if k in metadata), None)


def participant_raw(metadata: dict[str, str]) -> str | None:
    return next((metadata[k] for k in cfg.PARTICIPANT_KEYS if k in metadata), None)


def _with_weekday(value: str) -> str:
    # '2004-10-11' → '2004-10-11 (월요일)' / 날짜만 있는 값이 아니면 그대로
    try:
        return f"{value} ({_WEEKDAYS[datetime.strptime(value, '%Y-%m-%d').weekday()]}요일)"
    except ValueError:
        return value


def llm_metadata(metadata: dict[str, str], participants: list[Person]) -> dict[str, str]:
    # LLM 입력용 회의 정보 — 발화자 줄 제외, 참여자는 '이름(직무)' (user_id 없음), 일시에는 요일 추가 (상대적 기한 계산용)
    shown = {k: v for k, v in metadata.items() if k not in cfg.SPEAKER_KEYS}
    for key in cfg.DATETIME_KEYS:
        if key in shown:
            shown[key] = _with_weekday(shown[key])
    for key in cfg.PARTICIPANT_KEYS:
        if key in shown and participants:
            shown[key] = format_llm_participants(participants)
    return shown


# ── 본문 파싱 ────────────────────────────────────────────
def detect_format(first_line: str) -> str | None:
    # speaker 모드에서 첫 발화 줄로 형식 판정 (block / inline / None=구조 오류)
    if _BLOCK_HEADER.match(first_line):
        return "block"
    m = _INLINE.match(first_line)
    if m and len(m.group(1).strip()) <= cfg.MAX_SPEAKER_LEN:
        return "inline"
    return None


def _parse_inline(body: list[tuple[int, str]], errors: list[str]) -> list[dict]:
    raw = []
    for line_no, line in body:
        m = _INLINE.match(line)
        if not m or len(m.group(1).strip()) > cfg.MAX_SPEAKER_LEN:
            errors.append(f"{line_no}행: '이름_userid: 발화문' 형식이 아님 — {line[:30]!r}")
            continue
        if not m.group(2).strip():
            errors.append(f"{line_no}행: 발화문이 비어 있음 — {line[:30]!r}")
            continue
        raw.append({"label": m.group(1).strip(), "text": m.group(2).strip(), "line_number": line_no})
    return raw


def _parse_block(body: list[tuple[int, str]], errors: list[str]) -> list[dict]:
    raw = []
    for line_no, line in body:
        m = _BLOCK_HEADER.match(line)
        if m:
            raw.append({"label": m.group(1).strip(), "lines": [], "line_number": line_no})
        elif raw:
            raw[-1]["lines"].append(line)
        else:
            errors.append(f"{line_no}행: 첫 '[화자]' 줄보다 앞에 있는 본문 — {line[:30]!r}")
    for u in raw:
        u["text"] = " ".join(u.pop("lines"))
        if not u["text"]:
            errors.append(f"{u['line_number']}행: '[{u['label']}]' 다음에 발화문이 없음")
    return [u for u in raw if u["text"]]


def _check_labels(raw: list[dict], speakers: dict[str, Person], errors: list[str], warnings: list[str]) -> None:
    # 본문의 화자 표기를 '발화자'와 대조 → 발화마다 참여자를 연결
    used = set()
    for u in raw:
        key = normalize_label(u["label"])
        person = speakers.get(key)
        if person is None:
            errors.append(f"{u['line_number']}행: '{u['label']}'는 메타데이터 '발화자'에 없음 "
                          f"— 화자 표기 오류, 외부 참석자 또는 여러 줄 발화")
            continue
        u["person"] = person
        used.add(key)
    unused = [k for k in speakers if k not in used]
    if unused:
        warnings.append(f"'발화자'에는 있으나 발화가 없는 화자: {unused}")


def _check_missing_speakers(body: list[tuple[int, str]], participants: list[Person]) -> None:
    # none 모드: 본문의 화자 표기가 참여자의 '이름_userid'와 일치하면 '발화자' 줄이 빠진 것으로 보고 중단
    labels = {normalize_label(f"{p.name}{cfg.PARTICIPANT_SEPARATOR}{p.user_id}") for p in participants}
    found = []
    for line_no, line in body:
        m = _BLOCK_HEADER.match(line) or _INLINE.match(line)
        if m and normalize_label(m.group(1)) in labels:
            found.append(f"{line_no}행: {line[:30]!r}")
    if found:
        raise TranscriptFormatError("메타데이터 '발화자' 줄이 없는데 본문에 화자 표기가 있음", found)


def _warn_speaker_like(body: list[tuple[int, str]], warnings: list[str]) -> None:
    # none 모드: 화자 표기처럼 보이는 줄이 많으면 매핑 누락 의심 경고
    if not body:
        return
    like = sum(1 for _, line in body if _BLOCK_HEADER.match(line)
               or ((m := _INLINE.match(line)) and len(m.group(1).strip()) <= cfg.SPEAKER_LIKE_LABEL_LEN))
    if like / len(body) >= cfg.SPEAKER_LIKE_WARN_RATIO:
        warnings.append(f"'발화자' 메타데이터가 없지만 화자 표기처럼 보이는 줄이 {like}/{len(body)}줄 "
                        f"— 발화자 매핑이 빠졌을 수 있음 (화자 없이 한 줄 = 발화 하나로 처리함)")


def parse_transcript(text: str) -> dict:
    """전사문 txt → {metadata, llm_metadata, speaker_mode, transcript_format, participants, speakers,
    llm_names, transcript, warnings} / 구조 · 대조 오류는 TranscriptFormatError"""
    metadata, body = split_header(text)
    if not any(k in metadata for k in cfg.TITLE_KEYS):      # 메타데이터가 발화로 처리되어 LLM을 호출하는 일을 막음
        raise TranscriptFormatError("메타데이터 오류", [f"첫 줄부터 {cfg.TITLE_KEYS[0]!r} 키가 있어야 함 — 이전 형식 입력인지 확인"])
    warnings: list[str] = []
    raw_participants = participant_raw(metadata)
    participants = parse_participants(raw_participants) if raw_participants else []
    if raw_participants:
        _, count = split_participant_count(raw_participants)
        if count is not None and count != len(participants):
            warnings.append(f"'참여자'의 인원 표기({count}명)와 실제 수({len(participants)}명)가 다름")
    raw_speakers = speaker_raw(metadata)

    if raw_speakers is None:                                    # none 모드
        mode, fmt, speakers = "none", "plain", {}
        _check_missing_speakers(body, participants)
        _warn_speaker_like(body, warnings)
        raw = [{"person": None, "text": line, "line_number": no} for no, line in body]
    else:                                                       # speaker 모드
        mode = "speaker"
        speakers = parse_speakers(raw_speakers, participants)
        if not body:
            raise TranscriptFormatError("전사문 구조 오류", ["본문(발화)이 없음"])
        fmt = detect_format(body[0][1])
        if fmt is None:
            raise TranscriptFormatError("전사문 구조 오류", [
                f"{body[0][0]}행: 첫 발화가 '이름_userid: 발화문' · '[이름_userid]' 형식이 아님 — {body[0][1][:30]!r}"])
        errors: list[str] = []
        raw = _parse_inline(body, errors) if fmt == "inline" else _parse_block(body, errors)
        _check_labels(raw, speakers, errors, warnings)
        if errors:      # 줄 번호 순으로 정렬해서 보여줌
            errors.sort(key=lambda e: int(m.group(1)) if (m := re.match(r"(\d+)행", e)) else 0)
            raise TranscriptFormatError(f"전사문 구조 오류 (형식 {fmt})", errors)

    transcript = [TranscriptUtterance(
        utterance_index=cfg.UTTERANCE_START + i,
        speaker_user_id=u["person"].user_id if u["person"] else None,
        speaker_name=u["person"].name if u["person"] else None,
        speaker_job=u["person"].job if u["person"] else None,
        speaker_display=u["person"].display_name if u["person"] else None,
        text=u["text"], line_number=u["line_number"]) for i, u in enumerate(raw)]
    return {"metadata": metadata, "llm_metadata": llm_metadata(metadata, participants), "speaker_mode": mode,
            "transcript_format": fmt, "participants": participants,
            "speakers": [p.user_id for p in speakers.values()], "llm_names": llm_names(participants),
            "transcript": transcript, "warnings": warnings}
