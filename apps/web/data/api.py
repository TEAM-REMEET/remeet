"""백엔드(FastAPI) 호출. 화면 코드는 이 파일을 직접 쓰지 않고 data/repository.py 를 거쳐요."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import API_BASE_URL


class ApiError(Exception):
    """백엔드가 거절했거나(400 · 404 · 409 · 422) 연결이 안 됨. 메시지는 화면에 그대로 보여 줘도 돼요."""


def _detail(e: HTTPError) -> str:
    try:
        detail = json.load(e).get("detail")
    except ValueError:
        return f"서버 오류 ({e.code})"
    if isinstance(detail, list):  # 422: 입력 검증 실패 목록
        return " · ".join(f"{'.'.join(map(str, d.get('loc', [])[1:]))}: {d.get('msg')}" for d in detail)
    return str(detail)


def call(method: str, path: str, body: dict | None = None, token: str = ""):
    """token: 로그인 출입증 (관리자 API 등). 없으면 보내지 않아요."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} | ({"Authorization": f"Bearer {token}"} if token else {})
    req = Request(f"{API_BASE_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urlopen(req, timeout=15) as res:
            return json.load(res)
    except HTTPError as e:
        raise ApiError(_detail(e)) from e
    except URLError as e:
        raise ApiError(f"백엔드에 연결할 수 없어요 ({API_BASE_URL}). 백엔드 서버가 켜져 있는지 확인해 주세요.") from e
