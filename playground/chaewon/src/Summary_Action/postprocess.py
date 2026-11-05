"""입력 읽기 · 1-1 후처리 (근거 발화 검사 → ID 부여 → 최소 단위 저장 구조)

load_input: 회의 전사문 txt → 메타데이터 · 화자 모드 · 발화 목록 (transcript_parser.py, 번호는 config.UTTERANCE_START부터)
    speaker 모드('발화자' 있음): '이름_userid: 발화문' 또는 '[이름_userid]' + 발화문, 구조 · 대조 오류면 중단
    none 모드('발화자' 없음):    빈 줄 제외 한 줄 = 발화 하나
build_summary: LLM 출력(LLMMeetingSummary, ID 없음) → MeetingSummary
    ① 근거 발화 검사 (항목별)
       - 전사문에 없는 번호 제외 · 중복 제거 · 오름차순 정렬 (제외 내용은 history · postprocess_log)
       - 유효한 근거가 하나도 없으면 status=REVIEW_REQUIRED
    ② 태그 검사 (주요 논의)
       - 액션: decision_tag를 '결정'으로 고정
       - 액션이 아닌 항목: assignees를 빈 배열로, due를 null로 (값이 있었으면 기록)
       - assignees의 빈 문자열 · '-'와 중복은 제외, due의 빈 문자열 · '-'는 null
       - 액션의 due가 날짜 형식(config.DUE_FORMATS)이 아니면 null (기록)
       - 담당자마다 참여자와 대조해 user_id · 성명 · 직무를 배열로 저장 (대조 실패가 있으면 status=REVIEW_REQUIRED)
    ③ ID 부여
       - 안건: {회의 ID}_a{n} / 소주제: {안건 ID}_st{n} / 항목: {안건 ID}_{bg|d|un|next}{n}
       - 문장이 빈 항목 · 항목이 없는 안건은 만들지 않음 (기록)
       - 제목이 '기타'인 안건은 하나로 합쳐 맨 뒤에 둠 (config.MISC_AGENDA_TITLE)
    ④ 안건 목차 · 회의 결과 (item_id 참조)
       - 결정 수: decision_tag == '결정'이고 액션이 아닌 항목 (보류 · 공유 · [액션][결정] 제외)
restore_names: 그래프 마지막(finalize)에 LLM 입력 이름(코드가 붙인 A · B) → 사용자 표시 이름
"""

import re
from datetime import datetime
from pathlib import Path

import config as cfg
from pydantic import BaseModel

from tracing import traceable
from transcript_parser import name_restorer, parse_transcript, resolve_assignee, split_participant_count
from schemas import (ACTION_DECISION, ACTION_TAG, SECTIONS, Agenda, AgendaIndexEntry, BackgroundItem,
                     DiscussionItem, ItemRevision, LLMAgenda, LLMMeetingSummary, MeetingInfo, MeetingResults, MeetingSummary,
                     NextTopicItem, Person, PostprocessLog, SourceDocument, Subtopic, UnresolvedItem,
                     make_agenda_id, make_item_id, make_subtopic_id)

# 사용자 설정: config.py — DEFAULT_SUBTOPIC · EMPTY_VALUES · TITLE_KEYS · DATETIME_KEYS · PARTICIPANT_KEYS

STAGE = "postprocess"
ASSIGNEE_REVIEW_PREFIX = "담당자 확인 필요"
_NAME_FIELDS = {"text", "agenda_title", "subtopic", "title", "assignees", "before", "after", "reason", "description",
                "suggestion", "note", "message", "review_reasons"}     # LLM이 쓴 이름이 남을 수 있는 필드 (전사문 · 회의 정보 · ID 제외)


# ── 입력 ──────────────────────────────────────────────
@traceable(name="load_input")
def load_input(input_path: str | Path) -> dict:
    # 파싱 · 구조 검사: transcript_parser.py (speaker 모드의 구조 · 매핑 오류는 TranscriptFormatError로 중단)
    input_path = Path(input_path)
    parsed = parse_transcript(input_path.read_text(encoding=cfg.INPUT_ENCODING))
    if not parsed["transcript"]:
        raise ValueError(f"발화가 없음: {input_path}")
    return {"meeting_id": input_path.stem, "input_path": str(input_path), **parsed}


