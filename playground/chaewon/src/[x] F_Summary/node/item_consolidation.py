"""[2-2] 항목 정돈 (agenda_split 변형)

안건별로 발화 단위로 잘게 나뉜 항목을 핵심 중심으로 정돈 (이후 [3] 내용 검증이 원문과 대조해 확인)
    ① LLM 1회 (안건 하나당, LLMConsolidation): 결과 항목 목록(합칠 항목 ID · 섹션 · 태그 · 소주제 · 문장 · 근거) + 제거 항목
    ② 코드 검사 (통과하지 못한 결과 항목은 반영하지 않고 원래 항목 유지)
       - 없는 ID · 다른 결과에서 이미 쓴 ID 제외
       - 섹션: 원래 섹션 그대로이거나, 주요 논의 [정보](+ 논의 배경) → 논의 배경 이동만 허용, 섹션이 다른 항목끼리는 병합 안 함
       - 태그: 결과 섹션에 맞는 태그만 (맞지 않으면 원래 항목들의 공통 태그, 없으면 반영 안 함)
       - 근거: 원래 항목들의 근거 발화 안에서만 (없으면 원래 근거 전체)
       - 숫자: 결과 문장의 숫자는 원래 항목 문장에 있던 숫자만 (CHECK_NUMBERS)
       - 제거: CONSOLIDATE_ALLOW_REMOVE일 때 주요 논의 · 논의 배경 항목만 (결정사항 · 미해결 이슈 · 다음 회의 후보 안건은 유지)
    ③ 반영 (항목 ID 기준, 수정 전 값은 history stage="consolidate")
       - 같은 섹션: 가장 먼저 논의된 원래 항목에 결과를 반영, 나머지는 DELETED(병합)
       - 논의 배경으로 이동: 논의 배경에 새 항목 추가(origin_item_ids에 원래 ID), 원래 항목은 DELETED(이동)
       - 결과에도 제거 목록에도 없는 항목은 그대로 유지

노트북 확인용: consolidate_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: item_consolidation_node(state) → content_validation
"""

import re

import config as cfg
from llm import call_structured
from node.content_validation import source_utterances
from prompt.item_consolidation_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import (SECTION_TAGS, SECTIONS, Agenda, ItemRevision, LLMConsolidatedItem, LLMConsolidation, Segment,
                     SummaryItem, SummaryState)
from tracing import traceable

# 사용자 설정: config.py — CONSOLIDATE_MIN_ITEMS · CONSOLIDATE_ALLOW_REMOVE · CHECK_NUMBERS · STEP_EFFORT["item_consolidation"]

# ── 내부 상수 ────────────────────────────────────────
STAGE = "consolidate"
PROTECTED_SECTIONS = ("decisions", "unresolved_issues", "next_meeting_topics")  # 섹션 이동 · 제거 안 함
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


def first_evidence(item: SummaryItem) -> int:
    return item.evidence_utterance_indices[0] if item.evidence_utterance_indices else -1


# ── ② 코드 검사 ───────────────────────────────────────
def check_result(out: LLMConsolidatedItem, sources: list[tuple[str, SummaryItem]]) -> tuple[str | None, str | None]:
    # (결과 태그, 반영할 수 없는 이유) — 이유가 None이면 통과
    src_sections = {s for s, _ in sources}
    target = out.section
    if src_sections == {target}:
        pass
    elif target == "discussion_background" and src_sections <= {"discussion_background", "major_discussions"} \
            and all(getattr(it, "tag", None) == "정보" for s, it in sources if s == "major_discussions"):
        pass
    else:
        return None, f"허용되지 않은 섹션 병합 · 이동 ({sorted(src_sections)} → {target})"

    tag = out.tag
    if tag not in SECTION_TAGS[target]:
        common = {getattr(it, "tag", None) for s, it in sources if s == target}
        if len(common) == 1 and next(iter(common)) in SECTION_TAGS[target]:
            tag = next(iter(common))
        else:
            return None, f"{target}에 맞지 않는 태그 '{out.tag}'"
    if target == "discussion_background":
        tag = None

    if not out.text.strip():
        return None, "결과 문장이 비어 있음"
    if cfg.CHECK_NUMBERS:
        allowed = set(NUMBER_PATTERN.findall(" ".join(it.text for _, it in sources)))
        extra = set(NUMBER_PATTERN.findall(out.text)) - allowed
        if extra:
            return None, f"원래 항목에 없는 숫자 {sorted(extra)}"
    return tag, None


