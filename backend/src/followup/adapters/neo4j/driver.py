"""Neo4j 연결. 드라이버는 프로세스당 하나만 만들어 재사용한다."""

from functools import lru_cache
from pathlib import Path

from neo4j import Driver, GraphDatabase

from followup import settings

SCHEMA_FILE = Path(__file__).with_name("schema.cypher")


@lru_cache(maxsize=1)
def get_driver() -> Driver:
    if not settings.NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_PASSWORD가 설정되지 않았습니다. remeet/.env를 확인하세요.")
    # UNRECOGNIZED 경고 끔: 아직 아무도 email이 없을 때처럼 "없는 속성" 경고가 조회마다 찍힘
    return GraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
                                notifications_disabled_classifications=["UNRECOGNIZED"],
                                # Aura는 오래 쉰 연결을 끊는다 → 30초 넘게 쉰 연결은 쓰기 전에 살아 있는지 확인 (끊긴 연결로 수십 초 재시도 방지)
                                liveness_check_timeout=30, max_connection_lifetime=300)


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