def _pick(metadata: dict[str, str], keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    key = next((k for k in keys if k in metadata), None)
    return key, (metadata[key].strip() if key else None)


def parse_meeting_info(metadata: dict[str, str], participants: list[Person] | None = None,
                       speakers: list[str] | None = None) -> MeetingInfo:
    title_key, title = _pick(metadata, cfg.TITLE_KEYS)
    dt_key, dt = _pick(metadata, cfg.DATETIME_KEYS)
    pt_key, pt = _pick(metadata, cfg.PARTICIPANT_KEYS)
    sp_key, sp = _pick(metadata, cfg.SPEAKER_KEYS)

    date = None
    if dt:
        m = re.search(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", dt)
        date = m.group(0) if m else dt

    participants = participants or []
    count = split_participant_count(pt)[1] if pt else None
    if count is None and participants:
        count = len(participants)

    used = {title_key, dt_key, pt_key, sp_key}
    return MeetingInfo(title=title, date=date, datetime_raw=dt, participants=participants,
                       participant_count=count, participants_raw=pt, speaker_user_ids=speakers or [], speakers_raw=sp,
                       extra={k: v for k, v in metadata.items() if k not in used})


# ── ① 근거 발화 검사 ──────────────────────────────────
def check_evidence(indices: list[int], valid: set[int], item_id: str, log: list[PostprocessLog],
                   stage: str = STAGE) -> tuple[list[int], list[ItemRevision], list[str]]:
    # (유효한 근거, 이력, REVIEW_REQUIRED 사유) — 생성 후처리 · 재수정 적용(revision.py)에서 공용
    cleaned = sorted({i for i in indices if i in valid})
    history, reasons = [], []
    invalid = sorted({i for i in indices if i not in valid})
    if invalid:
        msg = f"전사문에 없는 근거 발화 번호 {invalid} 제외"
        history.append(ItemRevision(stage=stage, field="evidence_utterance_indices", before=list(indices),
                                    after=cleaned, reason=msg))
        log.append(PostprocessLog(item_id=item_id, message=msg))
    if not cleaned:
        reasons.append("유효한 근거 발화 없음")
        log.append(PostprocessLog(item_id=item_id, message="유효한 근거 발화 없음 → REVIEW_REQUIRED"))
    return cleaned, history, reasons


def _normalize_due(due: str | None, item_id: str, history: list[ItemRevision], log: list[PostprocessLog],
                   stage: str) -> str | None:
    # 날짜 형식이면 같은 표기로 통일 (2004-10-8 → 2004-10-08), 아니면 null (날짜가 틀린 값도 포함)
    if due is None:
        return None
    for fmt in cfg.DUE_FORMATS:
        try:
            return datetime.strptime(due, fmt).strftime(fmt)
        except ValueError:
            continue
    msg = f"날짜 형식이 아닌 due {due!r} → null"
    history.append(ItemRevision(stage=stage, field="due", before=due, after=None, reason=msg))
    log.append(PostprocessLog(item_id=item_id, message=msg))
    return None


def normalize_discussion(discussion_tag: str, decision: str | None, assignees: list[str], due: str | None,
                         item_id: str, history: list[ItemRevision], log: list[PostprocessLog],
                         stage: str = STAGE) -> tuple[str | None, list[str], str | None]:
    # 주요 논의 태그 규칙 → (decision_tag, assignees, due)
    #   액션: decision_tag '결정' 고정 / 액션이 아닌 항목: assignees 빈 배열 · due null
    #   빈 값('-', '없음' …): assignees에서 제외, due는 null / assignees의 같은 표기는 하나만 남김
    assignees = list(dict.fromkeys(name for value in assignees if (name := _empty_to_none(value))))
    due = _empty_to_none(due)
    if discussion_tag == ACTION_TAG:
        if decision != ACTION_DECISION:
            msg = f"액션의 결정사항 태그 {decision!r} → '{ACTION_DECISION}'"
            history.append(ItemRevision(stage=stage, field="decision_tag", before=decision,
                                        after=ACTION_DECISION, reason=msg))
            log.append(PostprocessLog(item_id=item_id, message=msg))
            decision = ACTION_DECISION
        return decision, assignees, _normalize_due(due, item_id, history, log, stage)
    for field, value, empty, label in (("assignees", assignees, [], "빈 배열"), ("due", due, None, "null")):
        if value:
            msg = f"액션이 아닌 항목의 {field} {value!r} → {label}"
            history.append(ItemRevision(stage=stage, field=field, before=value, after=empty, reason=msg))
            log.append(PostprocessLog(item_id=item_id, message=msg))
    return decision, [], None


def assignee_fields(assignees: list[str], participants: list[Person], item_id: str,
                    log: list[PostprocessLog]) -> tuple[dict, list[str]]:
    # 담당자 목록 → ({assignees, assignee_user_ids, assignee_names, assignee_jobs}, REVIEW_REQUIRED 사유) — 생성 후처리 · 재수정 적용에서 공용
    #   네 배열은 같은 순서 · 같은 길이 (대조되지 않은 담당자의 user_id · 성명 · 직무는 null)
    fields = {"assignees": [], "assignee_user_ids": [], "assignee_names": [], "assignee_jobs": []}
    reasons = []
    for assignee in assignees:
        person, problem = resolve_assignee(assignee, participants)
        if person and person.user_id in fields["assignee_user_ids"]:     # 표기만 다른 같은 사람
            log.append(PostprocessLog(item_id=item_id, message=f"같은 담당자 중복 제외 ({assignee})"))
            continue
        if problem:
            reason = f"{ASSIGNEE_REVIEW_PREFIX}: {problem} ({assignee})"
            reasons.append(reason)
            log.append(PostprocessLog(item_id=item_id, message=f"{reason} → REVIEW_REQUIRED"))
        fields["assignees"].append(assignee)
        fields["assignee_user_ids"].append(person.user_id if person else None)
        fields["assignee_names"].append(person.name if person else None)
        fields["assignee_jobs"].append(person.job if person else None)
    return fields, reasons


def _empty_to_none(value: str | None) -> str | None:
    return None if value is None or value.strip().lower() in cfg.EMPTY_VALUES else value.strip()


def _base(item_id: str, agenda_id: str, order: int, text: str, indices: list[int], valid: set[int],
          log: list[PostprocessLog]) -> dict:
    evidence, history, reasons = check_evidence(indices, valid, item_id, log)
    return {"item_id": item_id, "agenda_id": agenda_id, "order": order, "text": text.strip(),
            "evidence_utterance_indices": evidence, "history": history, "review_reasons": reasons,
            "status": "REVIEW_REQUIRED" if reasons else "ACTIVE"}


# ── ②③ 안건 하나 ─────────────────────────────────────
def build_agenda(llm_agenda, agenda_id: str, number: int, valid: set[int], log: list[PostprocessLog],
                 participants: list[Person]) -> Agenda | None:
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

        decision, assignees, due = normalize_discussion(it.discussion_tag, it.decision_tag, it.assignees, it.due,
                                                        item_id, base["history"], log)
        person, reasons = assignee_fields(assignees, participants, item_id, log)
        if reasons:
            base.update(review_reasons=base["review_reasons"] + reasons, status="REVIEW_REQUIRED")
        sections["major_discussions"].append(DiscussionItem(
            **base, subtopic_id=subtopics[title].subtopic_id, subtopic=title, discussion_tag=it.discussion_tag,
            decision_tag=decision, due=due, **person))

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
    ) for a in agendas if not a.is_empty]


