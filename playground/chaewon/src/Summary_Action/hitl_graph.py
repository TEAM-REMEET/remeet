"""LangGraph 파이프라인 — 사용자 수정(HITL) 반영: 수정 목록 → 근거 연결 ⇄ 검증 → 최종 JSON 저장

요약 · 액션 추출(graph.py)과 분리된 그래프. 사용자 검토가 언제 끝날지 모르므로 저장된 summary.json에서 다시 시작함

    START
      │
    load                저장된 summary.json · 사용자 수정 목록 읽기, 수정 목록 확인 (잘못되면 LLM 호출 전에 중단)
      │
    apply_edits         사용자 값 반영 — 생성 · 변경(액션명 · 일시 · 담당자) · 삭제
      │
      ├─ 대상 없음 (삭제만 · 수정 없음) ───────────────────────▶ finalize ─▶ save ─▶ END
      │
    link_evidence ◀─┐   [LLM] 근거 발화 번호만 연결 (prompt/Link_evidence.py)
      │             │
    verify_evidence │   [LLM] 근거가 적절한지 검증 (prompt/Verify_evidence.py)
      │             │
      ├─ 실패 · 회차 < MAX ─┘ 실패 항목만 다시 연결
      ├─ 통과 ─────────────────────────────────────────────────▶ finalize
      └─ 실패 · 회차 = MAX → clear_evidence ───────────────────▶ finalize
                             남은 문제 항목의 근거를 빈 배열로 (액션은 유지, REVIEW_REQUIRED로 표시하지 않음)

모델이 바꿀 수 있는 값은 근거 발화뿐: 출력 스키마에 다른 칸이 없고, 코드가 대상 항목의 근거 필드에만 반영함
반복 제한: HITL_MAX_VERIFY_ROUNDS(1~3) / API 호출 상한 config.hitl_max_llm_calls() / recursion_limit
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import config as cfg
from hitl import (apply_edits, apply_links, clear_unverified, finish_records, format_targets, llm_metadata, llm_utterances,
                  load_edits, now, save_final, to_evidence_issues, validate_edits)
from llm import call_structured
from prompt import Link_evidence as link_prompt
from prompt import Verify_evidence as verify_prompt
from render_md import load_summary
from schemas import (HitlEditRecord, HitlEdits, HitlEvidenceIssue, HitlEvidenceRound, HitlResult, LLMEvidenceLinks,
                     LLMEvidenceVerification, MeetingSummary)

RECURSION_LIMIT = 15    # 노드 실행 상한 (정상 최대: 2 + 연결 3 + 검증 3 + 3 = 11)


class HitlState(TypedDict, total=False):
    summary_path: str
    edits_path: str
    save: bool
    out_dir: str | None
    summary: MeetingSummary
    edits: HitlEdits
    records: list[HitlEditRecord]
    targets: list[str]                  # 이번 회차에 근거를 연결할 항목
    round: int                          # 진행한 근거 검증 횟수
    issues: list[HitlEvidenceIssue]     # 마지막 근거 검증의 문제
    rounds: list[HitlEvidenceRound]
    saved: list[str]


def _tag(summary: MeetingSummary, step: str) -> str:
    return f"{summary.meeting_id}_hitl_{step}"


def node_load(state: HitlState) -> dict:
    summary, edits = load_summary(state["summary_path"]), load_edits(state["edits_path"])
    validate_edits(summary, edits)
    print(f"[입력] {summary.meeting_id}: 사용자 수정 {len(edits.edits)}건")
    return {"summary": summary, "edits": edits, "round": 0, "issues": [], "rounds": []}


def node_apply_edits(state: HitlState) -> dict:
    summary = state["summary"]
    records, targets = apply_edits(summary, state["edits"])
    for r in records:
        print(f"[반영] {r.op} {r.item_id}" + (f" ({', '.join(r.changed_fields)})" if r.changed_fields else "")
              + (f" — {r.note}" if r.note else ""))
    print(f"[반영] 근거 연결 대상 {len(targets)}건")
    return {"summary": summary, "records": records, "targets": targets}


def node_link_evidence(state: HitlState) -> dict:
    summary, targets, round_no = state["summary"], state["targets"], state["round"] + 1
    mode = summary.source_document.speaker_mode
    llm = call_structured(link_prompt.build_system_prompt(mode),
                          link_prompt.build_user_prompt(llm_utterances(summary), llm_metadata(summary),
                                                        format_targets(summary, targets, "이전 근거"), state["issues"]),
                          LLMEvidenceLinks, tag=_tag(summary, f"link{round_no}"), effort=cfg.HITL_EFFORT)
    notes = apply_links(summary, llm, targets, round_no)
    print(f"[근거 연결 {round_no}] 대상 {len(targets)}건")
    for note in notes:
        print(f"   - {note}")
    return {"summary": summary}


def node_verify_evidence(state: HitlState) -> dict:
    summary, targets, round_no = state["summary"], state["targets"], state["round"] + 1
    mode = summary.source_document.speaker_mode
    llm = call_structured(verify_prompt.build_system_prompt(mode),
                          verify_prompt.build_user_prompt(llm_utterances(summary), llm_metadata(summary),
                                                          format_targets(summary, targets, "근거")),
                          LLMEvidenceVerification, tag=_tag(summary, f"verify{round_no}"), effort=cfg.HITL_EFFORT)
    issues = to_evidence_issues(llm, targets, round_no)
    print(f"[근거 검증 {round_no}/{cfg.HITL_MAX_VERIFY_ROUNDS}] " + ("통과" if not issues else f"문제 {len(issues)}건"))
    for i in issues:
        print(f"   - {i.issue_id} {i.item_id} {i.issue_type}: {i.description}")
    rounds = state["rounds"] + [HitlEvidenceRound(round=round_no, target_item_ids=list(targets), passed=not issues,
                                                  issues=issues)]
    failed = list(dict.fromkeys(i.item_id for i in issues))
    return {"round": round_no, "issues": issues, "rounds": rounds, "targets": failed or targets}


def node_clear_evidence(state: HitlState) -> dict:
    summary = state["summary"]
    clear_unverified(summary, state["issues"], state["round"])
    print(f"[근거 비움] {', '.join(state['targets'])} — 액션은 유지하고 근거만 빈 배열로 저장")
    return {"summary": summary}


def node_finalize(state: HitlState) -> dict:
    summary, records = state["summary"], state["records"]
    failed = {i.item_id for i in state["issues"]}
    finish_records(summary, records, failed)
    status = "COMPLETED" if records else "NO_CHANGES"
    summary.hitl = HitlResult(status=status, applied_at=now(), max_rounds=cfg.HITL_MAX_VERIFY_ROUNDS, edits=records,
                              rounds=state["rounds"])
    print(f"[정리] 수정 {len(records)}건 · 상태 {status}")
    return {"summary": summary}


def node_save(state: HitlState) -> dict:
    paths = save_final(state["summary"], state.get("out_dir"))
    for p in paths:
        print(f"[저장] {p}")
    return {"saved": [str(p) for p in paths]}


def route_after_apply(state: HitlState) -> str:
    return "link_evidence" if state["targets"] else "finalize"


def route_after_verify(state: HitlState) -> str:
    if not state["issues"]:
        return "finalize"
    return "clear_evidence" if state["round"] >= cfg.HITL_MAX_VERIFY_ROUNDS else "link_evidence"


def route_after_finalize(state: HitlState) -> str:
    return "save" if state.get("save", True) else END


def build_graph():
    g = StateGraph(HitlState)
    g.add_node("load", node_load)
    g.add_node("apply_edits", node_apply_edits)
    g.add_node("link_evidence", node_link_evidence)
    g.add_node("verify_evidence", node_verify_evidence)
    g.add_node("clear_evidence", node_clear_evidence)
    g.add_node("finalize", node_finalize)
    g.add_node("save", node_save)

    g.add_edge(START, "load")
    g.add_edge("load", "apply_edits")
    g.add_conditional_edges("apply_edits", route_after_apply, ["link_evidence", "finalize"])
    g.add_edge("link_evidence", "verify_evidence")
    g.add_conditional_edges("verify_evidence", route_after_verify, ["link_evidence", "clear_evidence", "finalize"])
    g.add_edge("clear_evidence", "finalize")
    g.add_conditional_edges("finalize", route_after_finalize, ["save", END])
    g.add_edge("save", END)
    return g.compile()


GRAPH = build_graph()
