"""[9] 구조 검증 (LLM 호출 없음)

렌더링 전에 안건별 요약(agendas)의 JSON이 정의한 구조 · 자료형 · ID 규칙을 만족하는지 코드로 확인
    ① JSON 변환 · 파싱 (내부 표준 JSON) — JSON_PARSE_ERROR
    ② 검사 + 안전한 오류 자동 보정 (보정할 것이 없을 때까지 최대 config.STRUCTURE_MAX_RETRIES회)
       | 검사                     | 오류 유형                | 자동 보정
       | 필수 섹션 존재 · 배열     | MISSING_SECTION /        | 없음(null) → 빈 배열, 객체 하나 → 배열로 감쌈
       |                          | SCHEMA_VALIDATION_ERROR  |
       | 안건 제목                 | SCHEMA_VALIDATION_ERROR  | 비었으면 안건 Registry의 제목
       | 필수 필드 · 자료형         | SCHEMA_VALIDATION_ERROR  | 근거 번호 문자열 → 정수, 상태값 오류 → REVIEW_REQUIRED (그 외 보정 안 함)
       | 허용 태그                 | INVALID_TAG              | 표기 차이(공백 · '/' 등) 정규화, 미해결 이슈는 태그 없음(null)
       | ID 형식 · 고유성           | INVALID_ID_REFERENCE /   | 항목 ID → 새 ID 부여 (안건 ID는 보정 안 함)
       |                          | DUPLICATE_ID             |
       | Segment 참조              | INVALID_ID_REFERENCE     | 없는 Segment 번호 제거
       | 근거 인덱스 범위           | EVIDENCE_OUT_OF_RANGE    | 범위 밖 번호 제거 (남는 근거가 없으면 보정 안 함)
       마지막에 Pydantic(Agenda · 항목 모델)으로 전체 구조를 한 번 더 확인
    ③ 보정 후에도 남은 오류 → 해당 항목(또는 안건)을 격리 (quarantine), 통과한 데이터만 다음 단계로 전달

삭제된(DELETED) 항목은 렌더링되지 않으므로 ID · 구조만 확인하고, 태그 · 근거 · 문장은 확인하지 않음
노트북 확인용: validate_structure_json(raw_json, ...)에 손상된 JSON을 넣어 보정 · 격리 동작 확인 가능
LangGraph 노드: structure_validation_node(state) → 다음 노드는 항상 render
"""

import json
import re
from dataclasses import dataclass, field
from typing import get_args

from pydantic import ValidationError

import config as cfg
from schemas import (SECTION_ITEM_MODEL, SECTION_PREFIX, SECTION_TAGS, SECTIONS, Agenda, AgendaRegistry, ItemStatus,
                     Segment, StructureErrorDetail, StructureValidationReport, SummaryState, make_item_id)
from tracing import traceable

# 사용자 설정: config.py — STRUCTURE_MAX_RETRIES

# ── 내부 상수 ────────────────────────────────────────
NEXT_NODE = "render"

_MISSING = object()


@dataclass
class Context:
    meeting_id: str
    utterance_ids: set[int]
    segment_ids: set[int]
    titles: dict[str, str]                                  # agenda_id → Registry 제목
    errors: list[tuple[StructureErrorDetail, tuple | None]] = field(default_factory=list)   # (오류, 격리 대상)
    fix: bool = False

    def add(self, error_type: str, target_id: str, field_name: str, expected: str, actual, target: tuple, fixed: bool):
        detail = StructureErrorDetail(error_type=error_type, target_id=target_id or "", field=field_name,
                                      expected=expected, actual=str(actual)[:200], auto_fixed=fixed)
        self.errors.append((detail, None if fixed else target))


def normalize_tag(tag, allowed: tuple):
    # 허용 태그와 표기만 다르면(공백 · '/' · 가운뎃점 종류) 허용 태그로 정규화, 아니면 _MISSING
    if tag in allowed:
        return tag
    if isinstance(tag, str):
        key = re.sub(r"[\s/·・ㆍ]", "", tag)
        for t in allowed:
            if t is not None and re.sub(r"[\s/·・ㆍ]", "", t) == key:
                return t
    return _MISSING