def build_results(agendas: list[Agenda]) -> MeetingResults:
    alive = lambda items: [it for it in items if it.status != "DELETED"]
    return MeetingResults(
        decision_item_ids=[it.item_id for a in agendas for it in alive(a.major_discussions) if it.is_counted_decision],
        action_item_ids=[it.item_id for a in agendas for it in alive(a.major_discussions) if it.is_action],
        unresolved_item_ids=[it.item_id for a in agendas for it in alive(a.unresolved_issues)],
        next_topic_item_ids=[it.item_id for a in agendas for it in alive(a.next_meeting_topics)],
    )


def evidence_range(agenda: Agenda) -> list[int]:
    evidence = [i for it in agenda.iter_items() for i in it.evidence_utterance_indices]
    return [min(evidence), max(evidence)] if evidence else []


def refresh_derived(summary: MeetingSummary) -> MeetingSummary:
    # 항목 수정 후 안건 순서 · 번호 · 근거 범위 · 안건 목차 · 회의 결과 다시 계산 (재수정 적용 후 사용)
    #   순서: 일반 안건 → '기타' → 빈 안건(표시 안 함, 번호 0) / agenda_id는 바뀌지 않음
    active = summary.active_agendas()
    normal = [a for a in active if a.agenda_title != cfg.MISC_AGENDA_TITLE]
    misc = [a for a in active if a.agenda_title == cfg.MISC_AGENDA_TITLE]
    empty = [a for a in summary.agendas if a.is_empty]
    summary.agendas = normal + misc + empty
    for number, agenda in enumerate(normal + misc, 1):
        agenda.agenda_number = number
    for agenda in empty:
        agenda.agenda_number = 0
    for agenda in summary.agendas:
        agenda.evidence_range = evidence_range(agenda)
    summary.agenda_index = build_index(summary.agendas)
    summary.meeting_results = build_results(summary.agendas)
    return summary


