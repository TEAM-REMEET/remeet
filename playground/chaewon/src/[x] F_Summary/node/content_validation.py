"""[3][5] 내용 검증 Agent

안건별 요약(agendas)을 원본 발화와 대조해 수정이 필요한 항목 · 사유 · 수정 방향(issues)을 찾음 (항목은 고치지 않음)
    ① 안건별 원본 발화 구성: 안건이 나온 Segment의 발화 (오버랩 포함) + 항목 근거 발화
    ② LLM 검증 (LLMContentValidation)
       - 1회차: prompt/content_validation_prompt.py (모든 항목)
       - 2회차~: prompt/content_recheck_prompt.py (재검증 대상 항목의 수정 전 · 수정 후 · 이전 지적)
    ③ 코드 검사: 대상 ID · 섹션 · 병합 대상 · 근거 번호 확인, current_text는 실제 문장으로 교체
       항목 하나에 이슈가 여러 개면 첫 이슈만 남김, issue_id 부여 ({agenda_id}_L{loop}_i{n})
    ④ 결과 처리
       - uncertain=true 이슈 → 대상 항목을 REVIEW_REQUIRED로 표시 (수정 방향을 단정하지 않음, 재수정 대상에서 제외)
       - 마지막 검증(config.CONTENT_MAX_LOOPS)에서도 이슈가 남으면 → 대상 항목을 REVIEW_REQUIRED로 표시하고 다음 단계로

검증 횟수
    1회차 [3]: 모든 안건의 모든 항목 검증
    2회차~ [5]: [4] 재수정에서 고친 항목(recheck_item_ids)만 재검증

노트북 확인용: validate_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: content_validation_node(state) / 분기: route_after_content_validation(state)
"""

import config as cfg
from llm import call_structured
import prompt.content_recheck_prompt as recheck_prompt
from prompt.content_validation_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import Agenda, ContentIssue, LLMContentValidation, Segment, SummaryState
from tracing import traceable

# 사용자 설정: config.py — CONTENT_MAX_LOOPS · INCLUDE_OVERLAP · STEP_EFFORT["content_validation"]

# 검증 후 다음 노드 이름 (summary_graph_main.py의 노드 이름과 맞춤)
REVISE_NODE = "content_revise"
NEXT_NODE = "readability_validation"


# ── ① 안건별 원본 발화 ────────────────────────────────
def source_utterances(agenda: Agenda, segments: list[Segment], utterance_texts: dict[int, str]) -> list[tuple[int, str]]:
    seg_ids = set(agenda.source_segment_ids)
    indices: set[int] = set()
    for s in segments:
        if s.segment_id in seg_ids:
            start, end = (s.overlap_start, s.overlap_end) if cfg.INCLUDE_OVERLAP else (s.utterance_start, s.utterance_end)
            indices.update(range(start, end + 1))
    indices.update(i for _, item in agenda.iter_items() for i in item.evidence_utterance_indices)   # 범위 밖 근거도 포함
    return [(i, utterance_texts[i]) for i in sorted(indices) if i in utterance_texts]


# ── ③ 코드 검사 ───────────────────────────────────────
def check_issues(agenda: Agenda, llm_out: LLMContentValidation, utterance_texts: dict[int, str], loop: int,
                 recheck_ids: list[str], log: list[str]) -> list[ContentIssue]:
    aid = agenda.agenda_id
    active = {item.item_id: (section, item) for section, item in agenda.iter_items() if item.status == "ACTIVE"}
    issues, seen = [], set()
    for n, raw in enumerate(llm_out.issues):
        issue = raw.model_copy(deep=True)
        where = f"{aid} 이슈{n}({issue.action} {issue.target_item_id})"

        if issue.action == "추가":
            if recheck_ids:
                log.append(f"{where}: 재검증에서는 추가 요청을 받지 않음 → 제외")
                continue
            if issue.target_item_id != aid:
                log.append(f"{where}: 추가 대상은 안건 ID여야 함 → {aid}로 변경")
                issue.target_item_id = aid
            issue.current_text = ""
        else:
            if issue.target_item_id not in active:
                log.append(f"{where}: 없는(또는 삭제 · 검토 필요 상태인) 항목 ID → 제외")
                continue
            if recheck_ids and issue.target_item_id not in recheck_ids:
                log.append(f"{where}: 재검증 대상이 아닌 항목 → 제외")
                continue
            if issue.target_item_id in seen:
                log.append(f"{where}: 같은 항목에 대한 이슈가 이미 있음 → 제외")
                continue
            seen.add(issue.target_item_id)
            section, item = active[issue.target_item_id]
            if issue.section != section:
                log.append(f"{where}: 섹션 {issue.section} → 실제 섹션 {section}로 변경")
                issue.section = section
            issue.current_text = item.text

        related = [i for i in dict.fromkeys(issue.related_item_ids) if i != issue.target_item_id]
        if issue.action == "병합":
            bad = [i for i in related if i not in active or active[i][0] != issue.section]
            related = [i for i in related if i not in bad]
            if bad:
                log.append(f"{where}: 병합 대상 중 없는 · 다른 섹션 항목 제외 {bad}")
            if not related:
                log.append(f"{where}: 병합할 항목 없음 → 제외")
                continue
        issue.related_item_ids = related

        invalid = [i for i in issue.evidence_utterance_indices if i not in utterance_texts]
        if invalid:
            log.append(f"{where}: 없는 발화 번호 제거 {invalid}")
            issue.evidence_utterance_indices = [i for i in issue.evidence_utterance_indices if i in utterance_texts]

        issues.append(ContentIssue(**issue.model_dump(), issue_id=f"{aid}_L{loop}_i{len(issues) + 1}",
                                   agenda_id=aid, loop=loop))

    if llm_out.validation_result == "PASS" and issues:
        log.append(f"{aid}: PASS로 반환했지만 이슈 {len(issues)}개 있음 → 이슈 기준으로 처리")
    return issues


