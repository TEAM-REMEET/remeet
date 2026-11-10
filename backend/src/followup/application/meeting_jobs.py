"""요약 · 액션 추출 백그라운드 실행 — 사람마다 작업 하나 (실행 중이면 새 등록을 막음)

    start_job    입력 확인 후 바로 돌아옴 → 뒤에서 summarize_submission 실행 → 끝나면 done · failed로 기록
                 (done 이고 등록한 사람이 주관자면 「요약이 끝났어요」 알림)
    current_job  지금 작업 (입력값 포함, 완료면 결과 포함) — 화면을 벗어났거나 브라우저를 닫았어도 이어서 볼 수 있음
    clear_job    끝난 작업 지우기 (실행 중이면 지우지 않음)

작업 기록은 저장소에 두어 입력값을 잃지 않음. 실행 중 표시는 프로세스 안(_active)에도 두어,
서버가 다시 시작되어 끊긴 작업을 찾아냄 (같은 내용으로 다시 요청하면 실패한 단계부터 이어서 진행).
uvicorn 프로세스 하나로 실행하는 지금 구성을 전제로 함.
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from followup import settings as cfg
from followup.analysis.summary import ui_test
from followup.analysis.summary.preprocessing import TranscriptFormatError
from followup.analysis.summary.transcript_builder import MeetingSubmission, build_meeting_text
from followup.application.ports import StructuredLLM, SummaryStore
from followup.application.summarize_meeting import LLMInputLimitError, summarize_submission
from followup.application.time_limit import TimeLimitExceeded, run_with_limit

logger = logging.getLogger(__name__)

FAILED_MESSAGE = "요약 실행에 실패했어요. 같은 내용으로 다시 시도하면 이어서 진행해요."
TIMEOUT_MESSAGE = "분석이 제한 시간(30분) 안에 끝나지 않았어요. 같은 내용으로 다시 시도하면 이어서 진행해요."
INPUT_LIMIT_MESSAGE = "회의 내용이 길어 분석할 수 있는 입력 길이를 넘었어요. 회의를 나눠서 다시 올려 주세요."
INTERRUPTED_MESSAGE = "서버가 다시 시작되어 분석이 중단됐어요. 같은 내용으로 다시 시도하면 이어서 진행해요."

_lock = threading.Lock()
_active: set[str] = set()
_executor = ThreadPoolExecutor(max_workers=cfg.MAX_SUMMARY_JOBS, thread_name_prefix="summary-job")


class JobRunningError(RuntimeError):
    """이 사람의 작업이 이미 실행 중"""


def _notify_summary_done(user_id: str, submission: MeetingSubmission) -> None:
    """요약이 끝나 분석 및 검토 화면으로 넘어가는 시점 → 내가 작성하고 내가 주관하는 회의면 나에게 알림.

    주관자가 다른 사람이면 화면이 분석 결과를 바로 넘기고 주관자에게 review 알림이 가요 (compose.handoff_meeting).
    회의는 화면이 결과를 받은 뒤에야 draft 로 저장되므로 meeting_id 없이 보냄 (누르면 작성 화면 · web open_notification).
    알림을 못 보내도 분석 결과는 그대로 쓸 수 있어야 해서 실패는 로그만 남김.
    """
    if submission.host_user_id != user_id:
        return
    try:
        from followup.adapters.postgres import notifications

        notifications.create_notification(actor_id=user_id, user_id=user_id, kind="created",
                                          text=f"「{submission.title}」 회의록 요약이 끝났어요. 분석 결과를 검토해 주세요.")
    except Exception:
        logger.exception("요약 완료 알림 실패 (%s)", user_id)


def _run(user_id: str, job: dict, submission: MeetingSubmission, llm: StructuredLLM, store: SummaryStore) -> None:
    try:
        if ui_test.is_test_transcript(submission.transcript):   # 시험 모드: LLM 없이 저장된 결과 사용
            summary = ui_test.run_summary(submission, store)
        else:
            summary = run_with_limit(lambda deadline: summarize_submission(submission, llm=llm, store=store, deadline=deadline),
                                     cfg.SUMMARY_TIMEOUT_SECONDS)
        job = {**job, "status": "done", "meeting_id": summary.meeting_id}
    except TimeLimitExceeded:
        logger.warning("요약 작업 시간 초과 (%s)", user_id)
        job = {**job, "status": "failed", "message": TIMEOUT_MESSAGE}
    except LLMInputLimitError as e:     # 다시 시도해도 같은 결과라 '이어서 진행' 안내를 하지 않음
        logger.warning("요약 작업 입력 상한 초과 (%s): %s", user_id, e)
        job = {**job, "status": "failed", "message": INPUT_LIMIT_MESSAGE}
    except Exception:
        logger.exception("요약 작업 실패 (%s)", user_id)      # 원인은 서버 로그에만 남김
        job = {**job, "status": "failed", "message": FAILED_MESSAGE}
    with _lock:
        store.write_job(user_id, json.dumps(job, ensure_ascii=False))
        _active.discard(user_id)
    if job["status"] == "done":     # 결과를 기록한 뒤에 알림 (누르면 바로 결과를 볼 수 있게)
        _notify_summary_done(user_id, submission)


def start_job(user_id: str, submission: MeetingSubmission, draft: dict, *, llm: StructuredLLM, store: SummaryStore) -> dict:
    """입력이 틀리면 TranscriptFormatError, 이미 실행 중이면 JobRunningError (둘 다 LLM 호출 없음)"""
    if ui_test.is_test_transcript(submission.transcript):
        if not cfg.ui_test_enabled():    # 시험용 입력이 실제 AI 호출로 이어지지 않도록 거절
            raise TranscriptFormatError("입력 오류", ["시험 모드가 꺼져 있어요 (UI_TEST_MODE). 회의 내용을 적어 주세요."])
    else:
        build_meeting_text(submission)
    job = {"status": "running", "meeting_id": None, "message": None, "request": submission.model_dump(mode="json", exclude_none=True),
           "draft": draft, "started_at": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        if user_id in _active:
            raise JobRunningError(user_id)
        _active.add(user_id)
        store.write_job(user_id, json.dumps(job, ensure_ascii=False))
    _executor.submit(_run, user_id, job, submission, llm, store)
    return job


def current_job(user_id: str, store: SummaryStore) -> dict | None:
    with _lock:
        content = store.read_job(user_id)
        if content is None:
            return None
        job = json.loads(content)
        if job["status"] == "running" and user_id not in _active:   # 서버가 다시 시작되어 끊김
            job = {**job, "status": "failed", "message": INTERRUPTED_MESSAGE}
            store.write_job(user_id, json.dumps(job, ensure_ascii=False))
    if job["status"] == "done":
        saved = store.read_summary(job["meeting_id"])
        if saved is None:
            return {**job, "status": "failed", "message": FAILED_MESSAGE}
        job["result"] = json.loads(saved)
    return job


def clear_job(user_id: str, store: SummaryStore) -> None:
    with _lock:
        if user_id in _active:
            raise JobRunningError(user_id)
        store.clear_job(user_id)
