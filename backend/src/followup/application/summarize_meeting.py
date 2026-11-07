"""요약 · 액션 아이템 추출 사용 사례 — 생성 → 후처리 → (검증 ⇄ 재수정, 최대 settings.MAX_VERIFY_ROUNDS회) → 저장

    START
      │
    generate ◀─┐        [LLM] 구조화 요약 + 액션 (analysis/summary/prompts/extract_summary_action_item.py)
      │        │        결과가 비면 다시 호출 (settings.GENERATE_EMPTY_RETRIES회), 그래도 비면 오류로 중단
      ├─ 비었음 ┘
      │
    postprocess         근거 발화 검사 · ID 부여 · 목차 · 회의 결과 (analysis/summary/postprocess.py)
      │
    verify  ◀──────────┐   [LLM] 문제 목록 — 생성 후 반드시 1회 이상 실행
      │                │
      ├─ 통과 ─────────┼──────────────────────────────────────▶ finalize ─▶ save ─▶ END
      ├─ 실패 · 회차 < MAX → revise  [LLM] 항목 단위 수정 (analysis/summary/revision.py)
      └─ 실패 · 회차 = MAX → mark_review ─────────────────────▶ finalize

전사문 구조 오류는 그래프 실행 전(load_transcript)에 중단하므로 LLM을 호출하지 않음.

문제 등급 (settings.IMPROVEMENT_ISSUE_TYPES)
    오류  회차와 관계없이 재수정 대상, 마지막 검증까지 남으면 '검토 필요'
    개선  1회차에만 재수정, 이후에는 검증 기록에만 남김 (통과를 막지 않음) — 위 그림의 '실패'는 처리할 문제가 있다는 뜻

반복 제한 (무한 루프 방지, 세 겹)
    1. route_after_verify: 검증 회차가 MAX_VERIFY_ROUNDS(1~3)에 도달하면 더 재수정하지 않음
    2. _StepRunner: 실행 1회당 API 호출 상한 settings.max_llm_calls() (기본 9)
    3. LangGraph recursion_limit: 노드 실행 횟수 상한 (RECURSION_LIMIT)

중간 실패 후 재실행
    단계별 LLM 응답을 SummaryStore에 저장함. 같은 입력으로 다시 실행하면 저장된 응답을 재사용해 실패한 단계부터 호출함.
    키에 모델 · 추론 강도 · 프롬프트 · 스키마가 포함되므로 값이 바뀌면 새로 호출함.
    summary.json 저장에 성공하면 단계별 응답을 삭제함 (실명 · 논의 내용을 오래 남기지 않음).
"""

import hashlib
import json
import logging
import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from followup import settings as cfg
from followup.analysis.summary import steps
from followup.analysis.summary.preprocessing import load_transcript
from followup.analysis.summary.schemas import LLMMeetingSummary, MeetingSummary, VerificationIssue
from followup.analysis.summary.transcript_builder import (MeetingSubmission, build_meeting_text, new_meeting_id,
                                                          new_project_id, submission_key)
from followup.application.ports import StructuredLLM, SummaryStore

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 25        # 노드 실행 상한 (정상 최대: 생성 2 + 후처리 1 + 검증 3 + 재수정 2 + 검토 표시 1 + 마무리 1 + 저장 1 = 11)
_MEETING_ID = re.compile(r"\w[\w.-]*")      # 회의 ID는 저장 경로에 쓰이므로 경로 구분자 · '.' 시작을 허용하지 않음
_MEETING_ID_STEP = "meeting_id"              # 요청 내용별로 회의 ID를 기억해 두는 단계 이름
_PROJECT_ID_STEP = "project_id"              # 요청 내용별로 새 프로젝트 ID를 기억해 두는 단계 이름


class LLMCallLimitError(RuntimeError):
    """실행 1회의 API 호출 상한 초과"""


class SummaryState(TypedDict, total=False):
    loaded: dict                        # load_transcript 결과
    llm_out: LLMMeetingSummary          # 생성 원본 (ID 없음)
    summary: MeetingSummary             # 후처리 · 재수정이 반영된 현재 요약
    generate_attempts: int              # 진행한 생성 호출 횟수
    round: int                          # 진행한 검증 횟수
    issues: list[VerificationIssue]     # 마지막 검증에서 처리할 문제 (재수정 · 검토 필요 대상, 기록만 남기는 개선 제외)