# ── 안건 하나 검증 ────────────────────────────────────
@traceable(name="content_validation")
def validate_agenda(agenda: Agenda, segments: list[Segment], utterance_texts: dict[int, str], meeting_id: str,
                    loop: int = 1, recheck_ids: list[str] | None = None,
                    previous_issues: list[ContentIssue] | None = None) -> dict:
    recheck_ids = recheck_ids or []
    utterances = source_utterances(agenda, segments, utterance_texts)
    if recheck_ids:     # 2회차~ 재검증
        system = recheck_prompt.SYSTEM_PROMPT
        user_prompt = recheck_prompt.build_user_prompt(agenda, utterances, recheck_ids, previous_issues or [])
    else:               # 1회차 전체 검증
        system, user_prompt = SYSTEM_PROMPT, build_user_prompt(agenda, utterances)
    llm_out = call_structured(system, user_prompt, LLMContentValidation,
                              tag=f"{meeting_id}_content_validation_{agenda.agenda_id}_loop{loop}", effort=cfg.STEP_EFFORT["content_validation"])
    log: list[str] = []
    issues = check_issues(agenda, llm_out, utterance_texts, loop, recheck_ids, log)
    return {
        "issues": issues,               # 코드 검사를 통과한 이슈
        "user_prompt": user_prompt,     # LLM 입력 (확인용)
        "llm_output": llm_out,          # LLM 원본 출력
        "log": log,                     # 코드 검사에서 제외 · 변경된 내용
    }


def mark_review_required(agendas: list[Agenda], issues: list[ContentIssue]) -> list[str]:
    # 이슈 대상 항목을 REVIEW_REQUIRED로 표시 (추가 요청은 대상 항목이 없으므로 표시 없이 이슈 기록만 남음)
    marked = []
    by_id = {a.agenda_id: a for a in agendas}
    for issue in issues:
        item = by_id[issue.agenda_id].find_item(issue.target_item_id) if issue.action != "추가" else None
        if item is not None and item.status == "ACTIVE":
            item.status = "REVIEW_REQUIRED"
            marked.append(item.item_id)
    return marked


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="content_validation_node")
def content_validation_node(state: SummaryState) -> dict:
    loop = state.get("content_loop", 0) + 1
    recheck = state.get("recheck_item_ids", [])
    issue_log = list(state.get("content_issue_log", []))
    previous = [i for i in issue_log if i.loop == loop - 1]
    agendas = [a.model_copy(deep=True) for a in state["agendas"]]

    issues: list[ContentIssue] = []
    for agenda in agendas:
        targets = [] if loop == 1 else [i for i in recheck if agenda.find_item(i) is not None]
        if loop > 1 and not targets:
            continue
        result = validate_agenda(agenda, state["segments"], state["utterance_texts"], state["meeting_id"],
                                 loop, targets, previous)
        issues += result["issues"]
        print(f"[내용 검증 {loop}회차] {agenda.agenda_id} {agenda.agenda_title}: "
              f"{result['llm_output'].validation_result} / 이슈 {len(result['issues'])}개"
              + (f" (재검증 대상 {targets})" if targets else ""))
        for line in result["log"]:
            print("   -", line)

    uncertain = [i for i in issues if i.uncertain]
    pending = [i for i in issues if not i.uncertain]
    marked = mark_review_required(agendas, uncertain)
    if pending and loop >= cfg.CONTENT_MAX_LOOPS:
        marked += mark_review_required(agendas, pending)
        print(f"[내용 검증] 최대 {cfg.CONTENT_MAX_LOOPS}회 도달 — 남은 이슈 {len(pending)}개 → 재수정 없이 다음 단계로")
        pending = []
    if marked:
        print(f"[내용 검증] REVIEW_REQUIRED 표시: {marked}")

    return {
        "agendas": agendas,
        "content_loop": loop,
        "content_issues": pending,                  # [4] 재수정 대상
        "content_issue_log": issue_log + issues,    # 전체 이력 (uncertain 포함)
        "recheck_item_ids": [],                     # [4] 재수정에서 다시 채움
    }


def route_after_content_validation(state: SummaryState) -> str:
    return REVISE_NODE if state.get("content_issues") else NEXT_NODE
