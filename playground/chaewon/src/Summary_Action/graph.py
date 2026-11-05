"""LangGraph 파이프라인 — 생성 → 후처리 → (검증 ⇄ 재수정, 최대 config.MAX_VERIFY_ROUNDS회) → 저장

    START
      │
    load_input          전사문 txt → 메타데이터 · 화자 모드 · 발화 (구조 오류면 중단, LLM 호출 전)
      │
    generate ◀─┐        [LLM] 구조화 요약 + 액션 (prompt/Extract_summary_action_item.py)
      │        │        결과가 비면 다시 호출 (config.GENERATE_EMPTY_RETRIES회), 그래도 비면 오류로 중단
      ├─ 비었음 ┘
      │
    postprocess         근거 발화 검사 · ID 부여 · 목차 · 회의 결과 (postprocess.py)
      │
    verify  ◀──────────┐   [LLM] 문제 목록 (prompt/Verify_summary.py) — 생성 후 반드시 1회 이상 실행
      │                │
      ├─ 통과 ─────────┼──────────────────────────────────────▶ finalize ─▶ save ─▶ END
      ├─ 실패 · 회차 < MAX → revise  [LLM] 항목 단위 수정 (prompt/Revise_summary.py → revision.py)
      └─ 실패 · 회차 = MAX → mark_review ─────────────────────▶ finalize
                             남은 문제 항목에 REVIEW_REQUIRED + 사유, 회의록 상단 '검토 필요'

문제 등급 (config.IMPROVEMENT_ISSUE_TYPES)
    오류  회차와 관계없이 재수정 대상, 마지막 검증까지 남으면 '검토 필요'
    개선  1회차에만 재수정, 이후에는 검증 기록에만 남김 (통과를 막지 않음) — 위 그림의 '실패'는 처리할 문제가 있다는 뜻

반복 제한 (무한 루프 방지, 세 겹)
    1. route_after_verify: 검증 회차가 MAX_VERIFY_ROUNDS(1~3)에 도달하면 더 재수정하지 않음
    2. llm.call_structured: 회의 하나당 API 호출 상한 config.max_llm_calls() (기본 9 = 생성 1 + 생성 재호출 1 + 검증 3 + 재수정 2 + 여유 2)
    3. LangGraph recursion_limit: 노드 실행 횟수 상한 (RECURSION_LIMIT)

상태(SummaryState)는 노드가 돌려준 키만 갱신됨. summary(MeetingSummary)는 노드 안에서 고친 뒤 다시 돌려줌.
LangSmith: 기록이 켜져 있으면 그래프 실행 1회 = trace 1개 (노드 · LLM 호출이 하위 run)
"""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import config as cfg
from llm import call_structured
from postprocess import build_summary, load_input, restore_names
from prompt import Extract_summary_action_item as gen_prompt
from prompt import Revise_summary as revise_prompt
from prompt import Verify_summary as verify_prompt
from render import save_outputs
from revision import apply_revision, mark_review_required, to_issues
from schemas import (LLMMeetingSummary, LLMRevision, LLMVerification, MeetingSummary, VerificationIssue,
                     VerificationResult, VerificationRound)

RECURSION_LIMIT = 25        # 노드 실행 상한 (정상 최대: 3 + 생성 재호출 2 + 검증 3 + 재수정 2 + 2~3 = 13)


class SummaryState(TypedDict, total=False):
    input_path: str
    save: bool
    loaded: dict                        # load_input 결과 (meeting_id · metadata · speaker_mode · transcript …)
    llm_out: LLMMeetingSummary          # 생성 원본 (ID 없음)
    summary: MeetingSummary             # 후처리 · 재수정이 반영된 현재 요약
    generate_attempts: int              # 진행한 생성 호출 횟수
    round: int                          # 진행한 검증 횟수
    issues: list[VerificationIssue]     # 마지막 검증에서 처리할 문제 (재수정 · 검토 필요 대상, 기록만 남기는 개선 제외)
    saved: list[str]


