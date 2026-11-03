"""[2] 안건 기준 1차 병합 (LLM 호출 없음)

Segment별 1차 요약(segment_summaries) → 안건별 요약(agendas)
    ① agenda_id 기준 그룹화: Segment별 같은 안건의 항목을 하나의 Agenda로 모음 (item_id 그대로 유지)
    ② 코드 중복 제거: 같은 섹션 · 같은 태그 · 같은 근거 발화 집합인 항목 → 먼저 나온 항목만 남김

의미적 중복 · 번복된 결정은 [3] 내용 검증에서 확인
삭제된 항목은 목록에서 빼지 않고 status="DELETED"로 남김 (이력 보존, 렌더링 · 검증에서 제외)
노트북 확인용: group_by_agenda / remove_exact_duplicates를 단계별로 실행 가능
LangGraph 노드: agenda_merge_node(state)
"""

import config as cfg
from schemas import SECTIONS, Agenda, ItemRevision, SegmentSummary, SummaryItem, SummaryState
from tracing import traceable

# 사용자 설정: config.py — REMOVE_EXACT_DUPLICATES


# ── ① agenda_id 기준 그룹화 ───────────────────────────
def group_by_agenda(segment_summaries: list[SegmentSummary]) -> list[Agenda]:
    # 안건은 처음 등장한 순서, 섹션 안 항목은 근거 발화 순서로 정렬 (입력 segment_summaries는 바꾸지 않음)
    agendas: dict[str, Agenda] = {}
    for summary in segment_summaries:
        for src in summary.agendas:
            src = src.model_copy(deep=True)
            agenda = agendas.get(src.agenda_id)
            if agenda is None:
                agendas[src.agenda_id] = src
                continue
            agenda.source_segment_ids = sorted({*agenda.source_segment_ids, *src.source_segment_ids})
            for section in SECTIONS:
                agenda.section(section).extend(src.section(section))
            for section, n in src.item_counters.items():
                agenda.item_counters[section] = max(agenda.item_counters.get(section, 0), n)

    for agenda in agendas.values():
        for section in SECTIONS:
            agenda.section(section).sort(key=lambda it: (it.evidence_utterance_indices[:1] or [-1], it.item_id))
    return list(agendas.values())


# ── ② 코드 중복 제거 ──────────────────────────────────
def remove_exact_duplicates(agenda: Agenda, log: list[str]) -> None:
    # 같은 섹션 · 같은 태그 · 같은 근거 발화 집합 → Overlap 때문에 같은 발화를 두 번 요약한 것으로 보고 먼저 나온 항목만 남김
    for section in SECTIONS:
        seen: dict[tuple, SummaryItem] = {}
        for item in agenda.section(section):
            if item.status == "DELETED":
                continue
            key = (getattr(item, "tag", None), tuple(item.evidence_utterance_indices))
            keep = seen.get(key)
            if keep is None:
                seen[key] = item
                continue
            keep.source_segment_ids = sorted({*keep.source_segment_ids, *item.source_segment_ids})
            item.history.append(ItemRevision(stage="merge", before_text=item.text,
                                             before_evidence=item.evidence_utterance_indices,
                                             reason=f"{keep.item_id}와 근거 발화 · 태그가 같아 중복 제거"))
            item.status = "DELETED"
            log.append(f"{agenda.agenda_id} {section}: {item.item_id} → {keep.item_id}에 중복 제거 (근거 {list(key[1])})")


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="agenda_merge_node")
def agenda_merge_node(state: SummaryState) -> dict:
    agendas = group_by_agenda(state["segment_summaries"])
    for agenda in agendas:
        log: list[str] = []
        before = sum(1 for _ in agenda.iter_items())
        if cfg.REMOVE_EXACT_DUPLICATES:
            remove_exact_duplicates(agenda, log)
        after = sum(1 for _ in agenda.iter_items())
        print(f"[병합] {agenda.agenda_id} {agenda.agenda_title} (s{agenda.source_segment_ids}): 항목 {before} → {after}")
        for line in log:
            print("   -", line)
    return {"agendas": agendas}
