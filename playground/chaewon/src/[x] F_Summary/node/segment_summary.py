"""[1] 추정 경계 Segment별 1차 요약

Segment 순서대로 (앞 Segment의 안건 Registry를 다음 Segment가 참조하므로 순차 처리)
    ① 안건 후보 구성 (Registry의 안건 전체를 ID · 제목 · 근거 범위만 LLM에 전달)
    ② LLM 1차 요약 (LLMSegmentSummary, ID 없음)
    ③ 후처리: 근거 발화 검사 → agenda_id 확정 → item_id 부여
    ④ Registry 갱신 (신규 안건 추가 / 기존 안건 근거 범위 갱신)

노트북 확인용: summarize_segment(...)로 Segment 하나씩 실행 가능
LangGraph 노드: segment_summary_node(state) — 아직 요약하지 않은 Segment만 처리
"""

import config as cfg
from llm import call_structured
from prompt.segment_summary_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import (SECTIONS, Agenda, AgendaRef, AgendaRegistry, LLMSegmentSummary, Segment,
                     SegmentSummary, SummaryState)
from tracing import traceable

# 사용자 설정: config.py — REQUIRE_CORE_EVIDENCE · STEP_EFFORT["segment_summary"]


# ── ① 안건 후보 구성 ──────────────────────────────────
def select_candidates(registry: AgendaRegistry, segment: Segment) -> list[AgendaRef]:
    # 앞에서 생긴 안건을 모두 후보로 전달 (초반 안건이 후반에 다시 논의되는 경우 대비), 생성 순서(a1, a2, ...) 유지
    # segment 인자는 이후 후보 선별 규칙을 다시 넣을 경우를 위해 유지
    return list(registry.agendas)


# ── ③ 후처리 ──────────────────────────────────────────
def clean_evidence(indices: list[int], segment: Segment, where: str, log: list[str]) -> list[int]:
    # 입력으로 준 발화 범위(overlap_start~overlap_end) 밖 번호 제거 → Core 근거가 없으면 빈 목록 (항목 제외)
    given = set(indices)
    kept = sorted(i for i in given if segment.overlap_start <= i <= segment.overlap_end)
    if len(kept) < len(given):
        log.append(f"{where}: 입력 범위 밖 근거 발화 제거 {sorted(given - set(kept))}")
    if not kept:
        log.append(f"{where}: 유효한 근거 발화 없음 → 항목 제외")
        return []
    if cfg.REQUIRE_CORE_EVIDENCE and not segment.core_indices & set(kept):
        log.append(f"{where}: 근거가 Overlap 발화뿐 {kept} → 항목 제외")
        return []
    return kept


def prior_item_counters(segment_summaries: list[SegmentSummary]) -> dict[str, dict[str, int]]:
    # 안건별 · 섹션별로 이미 부여한 마지막 번호 → Segment가 달라도 같은 안건의 item_id가 겹치지 않게 이어서 부여
    counters: dict[str, dict[str, int]] = {}
    for summary in segment_summaries:
        for agenda in summary.agendas:
            c = counters.setdefault(agenda.agenda_id, {})
            for section, n in agenda.item_counters.items():
                c[section] = max(c.get(section, 0), n)
    return counters


