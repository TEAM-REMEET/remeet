"""입력 읽기 · 1-1 후처리 (근거 발화 검사 → ID 부여 → 최소 단위 저장 구조)

load_input: 회의 전사문 txt → 메타데이터 + 발화 목록 ('화자: 발화문' 한 줄 = 발화 하나, 번호는 config.UTTERANCE_START부터)
build_summary: LLM 출력(LLMMeetingSummary, ID 없음) → MeetingSummary
    ① 근거 발화 검사 (항목별)
       - 전사문에 없는 번호 제외 · 중복 제거 · 오름차순 정렬 (제외 내용은 history · postprocess_log)
       - 유효한 근거가 하나도 없으면 status=REVIEW_REQUIRED
    ② 태그 검사 (주요 논의)
       - 액션: decision_tag를 '결정'으로 고정
       - 액션이 아닌 항목: assignee · due를 null로 (값이 있었으면 기록)
       - assignee · due의 빈 문자열 · '-'는 null
    ③ ID 부여
       - 안건: {회의 ID}_a{n} / 소주제: {안건 ID}_st{n} / 항목: {안건 ID}_{bg|d|un|next}{n}
       - 문장이 빈 항목 · 항목이 없는 안건은 만들지 않음 (기록)
    ④ 안건 목차 · 회의 결과 (item_id 참조)
       - 결정 수: decision_tag == '결정'이고 액션이 아닌 항목 (보류 · 공유 · [액션][결정] 제외)
"""

import re
from datetime import datetime
from pathlib import Path

import config as cfg
from tracing import traceable
from schemas import (ACTION_DECISION, SECTIONS, Agenda, AgendaIndexEntry, BackgroundItem,
                     DiscussionItem, ItemRevision, LLMMeetingSummary, MeetingInfo, MeetingResults, MeetingSummary,
                     NextTopicItem, PostprocessLog, SourceDocument, Subtopic, TranscriptUtterance, UnresolvedItem,
                     make_agenda_id, make_item_id, make_subtopic_id)

# 사용자 설정: config.py — DEFAULT_SUBTOPIC · EMPTY_VALUES · TITLE_KEYS · DATETIME_KEYS · PARTICIPANT_KEYS

STAGE = "postprocess"


# ── 입력 ──────────────────────────────────────────────
def _split_key_value(line: str) -> tuple[str, str] | None:
    # '키: 값' → (키, 값) / 콜론이 없거나 키가 너무 길면 None
    key, sep, value = line.partition(":")
    key = key.strip()
    if not sep or not key or len(key) > cfg.MAX_SPEAKER_LEN:
        return None
    return key, value.strip()


def parse_speakers(raw: str | None) -> dict[str, str]:
    # 'A=프로젝트 매니저, C=산업 디자이너' → {"A": "프로젝트 매니저", "C": "산업 디자이너"}
    speakers = {}
    for part in (raw or "").split(","):
        label, sep, role = part.partition("=")
        if sep and label.strip() and role.strip():
            speakers[label.strip()] = role.strip()
    return speakers


