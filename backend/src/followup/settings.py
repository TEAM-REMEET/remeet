"""환경변수를 읽는 유일한 곳.

로컬: remeet/.env 를 읽는다 (run_base_agent.py와 같은 위치). 이미 설정된 환경변수가 우선한다.
Docker: deploy/.env 가 env_file로 주입되므로 파일이 없어도 된다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False, encoding="utf-8-sig")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# 로그인 출입증(토큰) 서명 키. 길고 무작위인 값이어야 하고, 바뀌면 모든 사람이 다시 로그인해야 한다.
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_DAYS = 7
