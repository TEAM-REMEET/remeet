# Python 백엔드

## 채워야 할 내용

FastAPI API와 분석 Worker가 공유하는 Python 패키지 및 pyproject.toml을 작성한다.

## 구현 원칙

Neo4j·GCS를 영속 저장소로 사용한다. API는 요청/조회, Worker는 장시간 분석을 담당한다.

## 완료 기준

API 연결과 무관하게 분석이 진행되고 실패/재시작 후 저장된 상태로 복원된다.

