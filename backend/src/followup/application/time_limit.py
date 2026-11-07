"""처리 전체 시간 제한 (settings.SUMMARY_TIMEOUT_SECONDS · REVIEW_TIMEOUT_SECONDS)

run_with_limit      제한 시간이 지나면 기다리기를 멈추고 TimeLimitExceeded
_StepRunner         같은 기한이 지나면 새 LLM 호출을 하지 않음 → 이미 보낸 호출 1건만 끝나고 처리가 멈춤
                    (실행 중인 API 호출은 강제로 끊지 않음, 호출 1건 제한은 settings.LLM_TIMEOUT)
"""

import threading
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class TimeLimitExceeded(TimeoutError):
    """정해진 시간 안에 처리가 끝나지 않음"""


def run_with_limit(work: Callable[[float], T], seconds: float) -> T:
    # work(기한)을 별도 스레드에서 실행 — 기한은 time.monotonic() 기준
    deadline = time.monotonic() + seconds
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = work(deadline)
        except BaseException as e:      # 호출한 쪽에서 다시 발생시킴
            box["error"] = e

    thread = threading.Thread(target=target, daemon=True, name="time-limited-work")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeLimitExceeded(f"{seconds:g}초 안에 끝나지 않음")
    if "error" in box:
        raise box["error"]
    return box["value"]
