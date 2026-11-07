"""ULID — 만든 시각 순으로 정렬되는 26자 고유 ID (앞 10자: 밀리초 시각, 뒤 16자: 무작위)"""

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"      # Crockford Base32 (헷갈리는 I · L · O · U 제외)


def new_ulid(now_ms: int | None = None) -> str:
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    value = (timestamp << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(value >> (5 * i)) & 31] for i in range(25, -1, -1))
