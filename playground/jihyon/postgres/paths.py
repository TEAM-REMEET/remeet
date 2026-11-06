"""web 폴더를 찾아서 파이썬이 import 할 수 있게 하고, 이 폴더의 .env 를 읽습니다.

이 폴더(playground/<이름>/postgres)의 모든 스크립트는 맨 위에서 `import paths` 를 먼저 합니다.
그러면 web 폴더의 코드(config, data.models, data.repository …)를 고치지 않고 그대로 가져다 쓸 수 있어요.

web 폴더 위치
    1) .env 의 WEB_DIR 이 있으면 그 경로
    2) 없으면 저장소 맨 위의 web 폴더  (이 폴더 → ../../../web)
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

WEB_DIR = Path(os.environ.get("WEB_DIR") or HERE.parents[2] / "web").resolve()
if not (WEB_DIR / "app.py").exists():
    sys.exit(f"web 폴더를 찾지 못했어요: {WEB_DIR}\n→ .env 에 WEB_DIR=web폴더경로 를 적어 주세요.")

for p in (str(HERE), str(WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
