"""Neo4j 연결. 드라이버는 프로세스당 하나만 만들어 재사용한다."""

import ssl
from functools import lru_cache
from pathlib import Path

import certifi
from neo4j import Driver, GraphDatabase

from followup import settings

SCHEMA_FILE = Path(__file__).with_name("schema.cypher")


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    if not settings.NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_PASSWORD가 설정되지 않았습니다. remeet/.env를 확인하세요.")
    uri, tls = _tls(settings.NEO4J_URI)
    # UNRECOGNIZED 경고 끔: 아직 아무도 email이 없을 때처럼 "없는 속성" 경고가 조회마다 찍힘
    return GraphDatabase.driver(uri, auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD), **tls,
                                notifications_disabled_classifications=["UNRECOGNIZED"],
                                # Aura는 오래 쉰 연결을 끊는다 → 30초 넘게 쉰 연결은 쓰기 전에 살아 있는지 확인 (끊긴 연결로 수십 초 재시도 방지)
                                liveness_check_timeout=30, max_connection_lifetime=300)


def _tls(uri: str) -> tuple[str, dict]:
    """neo4j+s:// 를 certifi 인증서로 직접 검증하도록 바꾼다.

    Windows에서 경로에 한글이 있으면 OpenSSL이 인증서 파일을 못 열어 Aura 접속이 실패한다.
    인증서를 파일 경로가 아니라 내용(cadata)으로 넘기면 OS·경로와 상관없이 동작한다.
    드라이버는 ssl_context를 +s 없는 스킴에서만 받으므로 스킴에서 +s를 뗀다 (암호화는 그대로).
    """
    scheme, sep, rest = uri.partition("://")
    if not scheme.endswith("+s"):
        return uri, {}  # bolt:// 로컬 등은 그대로
    ctx = ssl.create_default_context(cadata=certifi.contents())
    return f"{scheme[:-2]}{sep}{rest}", {"ssl_context": ctx}


def close_driver() -> None:
    if get_driver.cache_info().currsize:
        get_driver().close()
        get_driver.cache_clear()


def apply_schema() -> None:
    """유일성 제약을 만든다. IF NOT EXISTS라 여러 번 실행해도 된다."""
    for statement in SCHEMA_FILE.read_text(encoding="utf-8").split(";"):
        body = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("//")).strip()
        if body:
            get_driver().execute_query(body)
