"""[7] 가독성 피드백 기반 부분 수정

[6] 가독성 검증 이슈(readability_issues)의 수정 방향에 맞게 지적된 문장만 다시 씀
    ① 안건별 LLM 1회 (LLMReadabilityRevision) — 요청 ID = 대상 항목 ID
    ② 코드 검사 (통과한 문장만 반영)
       - 요청에 없는 ID · 중복 결과 제외
       - 수정 문장이 비었거나 현재 문장과 같으면 제외
       - 숫자 보존: 수정 전후 숫자가 다르면 제외 (사실관계 변경 방지)
       - 길이 제한: 수정 문장이 현재 문장의 config.MAX_LENGTH_RATIO배를 넘으면 제외 (원문에 없는 내용 추가 방지)
       - 검증 이후 문장이 바뀌었거나 삭제된 항목이면 제외
    ③ item_id 기준으로 반영: 문장만 바꾸고 근거 발화 · 태그 · 소주제 · 상태는 그대로
       (수정 전 문장은 history, stage="readability_revise")
       반영된 이슈: applied=True, revised_text 기록 / 반영된 항목 ID → readability_recheck_ids ([8] 재검증 대상)
       반영하지 못한 이슈는 기존 문장 유지 (가독성 문제는 REVIEW_REQUIRED로 표시하지 않음)

노트북 확인용: revise_agenda(...)로 안건 하나씩 실행 가능
LangGraph 노드: readability_revise_node(state) → 다음 노드는 항상 readability_validation
"""

import re
from collections import Counter

import config as cfg
from llm import call_structured
from node.readability_validation import evidence_utterances
from prompt.readability_revise_prompt import SYSTEM_PROMPT, build_user_prompt
from schemas import Agenda, LLMReadabilityRevision, ReadabilityIssue, SummaryState
from tracing import traceable

# 사용자 설정: config.py — CHECK_NUMBERS · MAX_LENGTH_RATIO · STEP_EFFORT["readability_revise"]

# ── 내부 상수 ────────────────────────────────────────
STAGE = "readability_revise"         # 항목 history에 남길 단계 이름

NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


# ── ② 코드 검사 ───────────────────────────────────────
def check_revision(issue: ReadabilityIssue, current: str, revised: str) -> str | None:
    # 반영할 수 없는 이유 반환 (문제없으면 None)
    if not revised or revised == current:
        return "수정 문장이 비었거나 현재 문장과 같음"
    if cfg.CHECK_NUMBERS and Counter(NUMBER_PATTERN.findall(current)) != Counter(NUMBER_PATTERN.findall(revised)):
        return f"수정 전후 숫자가 다름 ({current!r} → {revised!r})"
    if cfg.MAX_LENGTH_RATIO and len(revised) > len(current) * cfg.MAX_LENGTH_RATIO:
        return f"수정 문장이 {cfg.MAX_LENGTH_RATIO}배 이상 길어짐"
    if current != issue.current_text:
        return "검증 이후 문장이 바뀜"
    return None


# ── 안건 하나 수정 ────────────────────────────────────
@traceable(name="readability_revise")
def revise_agenda(agenda: Agenda, issues: list[ReadabilityIssue], utterance_texts: dict[int, str],
                  meeting_id: str, loop: int) -> dict:
    # agenda를 직접 수정함 (노드에서는 복사본을 넘김)
    log: list[str] = []
    issues = [i for i in issues if (it := agenda.find_item(i.target_item_id)) is not None and it.status != "DELETED"]
    user_prompt = build_user_prompt(agenda, issues, evidence_utterances(agenda, utterance_texts))
    llm_out = call_structured(SYSTEM_PROMPT, user_prompt, LLMReadabilityRevision,
                              tag=f"{meeting_id}_readability_revise_{agenda.agenda_id}_loop{loop}", effort=cfg.STEP_EFFORT["readability_revise"])

    requests = {i.target_item_id: i for i in issues}
    revisions: dict[str, str] = {}
    for rev in llm_out.revision_result:
        if rev.target_item_id not in requests:
            log.append(f"{agenda.agenda_id}: 요청에 없는 ID {rev.target_item_id} → 제외")
        elif rev.target_item_id in revisions:
            log.append(f"{agenda.agenda_id}: {rev.target_item_id} 결과 중복 → 첫 결과만 사용")
        else:
            revisions[rev.target_item_id] = rev.revised_text.strip()

    applied: dict[str, str] = {}        # issue_id → 반영한 문장
    for item_id, issue in requests.items():
        where = f"{agenda.agenda_id} {issue.issue_id}({item_id})"
        if item_id not in revisions:
            log.append(f"{where}: LLM 결과 없음 → 기존 문장 유지")
            continue
        item = agenda.find_item(item_id)
        problem = check_revision(issue, item.text, revisions[item_id])
        if problem:
            log.append(f"{where}: {problem} → 기존 문장 유지")
            continue
        item.revise(revisions[item_id], item.evidence_utterance_indices, STAGE, loop, f"[{issue.criterion}] {issue.reason}")
        applied[issue.issue_id] = item.text
        log.append(f"{where}: 반영")

    return {
        "applied": applied,                                 # issue_id → 반영한 문장
        "recheck_item_ids": [i.target_item_id for i in issues if i.issue_id in applied],
        "user_prompt": user_prompt,                         # LLM 입력 (확인용)
        "llm_output": llm_out,                              # LLM 원본 출력
        "log": log,                                         # 반영 · 제외 내용
    }


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="readability_revise_node")
def readability_revise_node(state: SummaryState) -> dict:
    loop = state.get("readability_loop", 0)             # 이슈가 나온 검증 회차
    pending = state.get("readability_issues", [])
    agendas = [a.model_copy(deep=True) for a in state["agendas"]]

    applied: dict[str, str] = {}
    recheck: list[str] = []
    for agenda in agendas:
        issues = [i for i in pending if i.agenda_id == agenda.agenda_id]
        if not issues:
            continue
        result = revise_agenda(agenda, issues, state["utterance_texts"], state["meeting_id"], loop)
        applied.update(result["applied"])
        recheck += result["recheck_item_ids"]
        print(f"[가독성 수정 {loop}회차] {agenda.agenda_id} {agenda.agenda_title}: 이슈 {len(issues)}개 중 "
              f"{len(result['applied'])}개 반영 / 재검증 대상 {result['recheck_item_ids']}")
        for line in result["log"]:
            print("   -", line)

    issue_log = [i.model_copy(update={"applied": True, "revised_text": applied[i.issue_id]}) if i.issue_id in applied else i
                 for i in state.get("readability_issue_log", [])]
    return {
        "agendas": agendas,
        "readability_issues": [],
        "readability_issue_log": issue_log,
        "readability_recheck_ids": recheck,         # [8] 재검증 대상
    }
