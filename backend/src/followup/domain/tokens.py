"""로그인 출입증(토큰). 로그인에 성공하면 발급하고, 이후 요청마다 받아서 누구인지 확인한다.

형식: base64(내용 JSON).base64(HMAC-SHA256 서명). 서명 키(settings.AUTH_SECRET)를 모르면 내용을 바꿀 수 없다.
내용은 암호화하지 않으므로 비밀번호 같은 비밀값은 넣지 않는다 (user_id · 권한 · 만료 시각만).
"""

import base64
import hashlib
import hmac
import json
import time


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def make_token(claims: dict, secret: str, ttl_seconds: int, now: float | None = None) -> str:
    if not secret:
        raise RuntimeError("AUTH_SECRET이 설정되지 않았습니다. remeet/.env를 확인하세요.")
    body = _b64(json.dumps({**claims, "exp": int((now or time.time()) + ttl_seconds)}).encode())
    return f"{body}.{_sign(body, secret)}"


def read_token(token: str, secret: str, now: float | None = None) -> dict | None:
    """서명이 맞고 만료 전이면 내용, 아니면 None."""
    try:
        body, sig = token.split(".")
        if not secret or not hmac.compare_digest(sig, _sign(body, secret)):
            return None
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    return claims if claims.get("exp", 0) > (now or time.time()) else None