def to_segment_summary(llm_out: LLMSegmentSummary, segment: Segment, registry: AgendaRegistry,
                       counters: dict[str, dict[str, int]], log: list[str]) -> SegmentSummary:
    # LLM 출력 → ID가 부여된 SegmentSummary (registry는 신규 안건 번호 발급에 사용)
    sid = segment.segment_id
    agendas: dict[str, Agenda] = {}
    for a_no, llm_agenda in enumerate(llm_out.agendas):
        # 근거가 유효한 항목만 남김
        items = []
        for section in SECTIONS:
            for i_no, llm_item in enumerate(getattr(llm_agenda, section)):
                evidence = clean_evidence(llm_item.evidence_utterance_indices, segment,
                                          f"s{sid} 안건{a_no}({llm_agenda.agenda_title}) {section}[{i_no}]", log)
                if evidence:
                    fields = llm_item.model_dump(exclude={"evidence_utterance_indices"})
                    items.append((section, {**fields, "evidence_utterance_indices": evidence}))
        if not items:
            log.append(f"s{sid} 안건{a_no}({llm_agenda.agenda_title}): 남은 항목 없음 → 안건 제외")
            continue

        # agenda_id 확정: Registry에 있는 ID만 인정, null · 없는 ID는 신규 발급
        ref = registry.get(llm_agenda.agenda_id) if llm_agenda.agenda_id else None
        if llm_agenda.agenda_id and ref is None:
            log.append(f"s{sid} 안건{a_no}: Registry에 없는 agenda_id '{llm_agenda.agenda_id}' → 신규 안건으로 처리")
        agenda_id = ref.agenda_id if ref else registry.new_agenda_id()

        # 같은 Segment에서 같은 안건이 두 번 나오면 하나로 모음
        agenda = agendas.get(agenda_id)
        if agenda is None:
            agenda = agendas[agenda_id] = Agenda(
                agenda_id=agenda_id,
                agenda_title=ref.agenda_title if ref else llm_agenda.agenda_title,   # 기존 안건은 Registry 제목 유지
                source_segment_ids=[sid],
                item_counters=dict(counters.get(agenda_id, {})),
            )
        for section, fields in items:
            agenda.add_item(section, **fields, source_segment_ids=[sid])
    return SegmentSummary(segment_id=sid, agendas=list(agendas.values()))


# ── ④ Registry 갱신 ───────────────────────────────────
def update_registry(registry: AgendaRegistry, summary: SegmentSummary) -> None:
    for agenda in summary.agendas:
        first, last = agenda.evidence_range
        ref = registry.get(agenda.agenda_id)
        if ref is None:
            registry.agendas.append(AgendaRef(agenda_id=agenda.agenda_id, agenda_title=agenda.agenda_title,
                                              evidence_range=[first, last]))
        else:
            ref.evidence_range = [min(ref.evidence_range[0], first), max(ref.evidence_range[1], last)]


# ── Segment 하나 처리 ─────────────────────────────────
@traceable(name="segment_summary")
def summarize_segment(segment: Segment, registry: AgendaRegistry, segment_summaries: list[SegmentSummary],
                      meeting_id: str, metadata: dict[str, str]) -> dict:
    # 입력 registry는 바꾸지 않고 갱신된 복사본을 반환 (노트북에서 같은 Segment를 다시 실행해도 안전)
    registry = registry.model_copy(deep=True)
    candidates = select_candidates(registry, segment)
    user_prompt = build_user_prompt(segment, candidates, metadata)
    llm_out = call_structured(SYSTEM_PROMPT, user_prompt, LLMSegmentSummary,
                              tag=f"{meeting_id}_segment_summary_s{segment.segment_id}", effort=cfg.STEP_EFFORT["segment_summary"])
    log: list[str] = []
    summary = to_segment_summary(llm_out, segment, registry, prior_item_counters(segment_summaries), log)
    update_registry(registry, summary)
    return {
        "summary": summary,             # ID 부여된 결과
        "registry": registry,           # 갱신된 안건 Registry
        "candidates": candidates,       # LLM에 전달한 안건 후보
        "user_prompt": user_prompt,     # LLM 입력 (확인용)
        "llm_output": llm_out,          # LLM 원본 출력 (ID 없음)
        "log": log,                     # 후처리에서 제외 · 변경된 내용
    }


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="segment_summary_node")
def segment_summary_node(state: SummaryState) -> dict:
    registry = state.get("registry") or AgendaRegistry(meeting_id=state["meeting_id"])
    summaries = list(state.get("segment_summaries", []))
    done = {s.segment_id for s in summaries}
    for segment in state["segments"]:
        if segment.segment_id in done:
            continue
        result = summarize_segment(segment, registry, summaries, state["meeting_id"], state.get("metadata", {}))
        registry = result["registry"]
        summaries.append(result["summary"])
        print(f"[1차 요약] s{segment.segment_id} (발화 {segment.utterance_start}~{segment.utterance_end}): "
              f"안건 {[a.agenda_id for a in result['summary'].agendas]} / 후보 {[c.agenda_id for c in result['candidates']]}")
        for line in result["log"]:
            print("   -", line)
    return {"registry": registry, "segment_summaries": summaries}
