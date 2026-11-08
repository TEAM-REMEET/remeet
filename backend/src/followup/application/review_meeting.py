"""사용자 수정(HITL) 반영 → 근거 연결 · 검증 → 최종 JSON 저장 (LangGraph)

요약 · 액션 추출(summarize_meeting.py)과 분리된 그래프. 사용자 검토가 언제 끝날지 모르므로 저장된 summary.json에서 다시 시작함.

    START
      │
    apply_edits         사용자 값 반영 — 생성 · 변경(액션명 · 일시 · 담당자) · 삭제
      │
      ├─ 대상 없음 (삭제만 · 수정 없음) ───────────────────────▶ finalize ─▶ save ─▶ END
      │
    link_evidence ◀─┐   [LLM] 근거 발화 번호만 연결 (prompts/link_evidence.py)
      │             │
    verify_evidence │   [LLM] 근거가 적절한지 검증 (prompts/verify_evidence.py)
      │             │
      ├─ 실패 · 회차 < MAX ─┘ 실패 항목만 다시 연결
      ├─ 통과 ──────────────────────────────────────────────────▶ finalize
      └─ 실패 · 회차 = MAX → clear_evidence ───────────────────▶ finalize
                             남은 문제 항목의 근거를 빈 배열로 (액션은 유지, REVIEW_REQUIRED로 표시하지 않음)

모델이 바꿀 수 있는 값은 근거 발화뿐: 출력 스키마에 다른 칸이 없고, 코드가 대상 항목의 근거 필드에만 반영함.
수정 목록이 잘못되면 그래프 실행 전(validate_edits)에 중단하므로 LLM을 호출하지 않음.
여러 번 수정해도 원본 summary.json에서 다시 시작하므로, 수정 목록에는 지금까지의 수정을 모두 담아야 함.
실패 후 같은 수정 목록으로 다시 요청하면 저장된 단계별 응답을 재사용해 실패한 단계부터 진행됨.
"""

import logging
from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from followup import settings as cfg
from followup.analysis.summary import hitl, ui_test
from followup.analysis.summary.schemas import (AssignChanges, HitlEditRecord, HitlEdits, HitlEvidenceIssue,
                                               HitlEvidenceRound, HitlResult, MeetingSummary)
from followup.application.ports import StructuredLLM, SummaryStore
from followup.application.summarize_meeting import _MEETING_ID, _StepRunner
from followup.application.time_limit import run_with_limit

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 15    # 노드 실행 상한 (정상 최대: 반영 1 + 연결 3 + 검증 3 + 비움 1 + 마무리 1 + 저장 1 = 10)


class MeetingNotFoundError(LookupError):
    """저장된 summary.json이 없음"""


class ReviewState(TypedDict, total=False):
    summary: MeetingSummary
    records: list[HitlEditRecord]
    targets: list[str]                  # 이번 회차에 근거를 연결할 항목
    round: int                          # 진행한 근거 검증 횟수
    issues: list[HitlEvidenceIssue]     # 마지막 근거 검증의 문제
    rounds: list[HitlEvidenceRound]


def _build_graph(edits: HitlEdits, runner: _StepRunner, store: SummaryStore, save: bool):
    def node_apply_edits(state: ReviewState) -> dict:
        summary = state["summary"]
        records, targets = hitl.apply_edits(summary, edits)
        logger.info("[%s] 사용자 수정 %d건 반영 · 근거 연결 대상 %d건", summary.meeting_id, len(records), len(targets))
        return {"summary": summary, "records": records, "targets": targets}

    def node_link_evidence(state: ReviewState) -> dict:
        summary = state["summary"]
        notes = hitl.link_evidence(summary, state["targets"], state["round"] + 1, state["issues"], runner)
        for note in notes:
            logger.info("[%s] 근거 연결: %s", summary.meeting_id, note)
        return {"summary": summary}

    def node_verify_evidence(state: ReviewState) -> dict:
        summary, targets, round_no = state["summary"], state["targets"], state["round"] + 1
        issues = hitl.verify_evidence(summary, targets, round_no, runner)
        logger.info("[%s] 근거 검증 %d/%d: %s", summary.meeting_id, round_no, cfg.HITL_MAX_VERIFY_ROUNDS,
                    "통과" if not issues else f"문제 {len(issues)}건")
        rounds = state["rounds"] + [HitlEvidenceRound(round=round_no, target_item_ids=list(targets), passed=not issues,
                                                      issues=issues)]
        failed = list(dict.fromkeys(i.item_id for i in issues))
        return {"round": round_no, "issues": issues, "rounds": rounds, "targets": failed or targets}

    def node_clear_evidence(state: ReviewState) -> dict:
        summary = state["summary"]
        hitl.clear_unverified(summary, state["issues"], state["round"])
        return {"summary": summary}

    def node_finalize(state: ReviewState) -> dict:
        summary, records = state["summary"], state["records"]
        hitl.finish_records(summary, records, {i.item_id for i in state["issues"]})
        summary.hitl = HitlResult(status="COMPLETED" if records else "NO_CHANGES",
                                  applied_at=datetime.now().isoformat(timespec="seconds"),
                                  max_rounds=cfg.HITL_MAX_VERIFY_ROUNDS, edits=records, rounds=state["rounds"])
        return {"summary": summary}

    def node_save(state: ReviewState) -> dict:
        summary = state["summary"]
        location = store.save_final(summary)
        store.clear_steps(summary.meeting_id)
        logger.info("[%s] 최종 저장: %s", summary.meeting_id, location)
        return {}

    def route_after_apply(state: ReviewState) -> str:
        return "link_evidence" if state["targets"] else "finalize"

    def route_after_verify(state: ReviewState) -> str:
        if not state["issues"]:
            return "finalize"
        return "clear_evidence" if state["round"] >= cfg.HITL_MAX_VERIFY_ROUNDS else "link_evidence"

    def route_after_finalize(state: ReviewState) -> str:
        return "save" if save else END

    g = StateGraph(ReviewState)
    g.add_node("apply_edits", node_apply_edits)
    g.add_node("link_evidence", node_link_evidence)
    g.add_node("verify_evidence", node_verify_evidence)
    g.add_node("clear_evidence", node_clear_evidence)
    g.add_node("finalize", node_finalize)
    g.add_node("save", node_save)
    g.add_edge(START, "apply_edits")
    g.add_conditional_edges("apply_edits", route_after_apply, ["link_evidence", "finalize"])
    g.add_edge("link_evidence", "verify_evidence")
    g.add_conditional_edges("verify_evidence", route_after_verify, ["link_evidence", "clear_evidence", "finalize"])
    g.add_edge("clear_evidence", "finalize")
    g.add_conditional_edges("finalize", route_after_finalize, ["save", END])
    g.add_edge("save", END)
    return g.compile()


