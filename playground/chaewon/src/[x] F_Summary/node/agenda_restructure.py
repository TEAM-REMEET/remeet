"""[2-1] 안건 재구성 (agenda_split 변형)

[2] 병합 결과 안건이 적고(1~3개) 한 안건에 소주제 · 항목이 많으면, 그 안건의 항목을
'안건 목록으로 핵심 논의 사항과 흐름을 이해할 수 있는' 단위의 안건으로 다시 묶음 (항목 문장은 그대로)
    ① 재구성 대상 선정: 전체 안건 ≤ RESTRUCTURE_MAX_TOTAL_AGENDAS 이고,
       소주제 ≥ RESTRUCTURE_MIN_SUBTOPICS 또는 항목 ≥ RESTRUCTURE_MIN_ITEMS인 안건
    ② LLM 1회 (안건 하나당, LLMAgendaPlan): 새 안건 제목 + 항목 ID 배정
    ③ 코드 검사
       - 없는 ID · 중복 배정 제외, 빈 안건 제외
       - 배정되지 않은 항목(삭제된 항목 포함)은 근거 발화가 가장 가까운 안건에 넣음
       - 나뉜 안건이 1개 이하이거나 RESTRUCTURE_MAX_AGENDAS 초과면 재구성하지 않음
    ④ 새 안건 생성: 새 agenda_id(Registry 번호) · 새 item_id 부여, 이전 ID는 origin_item_ids와 history(stage="restructure")에 남김
       Registry를 새 안건 기준으로 갱신, 전체 안건을 처음 논의된 순서로 정렬 (SORT_AGENDAS_BY_FLOW)

노트북 확인용: restructure_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: agenda_restructure_node(state) → item_consolidation
"""

import config as cfg
from llm import call_structured
from prompt.agenda_restructure_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import SECTIONS, Agenda, AgendaRef, AgendaRegistry, ItemRevision, LLMAgendaPlan, SummaryItem, SummaryState
from tracing import traceable

# 사용자 설정: config.py — RESTRUCTURE_* · SORT_AGENDAS_BY_FLOW · STEP_EFFORT["agenda_restructure"]

# ── 내부 상수 ────────────────────────────────────────
STAGE = "restructure"


# ── ① 재구성 대상 ─────────────────────────────────────
def visible_count(agenda: Agenda) -> int:
    return sum(1 for _ in agenda.iter_items())


def subtopic_count(agenda: Agenda) -> int:
    return len({it.subtopic.strip() for it in agenda.major_discussions if it.status != "DELETED"})


def needs_restructure(agenda: Agenda, total_agendas: int) -> bool:
    if total_agendas > cfg.RESTRUCTURE_MAX_TOTAL_AGENDAS:
        return False
    return subtopic_count(agenda) >= cfg.RESTRUCTURE_MIN_SUBTOPICS or visible_count(agenda) >= cfg.RESTRUCTURE_MIN_ITEMS


def target_range(agenda: Agenda, targets: list[Agenda]) -> tuple[int, int]:
    # 회의 전체 목표 안건 수를 재구성 대상 안건들의 항목 수 비율로 나눔
    share = visible_count(agenda) / max(1, sum(visible_count(a) for a in targets))
    lo, hi = cfg.RESTRUCTURE_TARGET_AGENDAS
    lo, hi = max(1, round(lo * share)), max(2, round(hi * share))
    return min(lo, hi), min(hi, cfg.RESTRUCTURE_MAX_AGENDAS)


# ── ③ 코드 검사 ───────────────────────────────────────
def first_evidence(item: SummaryItem) -> int:
    return item.evidence_utterance_indices[0] if item.evidence_utterance_indices else -1


def assign_items(agenda: Agenda, plan: LLMAgendaPlan, log: list[str]) -> list[tuple[str, list[tuple[str, SummaryItem]]]]:
    # [(새 안건 제목, [(섹션, 항목)])] — 모든 항목(삭제 항목 포함)을 정확히 한 안건에 배정
    all_items = {it.item_id: (section, it) for section, it in agenda.iter_items(include_deleted=True)}
    visible = {it.item_id for _, it in agenda.iter_items()}
    groups, used = [], set()
    for n, g in enumerate(plan.agendas):
        ids = []
        for item_id in g.item_ids:
            if item_id not in visible:
                log.append(f"안건{n}({g.agenda_title}): 없는(또는 삭제된) 항목 ID {item_id} → 제외")
            elif item_id in used:
                log.append(f"안건{n}({g.agenda_title}): {item_id} 중복 배정 → 앞 안건에만 둠")
            else:
                ids.append(item_id)
                used.add(item_id)
        if not ids:
            log.append(f"안건{n}({g.agenda_title}): 배정된 항목 없음 → 제외")
            continue
        groups.append((g.agenda_title.strip() or agenda.agenda_title, [all_items[i] for i in ids]))

    # 배정되지 않은 항목 → 근거 발화가 가장 가까운 안건
    missing = [all_items[i] for i in all_items if i not in used]
    for section, item in missing:
        if not groups:
            break
        pos = first_evidence(item)
        best = min(range(len(groups)), key=lambda k: min(abs(first_evidence(it) - pos) for _, it in groups[k][1]))
        groups[best][1].append((section, item))
        if item.status != "DELETED":
            log.append(f"{item.item_id}: 배정되지 않음 → 근거 발화가 가장 가까운 안건 '{groups[best][0]}'에 넣음")
    return groups