# ── ② 항목 검사 ───────────────────────────────────────
def check_item(ctx: Context, agenda: dict, aid: str, section: str, item, counters: dict, seen_items: set) -> None:
    target = ("item", agenda, section, item)
    start = len(ctx.errors)
    if not isinstance(item, dict):
        ctx.add("SCHEMA_VALIDATION_ERROR", aid, section, "object", type(item).__name__, target, False)
        return

    # ID 형식 · 고유성
    iid = item.get("item_id")
    m = re.fullmatch(rf"{re.escape(aid)}_{SECTION_PREFIX[section]}(\d+)", iid) if isinstance(iid, str) else None
    if m is None or iid in seen_items:
        error_type = "DUPLICATE_ID" if iid in seen_items else "INVALID_ID_REFERENCE"
        expected = "고유한 item_id" if iid in seen_items else f"{aid}_{SECTION_PREFIX[section]}{{n}}"
        if ctx.fix:
            n = counters.get(section, 0) + 1
            counters[section] = n
            item["item_id"] = make_item_id(aid, section, n)
        ctx.add(error_type, iid if isinstance(iid, str) else aid, "item_id", expected, iid, target, ctx.fix)
        iid = item.get("item_id")
    elif counters.get(section, 0) < int(m.group(1)):
        ctx.add("SCHEMA_VALIDATION_ERROR", iid, "item_counters", f">= {m.group(1)}", counters.get(section, 0), target, ctx.fix)
        if ctx.fix:
            counters[section] = int(m.group(1))
    seen_items.add(iid)

    # 상태
    if item.get("status", "ACTIVE") not in get_args(ItemStatus):
        ctx.add("SCHEMA_VALIDATION_ERROR", iid, "status", "|".join(get_args(ItemStatus)), item.get("status"), target, ctx.fix)
        if ctx.fix:
            item["status"] = "REVIEW_REQUIRED"

    if item.get("status") != "DELETED":
        # 문장
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            ctx.add("SCHEMA_VALIDATION_ERROR", iid, "text", "비어 있지 않은 문자열", text, target, False)

        # 근거 인덱스
        ev = item.get("evidence_utterance_indices")
        if isinstance(ev, int):
            ctx.add("SCHEMA_VALIDATION_ERROR", iid, "evidence_utterance_indices", "array", "integer", target, ctx.fix)
            ev = [ev]
            if ctx.fix:
                item["evidence_utterance_indices"] = ev
        if not isinstance(ev, list):
            ctx.add("SCHEMA_VALIDATION_ERROR", iid, "evidence_utterance_indices", "array", type(ev).__name__, target, False)
        else:
            ints = [int(i) if isinstance(i, str) and i.strip().isdigit() else i for i in ev]
            if ints != ev:
                ctx.add("SCHEMA_VALIDATION_ERROR", iid, "evidence_utterance_indices", "array[int]", ev, target, ctx.fix)
            valid = [i for i in ints if isinstance(i, int) and not isinstance(i, bool) and i in ctx.utterance_ids]
            invalid = [i for i in ints if i not in valid]
            if not valid:       # 남는 근거가 없으면 보정 불가
                ctx.add("EVIDENCE_OUT_OF_RANGE", iid, "evidence_utterance_indices", "원본 발화 번호 1개 이상", ev, target, False)
            elif invalid:
                ctx.add("EVIDENCE_OUT_OF_RANGE", iid, "evidence_utterance_indices", "원본 발화 번호", invalid, target, ctx.fix)
            if ctx.fix and valid:
                item["evidence_utterance_indices"] = valid

        # 태그
        if section != "discussion_background":
            allowed = SECTION_TAGS[section]
            tag = item.get("tag")
            norm = normalize_tag(tag, allowed)
            if norm is _MISSING:
                fixable = None in allowed
                ctx.add("INVALID_TAG", iid, "tag", "|".join(t or "null" for t in allowed), tag, target, ctx.fix and fixable)
                if ctx.fix and fixable:
                    item["tag"] = None
            elif norm != tag:
                ctx.add("INVALID_TAG", iid, "tag", norm, tag, target, ctx.fix)
                if ctx.fix:
                    item["tag"] = norm

    # Segment 참조
    seg = item.get("source_segment_ids", [])
    if isinstance(seg, list) and any(s not in ctx.segment_ids for s in seg):
        ctx.add("INVALID_ID_REFERENCE", iid, "source_segment_ids", "존재하는 segment_id",
                [s for s in seg if s not in ctx.segment_ids], target, ctx.fix)
        if ctx.fix:
            item["source_segment_ids"] = [s for s in seg if s in ctx.segment_ids]

    # 전체 구조 (앞에서 보정할 수 없는 오류가 없을 때만)
    if all(t is None for _, t in ctx.errors[start:]):
        try:
            SECTION_ITEM_MODEL[section].model_validate(item)
        except ValidationError as e:
            err = e.errors()[0]
            ctx.add("SCHEMA_VALIDATION_ERROR", iid, ".".join(map(str, err["loc"])), err["msg"], err.get("input"), target, False)