class _StepRunner:
    """실행 1회 전용 LLM 호출기 — 호출 상한과 단계별 응답 재사용을 담당 (동시 실행 간에 값을 공유하지 않음)"""

    def __init__(self, llm: StructuredLLM, store: SummaryStore, meeting_id: str, use_cache: bool):
        self.llm, self.store, self.meeting_id, self.use_cache = llm, store, meeting_id, use_cache
        self.api_calls = 0

    def _key(self, system: str, user: str, schema: type[BaseModel], step: str, effort: str | None) -> str:
        payload = json.dumps([cfg.model_name(), effort, system, user, schema.__name__, schema.model_json_schema()],
                             ensure_ascii=False, sort_keys=True)
        return f"{step}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"

    def _read(self, key: str, schema: type[BaseModel], cache_if) -> BaseModel | None:
        content = self.store.read_step(self.meeting_id, key) if self.use_cache else None
        if content is None:
            return None
        try:
            cached = schema.model_validate_json(content)
        except ValidationError:     # 손상된 응답은 없는 것으로 처리
            return None
        return cached if cache_if is None or cache_if(cached) else None

    def __call__(self, system: str, user: str, schema: type[BaseModel], *, step: str, effort: str | None = None,
                 cache_if=None) -> BaseModel:
        # cache_if: 이 조건을 만족하는 응답만 저장 · 재사용
        effort = effort or cfg.REASONING_EFFORT
        key = self._key(system, user, schema, step, effort)
        cached = self._read(key, schema, cache_if)
        if cached is not None:
            logger.info("[%s] %s: 저장된 응답 사용", self.meeting_id, step)
            return cached
        if self.api_calls >= cfg.max_llm_calls():
            raise LLMCallLimitError(f"LLM 호출 상한 초과 — 실행 1회당 최대 {cfg.max_llm_calls()}회 "
                                    f"({cfg.llm_call_plan_text()}), 호출하지 않고 중단")
        self.api_calls += 1     # 실패해도 센 횟수로 둠
        logger.info("[%s] %s: LLM 호출 (추론 강도 %s)", self.meeting_id, step, effort)
        parsed = self.llm.parse(system, user, schema, effort)
        if self.use_cache and (cache_if is None or cache_if(parsed)):
            self.store.write_step(self.meeting_id, key, parsed.model_dump_json(indent=1))
        return parsed


def _build_graph(runner: _StepRunner, store: SummaryStore, save: bool):
    def node_generate(state: SummaryState) -> dict:
        attempts = state.get("generate_attempts", 0) + 1
        llm_out = steps.generate(state["loaded"], runner)
        if not llm_out.has_content():
            if attempts > cfg.GENERATE_EMPTY_RETRIES:
                raise steps.EmptySummaryError(f"생성 결과가 비어 있음 ({attempts}회 호출) — 저장하지 않고 중단")
            logger.warning("[%s] 생성 결과가 비어 있음 → 다시 호출 (%d/%d)", runner.meeting_id, attempts,
                           cfg.GENERATE_EMPTY_RETRIES)
        return {"llm_out": llm_out, "generate_attempts": attempts}

    def node_postprocess(state: SummaryState) -> dict:
        summary = steps.build(state["llm_out"], state["loaded"])
        summary.config["use_llm_cache"] = runner.use_cache      # 이 실행에서 실제로 쓴 값
        return {"summary": summary}

    def node_verify(state: SummaryState) -> dict:
        round_no = state["round"] + 1
        issues = steps.verify(state["loaded"], state["summary"], round_no, runner)
        found = len(state["summary"].verification.rounds[-1].issues)
        logger.info("[%s] 검증 %d/%d: %s (발견 %d건)", runner.meeting_id, round_no, cfg.MAX_VERIFY_ROUNDS,
                    "통과" if not issues else f"처리 대상 {len(issues)}건", found)
        return {"summary": state["summary"], "round": round_no, "issues": issues}

    def node_revise(state: SummaryState) -> dict:
        changes = steps.revise(state["loaded"], state["summary"], state["issues"], state["round"], runner)
        applied = sum(c.applied for c in changes)
        logger.info("[%s] 재수정 %d: 적용 %d건 · 미적용 %d건", runner.meeting_id, state["round"], applied,
                    len(changes) - applied)
        return {"summary": state["summary"]}

    def node_mark_review(state: SummaryState) -> dict:
        steps.mark_review(state["summary"], state["issues"], state["round"])
        return {"summary": state["summary"]}

    def node_finalize(state: SummaryState) -> dict:
        steps.finalize(state["summary"], state["round"])
        return {"summary": state["summary"]}

    def node_save(state: SummaryState) -> dict:
        location = store.save_summary(state["summary"])
        store.clear_steps(runner.meeting_id)
        logger.info("[%s] 저장: %s", runner.meeting_id, location)
        return {}

    def route_after_generate(state: SummaryState) -> str:
        return "postprocess" if state["llm_out"].has_content() else "generate"

    def route_after_verify(state: SummaryState) -> str:
        if not state["issues"]:
            return "finalize"
        return "mark_review" if state["round"] >= cfg.MAX_VERIFY_ROUNDS else "revise"

    g = StateGraph(SummaryState)
    for name, node in (("generate", node_generate), ("postprocess", node_postprocess), ("verify", node_verify),
                       ("revise", node_revise), ("mark_review", node_mark_review), ("finalize", node_finalize),
                       ("save", node_save)):
        g.add_node(name, node)
    g.add_edge(START, "generate")
    g.add_conditional_edges("generate", route_after_generate, ["postprocess", "generate"])
    g.add_edge("postprocess", "verify")
    g.add_conditional_edges("verify", route_after_verify, ["finalize", "revise", "mark_review"])
    g.add_edge("revise", "verify")
    g.add_edge("mark_review", "finalize")
    g.add_conditional_edges("finalize", lambda _: "save" if save else END, ["save", END])
    g.add_edge("save", END)
    return g.compile()