def parse_transcript(text: str) -> tuple[dict[str, str], list[TranscriptUtterance], list[str]]:
    # 회의 전사문 txt → (메타데이터, 발화 목록, 경고)
    #   앞부분의 '키: 값' 줄 중 키가 config.METADATA_KEYS인 줄 = 메타데이터
    #   그 뒤 '화자: 내용' 한 줄 = 발화 하나 (빈 줄 무시) / 형식이 다른 줄은 앞 발화에 이어 붙임
    metadata, raw_utts, warnings = {}, [], []
    in_header = True
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        kv = _split_key_value(line)
        if in_header and kv and kv[0] in cfg.METADATA_KEYS:
            metadata[kv[0]] = kv[1]
            continue
        in_header = False
        if kv:
            raw_utts.append({"speaker": kv[0], "text": kv[1], "line_number": line_no})
        elif raw_utts:
            raw_utts[-1]["text"] = f"{raw_utts[-1]['text']} {line}".strip()
            warnings.append(f"{line_no}행: '화자: 내용' 형식이 아님 → 앞 발화에 이어 붙임")
        else:
            warnings.append(f"{line_no}행: 화자 없는 줄 (첫 발화 전) → 제외")

    speakers = parse_speakers(next((metadata[k] for k in cfg.SPEAKER_KEYS if k in metadata), None))
    transcript = []
    for u in raw_utts:
        if not u["text"]:
            warnings.append(f"{u['line_number']}행: 내용이 빈 발화 → 제외")
            continue
        transcript.append(TranscriptUtterance(
            utterance_index=cfg.UTTERANCE_START + len(transcript), speaker=u["speaker"],
            speaker_role=speakers.get(u["speaker"]), text=u["text"], line_number=u["line_number"]))
    unknown = sorted({u.speaker for u in transcript if speakers and u.speaker not in speakers})
    if unknown:
        warnings.append(f"'발화자' 메타데이터에 없는 화자: {unknown}")
    return metadata, transcript, warnings


@traceable(name="load_input")
def load_input(input_path: str | Path) -> dict:
    input_path = Path(input_path)
    metadata, transcript, warnings = parse_transcript(input_path.read_text(encoding=cfg.INPUT_ENCODING))
    if not transcript:
        raise ValueError(f"발화가 없음: {input_path}")
    return {"meeting_id": input_path.stem, "metadata": metadata, "transcript": transcript,
            "input_path": str(input_path), "warnings": warnings}