# ── ② 안건 검사 ───────────────────────────────────────
def inspect(data: list, ctx: Context, fix: bool) -> list[tuple[StructureErrorDetail, tuple | None]]:
    ctx.errors, ctx.fix = [], fix
    seen_agendas, seen_items = set(), set()
    for agenda in data:
        target = ("agenda", agenda)
        if not isinstance(agenda, dict):
            ctx.add("SCHEMA_VALIDATION_ERROR", "", "agendas[]", "object", type(agenda).__name__, target, False)
            continue
        aid = agenda.get("agenda_id")
        if not isinstance(aid, str) or not re.fullmatch(rf"{re.escape(ctx.meeting_id)}_a\d+", aid):
            ctx.add("INVALID_ID_REFERENCE", str(aid), "agenda_id", f"{ctx.meeting_id}_a{{n}}", aid, target, False)
            continue
        if aid in seen_agendas:
            ctx.add("DUPLICATE_ID", aid, "agenda_id", "고유한 agenda_id", aid, target, False)
            continue
        seen_agendas.add(aid)
        start = len(ctx.errors)

        title = agenda.get("agenda_title")
        if not isinstance(title, str) or not title.strip():
            fixable = bool(ctx.titles.get(aid))
            ctx.add("SCHEMA_VALIDATION_ERROR", aid, "agenda_title", "비어 있지 않은 문자열", title, target, fix and fixable)
            if fix and fixable:
                agenda["agenda_title"] = ctx.titles[aid]

        counters = agenda.get("item_counters")
        if not isinstance(counters, dict):
            ctx.add("SCHEMA_VALIDATION_ERROR", aid, "item_counters", "object", type(counters).__name__, target, fix)
            counters = {}
            if fix:
                agenda["item_counters"] = counters

        for section in SECTIONS:
            items = agenda.get(section)
            if items is None:
                ctx.add("MISSING_SECTION", aid, section, "array", "없음", target, fix)
                if fix:
                    agenda[section] = []
                continue
            if isinstance(items, dict):
                ctx.add("SCHEMA_VALIDATION_ERROR", aid, section, "array", "object", target, fix)
                items = [items]
                if fix:
                    agenda[section] = items
            elif not isinstance(items, list):
                ctx.add("SCHEMA_VALIDATION_ERROR", aid, section, "array", type(items).__name__, target, False)
                continue
            for item in items:
                check_item(ctx, agenda, aid, section, item, counters, seen_items)

        seg = agenda.get("source_segment_ids", [])
        if isinstance(seg, list) and any(s not in ctx.segment_ids for s in seg):
            ctx.add("INVALID_ID_REFERENCE", aid, "source_segment_ids", "존재하는 segment_id",
                    [s for s in seg if s not in ctx.segment_ids], target, fix)
            if fix:
                agenda["source_segment_ids"] = [s for s in seg if s in ctx.segment_ids]

        # 전체 구조 (안건 · 항목에 보정할 수 없는 오류가 없을 때만)
        if all(t is None for _, t in ctx.errors[start:]):
            try:
                Agenda.model_validate(agenda)
            except ValidationError as e:
                err = e.errors()[0]
                ctx.add("SCHEMA_VALIDATION_ERROR", aid, ".".join(map(str, err["loc"])), err["msg"],
                        err.get("input"), target, False)
    return ctx.errors


# ── ③ 격리 ────────────────────────────────────────────
def quarantine_targets(data: list, remaining: list[tuple[StructureErrorDetail, tuple]]) -> list[dict]:
    # 보정하지 못한 오류의 대상 항목 · 안건을 data에서 빼고 격리 목록으로 반환 (같은 대상의 오류는 하나로 묶음)
    groups: dict[int, dict] = {}
    for detail, target in remaining:
        obj = target[1] if target[0] == "agenda" else target[3]
        g = groups.setdefault(id(obj), {"target": target, "errors": []})
        g["errors"].append(detail.model_dump())

    removed_agendas = {id(g["target"][1]) for g in groups.values() if g["target"][0] == "agenda"}
    quarantine = []
    for g in groups.values():
        target = g["target"]
        agenda = target[1]
        aid = agenda.get("agenda_id") if isinstance(agenda, dict) else None
        if target[0] == "agenda":
            data[:] = [a for a in data if a is not agenda]
            quarantine.append({"kind": "agenda", "agenda_id": aid, "data": agenda, "errors": g["errors"]})
        elif id(agenda) not in removed_agendas:
            section, item = target[2], target[3]
            agenda[section] = [it for it in agenda[section] if it is not item]
            item_id = item.get("item_id") if isinstance(item, dict) else None
            quarantine.append({"kind": "item", "agenda_id": aid, "section": section, "item_id": item_id,
                               "data": item, "errors": g["errors"]})
    return quarantine


