"""달러 → 원 환율 (관리자 「비용 관리」 화면의 원화 표시용).

settings.EXCHANGE_RATE_URL(유럽중앙은행 기준 · 영업일마다 갱신 · 키 없음)에서 하루 한 번 받아 서버 메모리에 둠.
받지 못하면(인터넷 차단 · 서비스 오류) 관리자 설정의 기본 환율을 쓰고 fallback=True 로 알려 줌 → 화면에 「기본 환율 적용」.
비용 기록은 달러로만 저장하므로 환율은 보여 줄 때만 씀.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable
from urllib.request import Request, urlopen

from followup import settings as cfg

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict | None = None      # {"rate": 1358.73, "date": "2026-11-06", "fallback": False}
_fetched_at = 0.0
RETRY_AFTER_FAILURE = 600       # 실패하면 10분 뒤에 다시 시도 (화면을 열 때마다 기다리지 않게)


def _fetch() -> dict:
    request = Request(cfg.EXCHANGE_RATE_URL, headers={"User-Agent": "remeet-backend"})
    with urlopen(request, timeout=cfg.EXCHANGE_RATE_TIMEOUT) as response:
        data = json.load(response)
    rate = data["rates"]["KRW"]
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise ValueError("환율 값이 올바르지 않음")
    return {"rate": float(rate), "date": str(data.get("date") or ""), "fallback": False}


def usd_krw(fallback: Callable[[], float]) -> dict:
    """{"rate", "date", "fallback"} — fallback()은 받지 못했을 때만 부름 (관리자 설정 값)."""
    global _cache, _fetched_at
    with _lock:
        if _cache is not None and time.monotonic() - _fetched_at < cfg.EXCHANGE_RATE_TTL_SECONDS:
            return dict(_cache)
        try:
            _cache, _fetched_at = _fetch(), time.monotonic()
            return dict(_cache)
        except Exception as e:  # noqa: BLE001 — 환율을 못 받아도 화면은 기본 환율로 보여 줌
            logger.warning("환율을 받지 못해 기본 환율을 씀: %s", type(e).__name__)
            if _cache is not None and not _cache["fallback"]:    # 예전에 받은 값이 있으면 그 값을 계속 씀
                _fetched_at = time.monotonic() - cfg.EXCHANGE_RATE_TTL_SECONDS + RETRY_AFTER_FAILURE
                return dict(_cache)
            _cache = {"rate": fallback(), "date": "", "fallback": True}
            _fetched_at = time.monotonic() - cfg.EXCHANGE_RATE_TTL_SECONDS + RETRY_AFTER_FAILURE
            return dict(_cache)


def reset() -> None:
    # 테스트용
    global _cache, _fetched_at
    with _lock:
        _cache, _fetched_at = None, 0.0