def _pick(metadata: dict[str, str], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    key = next((k for k in keys if k in metadata), None)
    return key, (metadata[key].strip() if key else None)


def parse_meeting_info(metadata: dict[str, str]) -> MeetingInfo:
    title_key, title = _pick(metadata, cfg.TITLE_KEYS)
    dt_key, dt = _pick(metadata, cfg.DATETIME_KEYS)
    pt_key, pt = _pick(metadata, cfg.PARTICIPANT_KEYS)
    sp_key, sp = _pick(metadata, cfg.SPEAKER_KEYS)

    date = duration = None
    if dt:
        m = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", dt)
        date = m.group(0) if m else re.sub(r"\(.*?\)", "", dt).strip() or None
        m = re.search(r"\((.*?)\)", dt)
        duration = m.group(1).strip() if m else None

    participants, count = [], None
    if pt:
        m = re.search(r"\(\s*(\d+)\s*명\s*\)", pt)
        count = int(m.group(1)) if m else None
        participants = [p.strip() for p in re.sub(r"\(.*?\)", "", pt).split(",") if p.strip()]
    if count is None and participants:
        count = len(participants)

    used = {title_key, dt_key, pt_key, sp_key}
    return MeetingInfo(title=title, date=date, duration=duration, datetime_raw=dt, participants=participants,
                       participant_count=count, participants_raw=pt, speakers=parse_speakers(sp), speakers_raw=sp,
                       extra={k: v for k, v in metadata.items() if k not in used})


# ── ① 근거 발화 검사 ──────────────────────────────────
def check_evidence(indices: list[int], valid: set[int], item_id: str, log: list[PostprocessLog]
                   ) -> tuple[list[int], list[ItemRevision], list[str]]:
    # (유효한 근거, 이력, REVIEW_REQUIRED 사유)
    cleaned = sorted({i for i in indices if i in valid})
    history, reasons = [], []
    invalid = sorted({i for i in indices if i not in valid})
    if invalid:
        msg = f"전사문에 없는 근거 발화 번호 {invalid} 제외"
        history.append(ItemRevision(stage=STAGE, field="evidence_utterance_indices", before=list(indices),
                                    after=cleaned, reason=msg))
        log.append(PostprocessLog(item_id=item_id, message=msg))
    if not cleaned:
        reasons.append("유효한 근거 발화 없음")
        log.append(PostprocessLog(item_id=item_id, message="유효한 근거 발화 없음 → REVIEW_REQUIRED"))
    return cleaned, history, reasons


def _empty_to_none(value: str | None) -> str | None:
    return None if value is None or value.strip().lower() in cfg.EMPTY_VALUES else value.strip()


def _base(item_id: str, agenda_id: str, order: int, text: str, indices: list[int], valid: set[int],
          log: list[PostprocessLog]) -> dict:
    evidence, history, reasons = check_evidence(indices, valid, item_id, log)
    return {"item_id": item_id, "agenda_id": agenda_id, "order": order, "text": text.strip(),
            "evidence_utterance_indices": evidence, "history": history, "review_reasons": reasons,
            "status": "REVIEW_REQUIRED" if reasons else "ACTIVE"}


# ── ②③ 안건 하나 ─────────────────────────────────────
def build_agenda(llm_agenda, agenda_id: str, number: int, valid: set[int], log: list[PostprocessLog]) -> Agenda | None:
    sections = {name: [] for name in SECTIONS}

    def next_id(section: str) -> tuple[str, int]:
        order = len(sections[section]) + 1
        return make_item_id(agenda_id, section, order), order

    def skip_empty(section: str, text: str) -> bool:
        if text.strip():
            return False
        log.append(PostprocessLog(item_id=None, message=f"{agenda_id} {section}: 문장이 빈 항목 제외"))
        return True

    for it in llm_agenda.discussion_background:
        if skip_empty("discussion_background", it.text):
            continue
        item_id, order = next_id("discussion_background")
        sections["discussion_background"].append(
            BackgroundItem(**_base(item_id, agenda_id, order, it.text, it.evidence_utterance_indices, valid, log)))

    subtopics: dict[str, Subtopic] = {}
    for it in llm_agenda.major_discussions:
        if skip_empty("major_discussions", it.text):
            continue
        item_id, order = next_id("major_discussions")
        base = _base(item_id, agenda_id, order, it.text, it.evidence_utterance_indices, valid, log)
        title = it.subtopic.strip() or cfg.DEFAULT_SUBTOPIC
        if title not in subtopics:
            subtopics[title] = Subtopic(subtopic_id=make_subtopic_id(agenda_id, len(subtopics) + 1),
                                        agenda_id=agenda_id, order=len(subtopics) + 1, title=title, item_ids=[])
        subtopics[title].item_ids.append(item_id)

        decision, assignee, due = it.decision_tag, _empty_to_none(it.assignee), _empty_to_none(it.due)
        if it.discussion_tag == "액션":
            if decision != ACTION_DECISION:
                msg = f"액션의 결정사항 태그 {decision!r} → '{ACTION_DECISION}'"
                base["history"].append(ItemRevision(stage=STAGE, field="decision_tag", before=decision,
                                                    after=ACTION_DECISION, reason=msg))
                log.append(PostprocessLog(item_id=item_id, message=msg))
                decision = ACTION_DECISION
        else:
            for field, value in (("assignee", assignee), ("due", due)):
                if value is not None:
                    msg = f"액션이 아닌 항목의 {field} {value!r} → null"
                    base["history"].append(ItemRevision(stage=STAGE, field=field, before=value, after=None, reason=msg))
                    log.append(PostprocessLog(item_id=item_id, message=msg))
            assignee = due = None
        sections["major_discussions"].append(DiscussionItem(
            **base, subtopic_id=subtopics[title].subtopic_id, subtopic=title, discussion_tag=it.discussion_tag,
            decision_tag=decision, assignee=assignee, due=due))

    for it in llm_agenda.unresolved_issues:
        if skip_empty("unresolved_issues", it.text):
            continue
        item_id, order = next_id("unresolved_issues")
        sections["unresolved_issues"].append(UnresolvedItem(
            **_base(item_id, agenda_id, order, it.text, it.evidence_utterance_indices, valid, log), tag=it.tag))

    for it in llm_agenda.next_meeting_topics:
        if skip_empty("next_meeting_topics", it.text):
            continue
        item_id, order = next_id("next_meeting_topics")
        sections["next_meeting_topics"].append(NextTopicItem(
            **_base(item_id, agenda_id, order, it.text, it.evidence_utterance_indices, valid, log), tag=it.tag))

    if not any(sections.values()):
        log.append(PostprocessLog(item_id=None, message=f"안건 '{llm_agenda.agenda_title}': 항목 없음 → 제외"))
        return None
    evidence = [i for items in sections.values() for it in items for i in it.evidence_utterance_indices]
    return Agenda(agenda_id=agenda_id, agenda_number=number, agenda_title=llm_agenda.agenda_title.strip(),
                  evidence_range=[min(evidence), max(evidence)] if evidence else [],
                  subtopics=list(subtopics.values()), **sections)


# ── ④ 안건 목차 · 회의 결과 ──────────────────────────
def build_index(agendas: list[Agenda]) -> list[AgendaIndexEntry]:
    return [AgendaIndexEntry(
        agenda_id=a.agenda_id, agenda_number=a.agenda_number, agenda_title=a.agenda_title,
        decision_count=sum(1 for it in a.major_discussions if it.status != "DELETED" and it.is_counted_decision),
        unresolved_count=sum(1 for it in a.unresolved_issues if it.status != "DELETED"),
        action_count=sum(1 for it in a.major_discussions if it.status != "DELETED" and it.is_action),
    ) for a in agendas]


def build_results(agendas: list[Agenda]) -> MeetingResults:
    alive = lambda items: [it for it in items if it.status != "DELETED"]
    return MeetingResults(
        decision_item_ids=[it.item_id for a in agendas for it in alive(a.major_discussions) if it.is_counted_decision],
        action_item_ids=[it.item_id for a in agendas for it in alive(a.major_discussions) if it.is_action],
        unresolved_item_ids=[it.item_id for a in agendas for it in alive(a.unresolved_issues)],
        next_topic_item_ids=[it.item_id for a in agendas for it in alive(a.next_meeting_topics)],
    )


def refresh_derived(summary: MeetingSummary) -> MeetingSummary:
    # 항목 수정 후 안건 목차 · 회의 결과 다시 계산 (검증 · 수정 단계에서 사용)
    summary.agenda_index = build_index(summary.agendas)
    summary.meeting_results = build_results(summary.agendas)
    return summary


# ── 전체 ──────────────────────────────────────────────
@traceable(name="build_summary")
def build_summary(llm_out: LLMMeetingSummary, loaded: dict) -> MeetingSummary:
    meeting_id, transcript = loaded["meeting_id"], loaded["transcript"]
    valid = {u.utterance_index for u in transcript}
    log: list[PostprocessLog] = [PostprocessLog(item_id=None, message=f"입력: {w}") for w in loaded.get("warnings", [])]
    agendas = []
    for llm_agenda in llm_out.agendas:
        number = len(agendas) + 1
        agenda = build_agenda(llm_agenda, make_agenda_id(meeting_id, number), number, valid, log)
        if agenda is not None:
            agendas.append(agenda)

    return MeetingSummary(
        meeting_id=meeting_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        config=cfg.snapshot(),
        source_document=SourceDocument(
            input_path=loaded["input_path"], utterance_count=len(transcript),
            first_utterance_index=transcript[0].utterance_index if transcript else None,
            last_utterance_index=transcript[-1].utterance_index if transcript else None),
        meeting_info=parse_meeting_info(loaded["metadata"]),
        transcript=transcript,
        agenda_index=build_index(agendas),
        meeting_results=build_results(agendas),
        agendas=agendas,
        postprocess_log=log,
    )