# ── 전체 ──────────────────────────────────────────────
def merge_misc_agendas(llm_agendas: list[LLMAgenda], log: list[PostprocessLog]) -> list[LLMAgenda]:
    # 제목이 '기타'인 안건을 하나로 합쳐 맨 뒤로 (ID 부여 전)
    misc = [a for a in llm_agendas if a.agenda_title.strip() == cfg.MISC_AGENDA_TITLE]
    if not misc:
        return llm_agendas
    if len(misc) > 1:
        log.append(PostprocessLog(item_id=None, message=f"'{cfg.MISC_AGENDA_TITLE}' 안건 {len(misc)}개를 하나로 합침"))
    merged = LLMAgenda(agenda_title=cfg.MISC_AGENDA_TITLE,
                       **{name: [it for a in misc for it in a.section_items(name)] for name in SECTIONS})
    return [a for a in llm_agendas if a.agenda_title.strip() != cfg.MISC_AGENDA_TITLE] + [merged]


# ── LLM 입력 이름 → 사용자 표시 이름 ────────────────────
def _restore_value(value, restore):
    # 문자열 또는 문자열 배열(assignees · review_reasons · 수정 이력의 before / after)의 이름을 되돌림
    if isinstance(value, list):
        return [restore(v) if isinstance(v, str) else v for v in value]
    return restore(value) if isinstance(value, str) else value


def _restore_fields(value, restore) -> None:
    if isinstance(value, BaseModel):
        for field in type(value).model_fields:
            current = getattr(value, field)
            if field in _NAME_FIELDS:
                setattr(value, field, _restore_value(current, restore))
            else:
                _restore_fields(current, restore)
    elif isinstance(value, list):
        for item in value:
            _restore_fields(item, restore)


def restore_names(summary: MeetingSummary) -> MeetingSummary:
    # 항목 · 안건 · 수정 이력 · 검증 기록의 LLM 입력 이름 → 표시 이름
    participants = summary.meeting_info.participants
    restore = name_restorer(participants)
    for part in (summary.agendas, summary.agenda_index, summary.verification, summary.postprocess_log):
        _restore_fields(part, restore)
    shown = {p.user_id: p.display_name for p in participants}
    for agenda in summary.agendas:
        for item in agenda.major_discussions:
            # 표기가 달라도 대조된 담당자는 표시 이름으로 통일
            item.assignees = [shown[user_id] if user_id else name
                              for user_id, name in zip(item.assignee_user_ids, item.assignees)]
    return summary


@traceable(name="build_summary")
def build_summary(llm_out: LLMMeetingSummary, loaded: dict) -> MeetingSummary:
    meeting_id, transcript = loaded["meeting_id"], loaded["transcript"]
    valid = {u.utterance_index for u in transcript}
    log: list[PostprocessLog] = [PostprocessLog(item_id=None, message=f"입력: {w}") for w in loaded.get("warnings", [])]
    meeting_info = parse_meeting_info(loaded["metadata"], loaded.get("participants"), loaded.get("speakers"))
    agendas = []
    for llm_agenda in merge_misc_agendas(llm_out.agendas, log):
        number = len(agendas) + 1
        agenda = build_agenda(llm_agenda, make_agenda_id(meeting_id, number), number, valid, log,
                              meeting_info.participants)
        if agenda is not None:
            agendas.append(agenda)

    return MeetingSummary(
        meeting_id=meeting_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        config=cfg.snapshot(),
        source_document=SourceDocument(
            input_path=loaded["input_path"], speaker_mode=loaded["speaker_mode"],
            transcript_format=loaded["transcript_format"], utterance_count=len(transcript),
            first_utterance_index=transcript[0].utterance_index if transcript else None,
            last_utterance_index=transcript[-1].utterance_index if transcript else None),
        meeting_info=meeting_info,
        transcript=transcript,
        agenda_index=build_index(agendas),
        meeting_results=build_results(agendas),
        agendas=agendas,
        postprocess_log=log,
    )