# ── ③ 반영 ────────────────────────────────────────────
def apply_result(agenda: Agenda, out: LLMConsolidatedItem, sources: list[tuple[str, SummaryItem]], tag: str | None,
                 log: list[str]) -> str:
    union = sorted({i for _, it in sources for i in it.evidence_utterance_indices})
    evidence = sorted({i for i in out.evidence_utterance_indices if i in union}) or union
    segments = sorted({s for _, it in sources for s in it.source_segment_ids})
    sources = sorted(sources, key=lambda x: (first_evidence(x[1]), x[1].item_id))
    target = out.section
    reason = f"항목 정돈: {[it.item_id for _, it in sources]}"

    if all(s == target for s, _ in sources):                  # 같은 섹션: 첫 항목에 반영, 나머지 병합
        keep = sources[0][1]
        subtopic = out.subtopic.strip() if target == "major_discussions" else ""
        if len(sources) == 1 and out.text.strip() == keep.text and evidence == keep.evidence_utterance_indices                 and tag == getattr(keep, "tag", None) and (not subtopic or subtopic == keep.subtopic):
            return keep.item_id                                 # 바뀐 것 없음 → 이력 · 로그 남기지 않음
        keep.revise(out.text.strip(), evidence, STAGE, reason=reason)
        if hasattr(keep, "tag"):
            keep.tag = tag
        if subtopic:
            keep.subtopic = subtopic
        keep.source_segment_ids = segments
        for _, other in sources[1:]:
            keep.origin_item_ids = [*keep.origin_item_ids, other.item_id]
            other.delete(STAGE, reason=f"항목 정돈: {keep.item_id}에 병합")
        merged = [it.item_id for _, it in sources[1:]]
        log.append(f"{keep.item_id}: " + (f"{merged} 병합" if merged else "다듬기"))
        return keep.item_id

    new = agenda.add_item(target, text=out.text.strip(), evidence_utterance_indices=evidence,     # 논의 배경으로 이동
                          source_segment_ids=segments, origin_item_ids=[it.item_id for _, it in sources])
    new.history.append(ItemRevision(stage=STAGE, before_text=" / ".join(it.text for _, it in sources),
                                    before_evidence=union, reason=reason))
    for _, it in sources:
        it.delete(STAGE, reason=f"항목 정돈: 논의 배경 {new.item_id}로 이동")
    log.append(f"{new.item_id}: {[it.item_id for _, it in sources]} → 논의 배경으로 이동")
    return new.item_id


# ── 안건 하나 정돈 ────────────────────────────────────
@traceable(name="item_consolidation")
def consolidate_agenda(agenda: Agenda, segments: list[Segment], utterance_texts: dict[int, str], meeting_id: str) -> dict:
    # agenda를 직접 수정함 (노드에서는 복사본을 넘김)
    log: list[str] = []
    utterances = [(i, t) for i, t in source_utterances(agenda, segments, utterance_texts)
                  if i in {j for _, it in agenda.iter_items() for j in it.evidence_utterance_indices}]
    user_prompt = build_user_prompt(agenda, utterances)
    llm_out = call_structured(SYSTEM_PROMPT, user_prompt, LLMConsolidation,
                              tag=f"{meeting_id}_item_consolidation_{agenda.agenda_id}",
                              effort=cfg.STEP_EFFORT["item_consolidation"])

    visible = {it.item_id: (section, it) for section, it in agenda.iter_items()}
    before = len(visible)
    used: set[str] = set()
    for n, out in enumerate(llm_out.items):
        ids = [i for i in dict.fromkeys(out.source_item_ids) if i in visible and i not in used]
        bad = [i for i in out.source_item_ids if i not in ids]
        if bad:
            log.append(f"결과{n}: 없는 · 이미 쓴 항목 ID {bad} 제외")
        if not ids:
            continue
        sources = [visible[i] for i in ids]
        tag, problem = check_result(out, sources)
        if problem:
            log.append(f"결과{n}({ids}): {problem} → 원래 항목 유지")
            used.update(ids)
            continue
        apply_result(agenda, out, sources, tag, log)
        used.update(ids)

    for removed in llm_out.removed:
        if removed.item_id not in visible or removed.item_id in used:
            log.append(f"제거 {removed.item_id}: 없는 · 이미 쓴 항목 → 무시")
            continue
        section, item = visible[removed.item_id]
        used.add(removed.item_id)
        if not cfg.CONSOLIDATE_ALLOW_REMOVE or section in PROTECTED_SECTIONS:
            log.append(f"제거 {removed.item_id}: {section} 항목은 제거하지 않음 → 유지")
            continue
        item.delete(STAGE, reason=f"항목 정돈: 제거 — {removed.reason}")
        log.append(f"제거 {removed.item_id}: {removed.reason}")

    untouched = [i for i in visible if i not in used]
    if untouched:
        log.append(f"결과에 없는 항목 {len(untouched)}개 → 그대로 유지 {untouched}")
    for section in SECTIONS:                                    # 섹션 안 항목을 논의 순서로
        agenda.section(section).sort(key=lambda it: (first_evidence(it), it.item_id))
    after = sum(1 for _ in agenda.iter_items())
    return {
        "before": before, "after": after,
        "user_prompt": user_prompt,     # LLM 입력 (확인용)
        "llm_output": llm_out,          # LLM 원본 출력
        "log": log,                     # 반영 · 제외 내용
    }


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="item_consolidation_node")
def item_consolidation_node(state: SummaryState) -> dict:
    agendas = [a.model_copy(deep=True) for a in state["agendas"]]
    for agenda in agendas:
        count = sum(1 for _ in agenda.iter_items())
        if count < cfg.CONSOLIDATE_MIN_ITEMS:
            print(f"[항목 정돈] {agenda.agenda_id} {agenda.agenda_title}: 항목 {count}개 → 정돈 생략")
            continue
        result = consolidate_agenda(agenda, state["segments"], state["utterance_texts"], state["meeting_id"])
        subtopics = len({it.subtopic for it in agenda.major_discussions if it.status != "DELETED"})
        print(f"[항목 정돈] {agenda.agenda_id} {agenda.agenda_title}: 항목 {result['before']} → {result['after']}개 "
              f"(논의 배경 {sum(1 for it in agenda.discussion_background if it.status != 'DELETED')} · 소주제 {subtopics})")
        for line in result["log"]:
            print("   -", line)
    return {"agendas": agendas}