def summarize_meeting(meeting_id: str, transcript_text: str, *, llm: StructuredLLM, store: SummaryStore,
                      source_name: str | None = None, save: bool = True, use_cache: bool | None = None,
                      project_id: str | None = None, directory_id: str | None = None,
                      host_user_id: str | None = None) -> MeetingSummary:
    """전사문 텍스트 → MeetingSummary (save=True면 summary.json 저장)

    source_name: summary.json에 남길 입력 이름 (파일 이름 등) / use_cache: None이면 settings.USE_LLM_CACHE
    입력 형식이 틀리면 TranscriptFormatError, 재실행은 같은 인자로 다시 호출하면 실패한 단계부터 진행됨
    """
    if not _MEETING_ID.fullmatch(meeting_id):
        raise ValueError(f"회의 ID 형식 오류: {meeting_id!r}")
    cfg.validate()
    model = cfg.model_name()
    loaded = {**load_transcript(meeting_id, transcript_text, source_name), "project_id": project_id,
              "directory_id": directory_id, "host_user_id": host_user_id}
    logger.info("[%s] 입력: 발화 %d개 · 화자 모드 %s (%s) · 경고 %d건", meeting_id, len(loaded["transcript"]),
                loaded["speaker_mode"], loaded["transcript_format"], len(loaded["warnings"]))
    runner = _StepRunner(llm, store, meeting_id, cfg.USE_LLM_CACHE if use_cache is None else use_cache)
    state = _build_graph(runner, store, save).invoke(
        {"loaded": loaded, "round": 0, "issues": []},
        config={"run_name": f"summary_action_{meeting_id}", "recursion_limit": RECURSION_LIMIT,
                "tags": list(cfg.LANGSMITH_TAGS),
                "metadata": {"model": model, "reasoning_effort": cfg.REASONING_EFFORT,
                             "max_verify_rounds": cfg.MAX_VERIFY_ROUNDS}})
    summary: MeetingSummary = state["summary"]
    logger.info("[%s] 완료: 검증 %s · API 호출 %d회", meeting_id, summary.verification.status, runner.api_calls)
    return summary


def _remembered_id(store: SummaryStore, key: str, step: str, make, remember: bool) -> str:
    # 재시도에도 같은 ID를 쓰도록 요청 내용별로 저장해 둔 값을 찾고, 없으면 새로 만듦
    value = store.read_step(key, step) if remember else None
    if value is None:
        value = make()
        if remember:
            store.write_step(key, step, value)
    return value


def summarize_submission(submission: MeetingSubmission, *, llm: StructuredLLM, store: SummaryStore,
                         save: bool = True, use_cache: bool | None = None) -> MeetingSummary:
    """POST /api/meetings 입력 → MeetingSummary

    project_id가 없으면 새 폴더로 보고 P_ULID를 만듦.
    실패 후 같은 요청을 다시 보내면 같은 ID로 실패한 단계부터 진행됨 (검증 · 재수정 프롬프트에 항목 ID가 들어가므로
    ID가 바뀌면 저장된 응답을 재사용할 수 없음). 완료된 뒤 다시 보내면 새 회의로 처리됨.
    """
    text = build_meeting_text(submission)       # 입력 형식이 틀리면 ID를 만들기 전에 중단
    resumable = save and (cfg.USE_LLM_CACHE if use_cache is None else use_cache)
    key = submission_key(submission)
    meeting_id = _remembered_id(store, key, _MEETING_ID_STEP, new_meeting_id, resumable)
    project_id = submission.project_id or _remembered_id(store, key, _PROJECT_ID_STEP, new_project_id, resumable)
    summary = summarize_meeting(meeting_id, text, llm=llm, store=store, save=save, use_cache=use_cache,
                                project_id=project_id, directory_id=submission.directory_id,
                                host_user_id=submission.host_user_id)
    if resumable:
        store.clear_steps(key)
    return summary
