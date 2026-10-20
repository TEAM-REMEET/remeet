# REMEET
회의록 기반 Follow-up AI Agent 서비스       

## 처음 시작하기

git clone https://github.com/TEAM-REMEET/remeet.git       
cd remeet       

uv sync                              # 의존성 설치 (루트 pyproject.toml/uv.lock 하나로 통합. 각자 uv init 금지)       
uv run nbstripout --install          # 노트북 출력 자동 제거 (1회)       
cp .env.example .env                 # 환경변수 파일 만들기 → DB_* 등 값은 팀원에게 받아서 채우기       

## 채워야 할 내용

회의 등록 → 전처리·주제 청킹 → 요약·액션 병렬 추출 → 경로별 병합·검증 → 담당자 추천 → 관리자 검토·확정 → 저장·열람을 구현한다.       

## 구현 원칙

docs에서 범위와 설계를 읽고 specs 순서대로 구현한다. API와 분석 Worker는 backend의 같은 패키지를 사용한다. 외부 연동과 전송 승인은 후속 범위다.       

## 완료 기준

각 기능의 요구사항 ID, 코드, 테스트가 연결되고 확정 결과가 재실행으로 덮어써지지 않는다.       