# ── ④ 새 안건 생성 ────────────────────────────────────
def build_agendas(source: Agenda, groups: list, registry: AgendaRegistry) -> list[Agenda]:
    agendas = []
    for title, members in groups:
        new = Agenda(agenda_id=registry.new_agenda_id(), agenda_title=title)
        for section in SECTIONS:
            for _, item in sorted(((s, it) for s, it in members if s == section), key=lambda x: (first_evidence(x[1]), x[1].item_id)):
                data = item.model_dump(exclude={"item_id"})
                data["origin_item_ids"] = [*item.origin_item_ids, item.item_id]
                moved = new.add_item(section, **data)
                moved.history.append(ItemRevision(stage=STAGE, before_text=item.text,
                                                  before_evidence=item.evidence_utterance_indices,
                                                  before_tag=getattr(item, "tag", None),
                                                  before_subtopic=getattr(item, "subtopic", None),
                                                  reason=f"안건 재구성: {source.agenda_id} → {new.agenda_id} ({item.item_id} → {moved.item_id})"))
        new.source_segment_ids = sorted({s for _, it in new.iter_items(include_deleted=True) for s in it.source_segment_ids})
        agendas.append(new)
    return agendas


# ── 안건 하나 재구성 ──────────────────────────────────
@traceable(name="agenda_restructure")
def restructure_agenda(agenda: Agenda, registry: AgendaRegistry, meeting_id: str, metadata: dict[str, str],
                       target: tuple[int, int]) -> dict:
    # registry는 새 안건 번호 부여로 바뀜 (노드에서는 복사본을 넘김)
    log: list[str] = []
    user_prompt = build_user_prompt(agenda, metadata, target, cfg.RESTRUCTURE_MAX_AGENDAS)
    llm_out = call_structured(SYSTEM_PROMPT, user_prompt, LLMAgendaPlan,
                              tag=f"{meeting_id}_agenda_restructure_{agenda.agenda_id}",
                              effort=cfg.STEP_EFFORT["agenda_restructure"])
    groups = assign_items(agenda, llm_out, log)
    agendas = [agenda]
    if len(groups) <= 1:
        log.append(f"{agenda.agenda_id}: 나뉜 안건이 {len(groups)}개 → 재구성하지 않음")
    elif len(groups) > cfg.RESTRUCTURE_MAX_AGENDAS:
        log.append(f"{agenda.agenda_id}: 나뉜 안건이 {len(groups)}개로 최대 {cfg.RESTRUCTURE_MAX_AGENDAS}개 초과 → 재구성하지 않음")
    else:
        agendas = build_agendas(agenda, groups, registry)
    return {
        "agendas": agendas,             # 재구성된 안건 (재구성하지 않으면 원래 안건)
        "user_prompt": user_prompt,     # LLM 입력 (확인용)
        "llm_output": llm_out,          # LLM 원본 출력
        "log": log,                     # 코드 검사에서 제외 · 변경된 내용
    }


def update_registry(registry: AgendaRegistry, agendas: list[Agenda]) -> None:
    # Registry를 현재 안건 목록 기준으로 다시 씀 (안건 번호 next_number는 유지 → 번호 재사용 안 함)
    registry.agendas = [AgendaRef(agenda_id=a.agenda_id, agenda_title=a.agenda_title, evidence_range=a.evidence_range)
                        for a in agendas if a.evidence_range]


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="agenda_restructure_node")
def agenda_restructure_node(state: SummaryState) -> dict:
    agendas = [a.model_copy(deep=True) for a in state["agendas"]]
    registry = state["registry"].model_copy(deep=True)
    targets = [a for a in agendas if needs_restructure(a, len(agendas))]
    target_ids = {id(a) for a in targets}
    if not targets:
        print(f"[안건 재구성] 대상 없음 (안건 {len(agendas)}개 — 기준: 안건 ≤ {cfg.RESTRUCTURE_MAX_TOTAL_AGENDAS}개, "
              f"소주제 ≥ {cfg.RESTRUCTURE_MIN_SUBTOPICS}개 또는 항목 ≥ {cfg.RESTRUCTURE_MIN_ITEMS}개)")
        return {}

    result_agendas = []
    for agenda in agendas:
        if id(agenda) not in target_ids:
            result_agendas.append(agenda)
            continue
        target = target_range(agenda, targets)
        result = restructure_agenda(agenda, registry, state["meeting_id"], state.get("metadata", {}), target)
        result_agendas += result["agendas"]
        print(f"[안건 재구성] {agenda.agenda_id} {agenda.agenda_title} (소주제 {subtopic_count(agenda)}개 · 항목 "
              f"{visible_count(agenda)}개, 목표 {target[0]}~{target[1]}개) → 안건 {len(result['agendas'])}개")
        for a in result["agendas"]:
            if a is not agenda:
                print(f"   + {a.agenda_id} {a.agenda_title}: 항목 {visible_count(a)}개 (소주제 {subtopic_count(a)}개)")
        for line in result["log"]:
            print("   -", line)

    if cfg.SORT_AGENDAS_BY_FLOW:
        result_agendas.sort(key=lambda a: a.evidence_range[0] if a.evidence_range else float("inf"))
    update_registry(registry, result_agendas)
    return {"agendas": result_agendas, "registry": registry}