def review_meeting(meeting_id: str, edits: HitlEdits, *, llm: StructuredLLM, store: SummaryStore,
                   save: bool = True, use_cache: bool | None = None) -> MeetingSummary:
    """저장된 summary.json + 사용자 수정 목록 → 최종 JSON ({회의 ID}_summary_final.json, 원본은 그대로 둠)

    회의가 없으면 MeetingNotFoundError, 수정 목록이 잘못되면 hitl.HitlEditError (둘 다 LLM 호출 전)
    settings.REVIEW_TIMEOUT_SECONDS 안에 끝나지 않으면 TimeLimitExceeded (다시 요청하면 저장된 단계부터 진행)
    """
    if not _MEETING_ID.fullmatch(meeting_id):
        raise MeetingNotFoundError(meeting_id)
    content = store.read_summary(meeting_id)
    if content is None:
        raise MeetingNotFoundError(meeting_id)
    cfg.validate()
    summary = MeetingSummary.model_validate_json(content)
    hitl.validate_edits(summary, edits)
    if ui_test.is_test_summary(summary):    # 시험 모드 결과는 확정할 때도 LLM을 호출하지 않음
        llm = ui_test.UiTestLLM(summary)
    if save:    # 받은 수정 목록을 그대로 남김 (쓴 값만, {"edits": [...]})
        store.save_edits(meeting_id, edits.model_dump_json(indent=2, exclude_unset=True))
    runner = _StepRunner(llm, store, meeting_id, cfg.USE_LLM_CACHE if use_cache is None else use_cache,
                         max_calls=cfg.hitl_max_llm_calls(), feature="evidence")
    def work(deadline: float) -> dict:
        runner.deadline = deadline
        return _build_graph(edits, runner, store, save).invoke(
            {"summary": summary, "round": 0, "issues": [], "rounds": []},
            config={"run_name": f"summary_action_hitl_{meeting_id}", "recursion_limit": RECURSION_LIMIT,
                    "tags": [*cfg.LANGSMITH_TAGS, "hitl"]})

    state = run_with_limit(work, cfg.REVIEW_TIMEOUT_SECONDS)
    result: MeetingSummary = state["summary"]
    logger.info("[%s] HITL 완료: 수정 %d건 · API 호출 %d회", meeting_id, len(result.hitl.edits), runner.api_calls)
    return result


def save_assignments(meeting_id: str, changes: AssignChanges, *, store: SummaryStore) -> MeetingSummary:
    """담당자 추천 단계에서 바꾼 담당자 · 마감일 → 최종 JSON에 반영해 다시 저장 (LLM · 근거 연결 없음)

    최종 JSON이 없으면 MeetingNotFoundError, 변경 목록이 잘못되면 hitl.HitlEditError (저장 전)
    """
    if not _MEETING_ID.fullmatch(meeting_id):
        raise MeetingNotFoundError(meeting_id)
    content = store.read_final(meeting_id)
    if content is None:
        raise MeetingNotFoundError(meeting_id)
    summary = MeetingSummary.model_validate_json(content)
    hitl.validate_assignments(summary, changes)
    records = hitl.apply_assignments(summary, changes)
    if records:
        store.save_final(summary)
    logger.info("[%s] 담당자 추천 단계 수정 %d건 반영", meeting_id, len(records))
    return summary
