"""[6], [8] 가독성 검증 Agent

내용 검증을 마친 안건별 요약(agendas)에서 가독성 개선이 필요한 문장 · 이유 · 수정 방향(issues)을 찾음 (문장은 고치지 않음 → [7])
    ① 안건별 근거 발화 구성: 항목 근거 발화 (의미 확인용, 앞뒤 config.EVIDENCE_CONTEXT개 포함)
    ② LLM 검증 (LLMReadabilityValidation)
       - 1회차: prompt/readability_validation_prompt.py (삭제되지 않은 모든 항목)
       - 2회차~: prompt/readability_recheck_prompt.py ([7]에서 다듬은 항목의 수정 전 · 수정 후 · 이전 지적)
    ③ 코드 검사: 대상 ID 확인 (삭제된 항목 · 재검증 대상이 아닌 항목 제외), 항목 하나에 첫 이슈만,
       current_text는 실제 문장으로 교체, issue_id 부여 ({agenda_id}_R{loop}_i{n})
    ④ 마지막 검증(config.READABILITY_MAX_LOOPS)에서도 이슈가 남으면 → 수정하지 않고 다음 단계로 (재검증 없이 바꾸지 않음)

검증 횟수
    1회차: 모든 안건의 모든 항목 검증
    2회차~: [7] 가독성 수정에서 고친 항목(readability_recheck_ids)만 재검증
수정 문장 검사(숫자 보존 · 길이 제한)는 [7] readability_revise.py에서 수행

노트북 확인용: validate_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: readability_validation_node(state) / 분기: route_after_readability_validation(state)
"""

import config as cfg
import prompt.readability_recheck_prompt as recheck_prompt
from llm import call_structured
from prompt.readability_validation_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import Agenda, LLMReadabilityValidation, ReadabilityIssue, SummaryState
from tracing import traceable

# 사용자 설정: config.py — READABILITY_MAX_LOOPS · EVIDENCE_CONTEXT · STEP_EFFORT["readability_validation"]

# 검증 후 다음 노드 이름 (summary_graph_main.py의 노드 이름과 맞춤)
REVISE_NODE = "readability_revise"
NEXT_NODE = "structure_validation"


# ── ① 안건별 근거 발화 ────────────────────────────────
def evidence_utterances(agenda: Agenda, utterance_texts: dict[int, str]) -> list[tuple[int, str]]:
    indices = {j for _, item in agenda.iter_items() for i in item.evidence_utterance_indices
               for j in range(i - cfg.EVIDENCE_CONTEXT, i + cfg.EVIDENCE_CONTEXT + 1)}
    return [(i, utterance_texts[i]) for i in sorted(indices) if i in utterance_texts]


# ── ③ 코드 검사 ───────────────────────────────────────
def check_issues(agenda: Agenda, llm_out: LLMReadabilityValidation, loop: int, recheck_ids: list[str],
                 log: list[str]) -> list[ReadabilityIssue]:
    aid = agenda.agenda_id
    visible = {item.item_id: item for _, item in agenda.iter_items()}     # 삭제되지 않은 항목
    issues, seen = [], set()
    for n, raw in enumerate(llm_out.issues):
        issue = raw.model_copy(deep=True)
        where = f"{aid} 이슈{n}({issue.target_item_id})"
        item = visible.get(issue.target_item_id)
        if item is None:
            log.append(f"{where}: 없는(또는 삭제된) 항목 ID → 제외")
            continue
        if recheck_ids and issue.target_item_id not in recheck_ids:
            log.append(f"{where}: 재검증 대상이 아닌 항목 → 제외")
            continue
        if issue.target_item_id in seen:
            log.append(f"{where}: 같은 항목에 대한 이슈가 이미 있음 → 제외")
            continue
        issue.current_text = item.text
        seen.add(issue.target_item_id)
        issues.append(ReadabilityIssue(**issue.model_dump(), issue_id=f"{aid}_R{loop}_i{len(issues) + 1}",
                                       agenda_id=aid, loop=loop))

    if llm_out.validation_result == "PASS" and issues:
        log.append(f"{aid}: PASS로 반환했지만 이슈 {len(issues)}개 있음 → 이슈 기준으로 처리")
    return issues


# ── 안건 하나 검증 ────────────────────────────────────
@traceable(name="readability_validation")
def validate_agenda(agenda: Agenda, utterance_texts: dict[int, str], meeting_id: str, loop: int = 1,
                    recheck_ids: list[str] | None = None,
                    previous_issues: list[ReadabilityIssue] | None = None) -> dict:
    recheck_ids = recheck_ids or []
    evidence = evidence_utterances(agenda, utterance_texts)
    if recheck_ids:     # 2회차~ 재검증
        system = recheck_prompt.SYSTEM_PROMPT
        user_prompt = recheck_prompt.build_user_prompt(agenda, evidence, recheck_ids, previous_issues or [])
    else:               # 1회차 전체 검증
        system, user_prompt = SYSTEM_PROMPT, build_user_prompt(agenda, evidence)
    llm_out = call_structured(system, user_prompt, LLMReadabilityValidation,
                              tag=f"{meeting_id}_readability_validation_{agenda.agenda_id}_loop{loop}", effort=cfg.STEP_EFFORT["readability_validation"])
    log: list[str] = []
    issues = check_issues(agenda, llm_out, loop, recheck_ids, log)
    return {
        "issues": issues,               # 코드 검사를 통과한 이슈
        "user_prompt": user_prompt,     # LLM 입력 (확인용)
        "llm_output": llm_out,          # LLM 원본 출력
        "log": log,                     # 코드 검사에서 제외 · 변경된 내용
    }


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="readability_validation_node")
def readability_validation_node(state: SummaryState) -> dict:
    loop = state.get("readability_loop", 0) + 1
    recheck = state.get("readability_recheck_ids", [])
    issue_log = list(state.get("readability_issue_log", []))
    previous = [i for i in issue_log if i.loop == loop - 1]

    issues: list[ReadabilityIssue] = []
    for agenda in state["agendas"]:
        if not any(True for _ in agenda.iter_items()):
            continue
        targets = [] if loop == 1 else [i for i in recheck if agenda.find_item(i) is not None]
        if loop > 1 and not targets:
            continue
        result = validate_agenda(agenda, state["utterance_texts"], state["meeting_id"], loop, targets, previous)
        issues += result["issues"]
        print(f"[가독성 검증 {loop}회차] {agenda.agenda_id} {agenda.agenda_title}: "
              f"{result['llm_output'].validation_result} / 이슈 {len(result['issues'])}개"
              + (f" (재검증 대상 {targets})" if targets else ""))
        for line in result["log"]:
            print("   -", line)

    pending = issues
    if pending and loop >= cfg.READABILITY_MAX_LOOPS:
        print(f"[가독성 검증] 최대 {cfg.READABILITY_MAX_LOOPS}회 도달 — 남은 이슈 {len(pending)}개는 수정하지 않고 다음 단계로")
        pending = []

    return {
        "readability_loop": loop,
        "readability_issues": pending,                  # [7] 가독성 수정 대상
        "readability_issue_log": issue_log + issues,    # 전체 이력
        "readability_recheck_ids": [],                  # [7] 가독성 수정에서 다시 채움
    }


def route_after_readability_validation(state: SummaryState) -> str:
    return REVISE_NODE if state.get("readability_issues") else NEXT_NODE
