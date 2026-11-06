# """앱 전역 설정값.

# 화면/로직 어디서나 쓰는 상수만 모아 둡니다. 색상 토큰은 ui/theme.py, 목업 데이터는 data/mock/ 에 있습니다.
# """

# from pathlib import Path

# APP_TITLE = "RE:MEET"

# # 브랜드 이미지 (assets/brand/)
# BRAND_DIR = Path(__file__).resolve().parent / "assets" / "icons"
# LOGO_SVG = BRAND_DIR / "logo.svg"  
# FAVICON_SVG = BRAND_DIR / "logo-header.svg"  
# APP_ICON = str(FAVICON_SVG)

# # 로그인한 사용자 (목업). 모든 권한·역할 계산의 기준이 됩니다.
# CURRENT_USER_ID = "yun"
# WORKSPACE_NAME = "데모컴퍼니"
# WORKSPACE_PLAN = "회의노트 · Business"

# DEMO_EMAIL = "demo@hoeui.kr"
# DEMO_PASSWORD = "demo1234"

# # 결과 전달(SSE) 시뮬레이션 타이밍 (초)
# DELIVERY_FIRST_EVENT_DELAY = 0.4
# DELIVERY_EVENT_INTERVAL = 0.65
# DELIVERY_POLL_INTERVAL = 0.5

# # 폴더 위키 그래프를 보여 주는 최소 회의록 수
# FOLDER_WIKI_MIN_NOTES = 3

# YEAR = 2026

# # 모든 페이지 공통 본문 최대 너비 (페이지마다 시작 위치를 맞추기 위함)
# PAGE_MAX_WIDTH = 1340


# # 새로고침해도 로그인이 유지되도록 쓰는 데모용 쿠키 이름
# LOGIN_COOKIE = "hoeui_demo_login"


"""앱 전역 설정값.

화면/로직 어디서나 쓰는 상수만 모아 둡니다. 색상 토큰은 ui/theme.py, 목업 데이터는 data/mock/ 에 있습니다.
"""

import json
import os
from pathlib import Path

APP_TITLE = "RE:MEET"

# 백엔드(FastAPI) 주소. 로컬은 기본값, Docker는 docker-compose에서 http://backend:8000 을 넣어요.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
# 회의 요약 응답을 기다리는 시간 (초). 요약은 몇 분 이상 걸릴 수 있어 nginx의 proxy_read_timeout과 같게 둬요.
MEETING_SUMMARY_TIMEOUT = 3600

# 브랜드 이미지 (assets/brand/)
BRAND_DIR = Path(__file__).resolve().parent / "assets" / "icons"
LOGO_SVG = BRAND_DIR / "logo.svg"  
FAVICON_SVG = BRAND_DIR / "logo-header.svg"  
APP_ICON = str(FAVICON_SVG)

WORKSPACE_NAME = "데모컴퍼니"
WORKSPACE_PLAN = "회의노트 · Business"


# 사용자에게 「관리자에게 문의」로 안내하는 관리자 이메일 (Neo4j 관리 계정)
ADMIN_EMAIL = "admin@remeet.example"

# 로그인 화면의 테스트용 「바로 시작」 버튼 (데모 = 바트 u1, 관리자 = admin).
# 비밀번호는 git에 안 올라가는 backend/seed/passwords.local.json 에서 읽어요.
# 이 파일은 Docker 이미지에 안 들어가서, 서버(EC2)에서는 버튼이 자동으로 숨겨져요.
_PASSWORDS = Path(__file__).resolve().parents[2] / "backend" / "seed" / "passwords.local.json"
DEMO_ACCOUNTS: dict = {}
if _PASSWORDS.exists():
    _pw = json.loads(_PASSWORDS.read_text(encoding="utf-8"))
    DEMO_ACCOUNTS = {k: _pw[k] for k in ("u1", "admin") if k in _pw}  # {"u1": {name, email, password}, ...}

# 결과 전달(SSE) 시뮬레이션 타이밍 (초)
DELIVERY_FIRST_EVENT_DELAY = 0.4
DELIVERY_EVENT_INTERVAL = 0.65
DELIVERY_POLL_INTERVAL = 0.5

# 폴더 위키 그래프를 보여 주는 최소 회의록 수
FOLDER_WIKI_MIN_NOTES = 3

YEAR = 2026

# 모든 페이지 공통 본문 최대 너비 (페이지마다 시작 위치를 맞추기 위함)
PAGE_MAX_WIDTH = 1340


# 새로고침해도 로그인이 유지되도록 쓰는 데모용 쿠키 이름
LOGIN_COOKIE = "remeet_token"  # 로그인 출입증(토큰)을 넣어 두는 쿠키 (7일)
