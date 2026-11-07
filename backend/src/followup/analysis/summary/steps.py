"""요약 · 액션 아이템 추출의 LLM 단계 (생성 · 검증 · 재수정) 와 최종 상태 판정

각 함수는 프롬프트를 만들어 call(system, user, schema, step=…)로 호출하고 결과를 요약에 반영함.
호출 상한 · 응답 재사용은 call을 넘겨주는 쪽(application/summarize_meeting.py)이 담당.
"""

import hashlib
from collections.abc import Callable

from followup import settings as cfg
from followup.analysis.summary.postprocess import build_summary, restore_names
from followup.analysis.summary.prompts import extract_summary_action_item as generate_prompt
from followup.analysis.summary.prompts import revise_summary as revise_prompt
from followup.analysis.summary.prompts import verify_summary as verify_prompt
from followup.analysis.summary.revision import apply_revision, mark_review_required, to_issues
from followup.analysis.summary.schemas import (LLMMeetingSummary, LLMRevision, LLMVerification, MeetingSummary, RevisionChange,
                                       VerificationIssue, VerificationResult, VerificationRound)

StructuredCall = Callable[..., object]


class EmptySummaryError(RuntimeError):
    """재호출 후에도 생성 결과가 비어 있음"""


def prompt_version() -> str:
    # 프롬프트 내용으로 계산 (프롬프트를 고치면 자동으로 바뀜) — summary.json의 config에 기록
    prompts = [m.build_system_prompt(mode) for m in (generate_prompt, verify_prompt, revise_prompt)
               for mode in ("speaker", "none")]
    return hashlib.sha256("\n".join(prompts).encode("utf-8")).hexdigest()[:12]


def _utterances(loaded: dict) -> list[dict]:
    # LLM 입력용 이름(llm_name)은 여기서만 붙임 — 저장 데이터에는 없음
    return [{**u.model_dump(), "llm_name": loaded["llm_names"].get(u.speaker_user_id)} for u in loaded["transcript"]]


def generate(loaded: dict, call: StructuredCall) -> LLMMeetingSummary:
    return call(generate_prompt.build_system_prompt(loaded["speaker_mode"]),
                generate_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"]),
                LLMMeetingSummary, step="generate",
                cache_if=LLMMeetingSummary.has_content)     # 빈 응답은 저장하지 않음 (재호출 · 재실행 시 새로 호출)


def build(llm_out: LLMMeetingSummary, loaded: dict) -> MeetingSummary:
    summary = build_summary(llm_out, loaded)
    summary.project_id = loaded.get("project_id")
    summary.directory_id = loaded.get("directory_id")
    summary.host_user_id = loaded.get("host_user_id")
    summary.config["prompt_version"] = prompt_version()
    summary.verification = VerificationResult(max_rounds=cfg.MAX_VERIFY_ROUNDS)
    return summary


def verify(loaded: dict, summary: MeetingSummary, round_no: int, call: StructuredCall) -> list[VerificationIssue]:
    llm = call(verify_prompt.build_system_prompt(loaded["speaker_mode"]),
               verify_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"], summary),
               LLMVerification, step=f"verify{round_no}", effort=cfg.VERIFY_EFFORT)
    issues = to_issues(llm, summary, round_no)
    # 처리할 문제: 재수정할 수 있는 1회차는 전부, 그 외에는 오류만 (개선은 기록만 남김)
    errors = [i for i in issues if i.issue_type not in cfg.IMPROVEMENT_ISSUE_TYPES]
    targets = issues if round_no == 1 and round_no < cfg.MAX_VERIFY_ROUNDS else errors
    summary.verification.rounds.append(VerificationRound(round=round_no, passed=not targets, issues=issues))
    return targets


def revise(loaded: dict, summary: MeetingSummary, issues: list[VerificationIssue], round_no: int,
           call: StructuredCall) -> list[RevisionChange]:
    llm = call(revise_prompt.build_system_prompt(loaded["speaker_mode"]),
               revise_prompt.build_user_prompt(_utterances(loaded), loaded["llm_metadata"], summary, issues),
               LLMRevision, step=f"revise{round_no}", effort=cfg.REVISE_EFFORT)
    changes = apply_revision(summary, llm, round_no)
    summary.verification.rounds[-1].revision = changes
    return changes


def mark_review(summary: MeetingSummary, issues: list[VerificationIssue], round_no: int) -> None:
    marked = mark_review_required(summary, issues)
    v = summary.verification
    v.status = "REVIEW_REQUIRED"
    v.remaining_issue_ids = [i.issue_id for i in issues]
    v.message = f"검증 {round_no}회 후에도 문제 {len(issues)}건이 해결되지 않음 (검토 필요 항목 {marked}개)"


def finalize(summary: MeetingSummary, round_no: int) -> None:
    v = summary.verification
    if v.status != "REVIEW_REQUIRED":
        if summary.active_agendas():
            v.status, v.message = "PASSED", f"검증 {round_no}회차 통과"
        else:       # 재수정으로 항목이 모두 삭제된 경우 — 검증을 통과해도 정상 결과로 저장하지 않음
            v.status, v.message = "REVIEW_REQUIRED", f"재수정 후 남은 항목이 없음 (검증 {round_no}회)"
    restore_names(summary)      # 코드가 붙인 A · B → 표시 이름 (검증 · 재수정이 끝난 뒤, 저장 전)