def _utterances(loaded: dict) -> list[dict]:
    # LLM 입력용 이름(llm_name)은 여기서만 붙임 — 저장 데이터에는 없음
    return [{**u.model_dump(), "llm_name": loaded["llm_names"].get(u.speaker_user_id)} for u in loaded["transcript"]]


def _tag(loaded: dict, step: str) -> str:
    return f"{loaded['meeting_id']}_{cfg.CACHE_TAG}_{step}"


# ── 노드 ──────────────────────────────────────────────
def node_load_input(state: SummaryState) -> dict:
    loaded = load_input(state["input_path"])
    print(f"[입력] {loaded['meeting_id']}: 발화 {len(loaded['transcript'])}개 · 화자 모드 {loaded['speaker_mode']} "
          f"({loaded['transcript_format']}) ({Path(state['input_path']).name})")
    for w in loaded["warnings"]:
        print(f"   - {w}")
    return {"loaded": loaded, "round": 0, "issues": []}


def node_generate(state: SummaryState) -> dict:
    loaded = state["loaded"]
    attempts = state.get("generate_attempts", 0) + 1
    llm_out = call_structured(gen_prompt.build_system_prompt(loaded["speaker_mode"]),
                              gen_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"]),
                              LLMMeetingSummary, tag=_tag(loaded, "generate"),
                              cache_if=LLMMeetingSummary.has_content)     # 빈 응답은 캐시하지 않음 (재호출 · 재실행 시 새로 호출)
    if not llm_out.has_content():
        if attempts > cfg.GENERATE_EMPTY_RETRIES:
            raise RuntimeError(f"생성 결과가 비어 있음 ({attempts}회 호출) — 저장하지 않고 중단")
        print(f"[생성] 결과가 비어 있음 → 다시 호출 ({attempts}/{cfg.GENERATE_EMPTY_RETRIES})")
    return {"llm_out": llm_out, "generate_attempts": attempts}


def node_postprocess(state: SummaryState) -> dict:
    summary = build_summary(state["llm_out"], state["loaded"])
    summary.verification = VerificationResult(max_rounds=cfg.MAX_VERIFY_ROUNDS)
    _print_summary("생성", summary)
    return {"summary": summary}


def node_verify(state: SummaryState) -> dict:
    loaded, summary = state["loaded"], state["summary"]
    round_no = state["round"] + 1
    llm = call_structured(verify_prompt.build_system_prompt(loaded["speaker_mode"]),
                          verify_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"], summary),
                          LLMVerification, tag=_tag(loaded, f"verify{round_no}"),
                          effort=cfg.VERIFY_EFFORT)
    issues = to_issues(llm, summary, round_no)
    errors = [i for i in issues if i.issue_type not in cfg.IMPROVEMENT_ISSUE_TYPES]
    # 처리할 문제: 재수정할 수 있는 1회차는 전부, 그 외에는 오류만 (개선은 기록만 남김)
    targets = issues if round_no == 1 and round_no < cfg.MAX_VERIFY_ROUNDS else errors
    summary.verification.rounds.append(VerificationRound(round=round_no, passed=not targets, issues=issues))
    print(f"[검증 {round_no}/{cfg.MAX_VERIFY_ROUNDS}] " + ("통과" if not targets else "재수정 · 검토 대상 있음")
          + f" (오류 {len(errors)}건 · 개선 {len(issues) - len(errors)}건)")
    for i in issues:
        grade = "오류" if i in errors else "개선" if i in targets else "개선 · 기록만"
        print(f"   - {i.issue_id} [{grade}] {i.issue_type}: {', '.join(i.item_ids) or i.agenda_id or '회의 전체'}")
    return {"summary": summary, "round": round_no, "issues": targets}


