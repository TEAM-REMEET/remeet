"""이 폴더의 .env 를 읽고, 이 폴더를 import 경로에 넣습니다.

이 폴더(playground/<이름>/postgres)의 스크립트는 맨 위에서 `import paths` 를 먼저 합니다.
web 폴더(프론트 목업)에는 더 이상 기대지 않아요 — 데이터는 백엔드 · seed_people.py 가 넣습니다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """.env (KEY=VALUE 한 줄씩) → 환경 변수. 터미널에서 이미 설정한 값이 우선."""
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))