def quarantine_id(q: dict) -> str:
    return str(q.get("item_id") or q.get("agenda_id"))


# ── 검증 실행 ─────────────────────────────────────────
def validate_structure_json(raw_json: str, meeting_id: str, segments: list[Segment], utterance_texts: dict[int, str],
                            registry: AgendaRegistry | None = None) -> dict:
    ctx = Context(meeting_id=meeting_id, utterance_ids=set(utterance_texts),
                  segment_ids={s.segment_id for s in segments},
                  titles={a.agenda_id: a.agenda_title for a in registry.agendas} if registry else {})
    log: list[str] = []

    # ① JSON 파싱
    try:
        data = json.loads(raw_json)
        if not isinstance(data, list):
            raise ValueError(f"최상위가 배열이 아님 ({type(data).__name__})")
    except (json.JSONDecodeError, ValueError) as e:
        detail = StructureErrorDetail(error_type="JSON_PARSE_ERROR", field="agendas", expected="JSON array", actual=str(e)[:200])
        report = StructureValidationReport(validation_status="FAILED", error_details=[detail], requires_manual_review=True)
        return {"agendas": [], "report": report, "quarantine": [{"kind": "json", "data": raw_json, "errors": [detail.model_dump()]}],
                "log": [f"JSON 파싱 실패: {e}"]}

    # ② 검사 + 자동 보정 → 재검증
    fixed_details: list[StructureErrorDetail] = []
    retry = 0
    while retry < cfg.STRUCTURE_MAX_RETRIES:
        fixed = [d for d, t in inspect(data, ctx, fix=True) if d.auto_fixed]
        if not fixed:
            break
        fixed_details += fixed
        retry += 1
        log.append(f"자동 보정 {retry}회차: {len(fixed)}건")
    remaining = inspect(data, ctx, fix=False)       # fix=False에서는 모든 오류가 미보정(격리 대상)으로 기록됨

    # ③ 격리 → 남은 데이터 최종 확인
    quarantine = quarantine_targets(data, remaining)
    for q in quarantine:
        log.append(f"격리: {q['kind']} {quarantine_id(q)} — {[e['error_type'] for e in q['errors']]}")
    leftover = inspect(data, ctx, fix=False)
    if leftover:        # 항목 격리 후 안건 구조 오류가 드러난 경우 → 해당 안건도 격리
        quarantine += quarantine_targets(data, leftover)
        remaining += leftover

    agendas = [Agenda.model_validate(a) for a in data]
    for a in agendas:
        if not any(True for _ in a.iter_items()):
            log.append(f"{a.agenda_id}: 표시할 항목이 없는 안건 (렌더링에서 제외됨)")

    report = StructureValidationReport(
        validation_status="FAILED" if remaining else "PASSED",
        retry_count=retry,
        error_details=fixed_details + [d for d, _ in remaining],
        quarantined_ids=[quarantine_id(q) for q in quarantine],
        requires_manual_review=bool(remaining),
    )
    return {"agendas": agendas, "report": report, "quarantine": quarantine, "log": log}


def validate_structure(agendas: list[Agenda], meeting_id: str, segments: list[Segment], utterance_texts: dict[int, str],
                       registry: AgendaRegistry | None = None) -> dict:
    raw = json.dumps([a.model_dump(mode="json") for a in agendas], ensure_ascii=False)
    return validate_structure_json(raw, meeting_id, segments, utterance_texts, registry)


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="structure_validation_node")
def structure_validation_node(state: SummaryState) -> dict:
    result = validate_structure(state["agendas"], state["meeting_id"], state["segments"], state["utterance_texts"],
                                state.get("registry"))
    report = result["report"]
    fixed = sum(d.auto_fixed for d in report.error_details)
    review = [it.item_id for a in result["agendas"] for _, it in a.iter_items() if it.status == "REVIEW_REQUIRED"]
    print(f"[구조 검증] {report.validation_status} / 자동 보정 {fixed}건 (재검증 {report.retry_count}회) / "
          f"격리 {len(result['quarantine'])}건 / 검토 필요(REVIEW_REQUIRED) 항목 {len(review)}개")
    for line in result["log"]:
        print("   -", line)
    return {
        "agendas": result["agendas"],               # 구조 검증을 통과한 데이터만
        "structure_report": report,
        "quarantine": result["quarantine"],
    }
