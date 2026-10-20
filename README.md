# remeet
회의록 기반 Follow-up AI Agent 서비스

## 처음 시작하기

git clone https://github.com/TEAM-REMEET/remeet.git
cd remeet

uv sync                              # 의존성 설치 (루트 pyproject.toml/uv.lock 하나로 통합. 각자 uv init 금지)
uv run nbstripout --install          # 노트북 출력 자동 제거 (1회)
cp .env.example .env                 # 환경변수 파일 만들기 → DB_* 등 값은 팀원에게 받아서 채우기