def node_revise(state: SummaryState) -> dict:
    loaded, summary, round_no = state["loaded"], state["summary"], state["round"]
    llm = call_structured(revise_prompt.build_system_prompt(loaded["speaker_mode"]),
                          revise_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"], summary,
                                                          state["issues"]),
                          LLMRevision, tag=_tag(loaded, f"revise{round_no}"), effort=cfg.REVISE_EFFORT)
    changes = apply_revision(summary, llm, round_no)
    summary.verification.rounds[-1].revision = changes
    applied = [c for c in changes if c.applied]
    print(f"[재수정 {round_no}] 적용 {len(applied)}건 · 미적용 {len(changes) - len(applied)}건")
    for c in changes:
        print(f"   - {c.op} {c.target_id}" + (f" ({', '.join(c.changed_fields)})" if c.changed_fields else "")
              + ("" if c.applied else f" — 미적용: {c.note}"))
    return {"summary": summary}


def node_mark_review(state: SummaryState) -> dict:
    summary, issues = state["summary"], state["issues"]
    marked = mark_review_required(summary, issues)
    v = summary.verification
    v.status = "REVIEW_REQUIRED"
    v.remaining_issue_ids = [i.issue_id for i in issues]
    v.message = (f"검증 {state['round']}회 후에도 문제 {len(issues)}건이 해결되지 않음 "
                 f"(검토 필요 항목 {marked}개)")
    print(f"[검토 필요] {v.message}")
    return {"summary": summary}


def node_finalize(state: SummaryState) -> dict:
    summary = state["summary"]
    v = summary.verification
    if v.status != "REVIEW_REQUIRED":
        if summary.active_agendas():
            v.status, v.message = "PASSED", f"검증 {state['round']}회차 통과"
        else:       # 재수정으로 항목이 모두 삭제된 경우 — 검증을 통과해도 정상 결과로 저장하지 않음
            v.status, v.message = "REVIEW_REQUIRED", f"재수정 후 남은 항목이 없음 (검증 {state['round']}회)"
    restore_names(summary)      # 코드가 붙인 A · B → 표시 이름 (검증 · 재수정이 끝난 뒤, 저장 전)
    _print_summary("최종", summary)
    return {"summary": summary}


def node_save(state: SummaryState) -> dict:
    paths = save_outputs(state["summary"], state["llm_out"])
    for p in paths:
        print(f"[저장] {p}")
    return {"saved": [str(p) for p in paths]}


# ── 분기 ──────────────────────────────────────────────
def route_after_generate(state: SummaryState) -> str:
    return "postprocess" if state["llm_out"].has_content() else "generate"


def route_after_verify(state: SummaryState) -> str:
    if not state["issues"]:
        return "finalize"
    if state["round"] >= cfg.MAX_VERIFY_ROUNDS:
        return "mark_review"
    return "revise"


def route_after_finalize(state: SummaryState) -> str:
    return "save" if state.get("save", True) else END


def build_graph():
    g = StateGraph(SummaryState)
    g.add_node("load_input", node_load_input)
    g.add_node("generate", node_generate)
    g.add_node("postprocess", node_postprocess)
    g.add_node("verify", node_verify)
    g.add_node("revise", node_revise)
    g.add_node("mark_review", node_mark_review)
    g.add_node("finalize", node_finalize)
    g.add_node("save", node_save)

    g.add_edge(START, "load_input")
    g.add_edge("load_input", "generate")
    g.add_conditional_edges("generate", route_after_generate, ["postprocess", "generate"])
    g.add_edge("postprocess", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["finalize", "revise", "mark_review"])
    g.add_edge("revise", "verify")
    g.add_edge("mark_review", "finalize")
    g.add_conditional_edges("finalize", route_after_finalize, ["save", END])
    g.add_edge("save", END)
    return g.compile()


def _print_summary(label: str, summary: MeetingSummary) -> None:
    items = [it for a in summary.agendas for it in a.iter_items()]
    r = summary.meeting_results
    print(f"[{label}] 안건 {len(summary.active_agendas())}개 · 항목 {len(items)}개 "
          f"(결정 {len(r.decision_item_ids)} · 액션 {len(r.action_item_ids)} · 미해결 {len(r.unresolved_item_ids)} · "
          f"다음 회의 {len(r.next_topic_item_ids)}) · 검토 필요 {sum(it.status == 'REVIEW_REQUIRED' for it in items)}")


GRAPH = build_graph()
